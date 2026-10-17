"""The CI-gated auto-deploy, health check and rollback scripts, run for real against a throwaway git
remote with fake `curl` (GitHub API), `sudo`/`docker` shims. No network, no Docker needed."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = ["auto-update.sh", "healthcheck.sh", "rollback.sh", "resume-auto-update.sh"]

pytestmark = pytest.mark.skipif(not shutil.which("git") or not shutil.which("flock"), reason="needs git and flock")


def sh(cmd, cwd, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, shell=True, text=True, capture_output=True, check=check)


@pytest.fixture()
def world(tmp_path):
    origin, work, bin_, state = tmp_path / "origin.git", tmp_path / "work", tmp_path / "bin", tmp_path / "state"
    bin_.mkdir()
    sh(f"git init -q --bare -b main {origin}", tmp_path)
    sh(f"git clone -q {origin} {work}", tmp_path)
    (work / "scripts").mkdir()
    for s in SCRIPTS:
        shutil.copy(REPO / "scripts" / s, work / "scripts" / s)
    (work / "v.txt").write_text("1")
    g = "git -c user.email=t@t -c user.name=t"
    sh(f"{g} add -A && {g} commit -qm one && git push -q origin main", work)
    sh("git remote set-url origin https://github.com/owner/repo.git", work)
    sh(f"git config url.{origin}.insteadOf https://github.com/owner/repo.git", work)
    # fakes: curl answers from $FAKE_CI (success|failed|pending|missing|down); docker health from $FAKE_HEALTH
    (bin_ / "curl").write_text("""#!/usr/bin/env bash
case "$FAKE_CI" in
  down) exit 22;;
  missing) echo '{"check_runs": []}';;
  pending) echo '{"check_runs": [{"status": "in_progress", "conclusion": null}]}';;
  failed) echo '{"check_runs": [{"status": "completed", "conclusion": "failure"}]}';;
  *) echo '{"check_runs": [{"status": "completed", "conclusion": "success"}]}';;
esac
""")
    (bin_ / "sudo").write_text('#!/usr/bin/env bash\n[ "$1" = "-n" ] && shift\nexec "$@"\n')
    (bin_ / "docker").write_text("""#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
if [ "$2" = "exec" ]; then [ "$(cat "$FAKE_HEALTH_FILE")" = ok ]; exit; fi
if [ "$2" = "ps" ]; then echo running; exit 0; fi
exit 0
""")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "PMINTEL_STATE": str(state),
           "HEALTH_TIMEOUT": "0", "HEALTH_INTERVAL": "0", "FAKE_LOG": str(tmp_path / "docker.log"),
           "FAKE_HEALTH_FILE": str(tmp_path / "health"), "FAKE_CI": "success"}
    (tmp_path / "health").write_text("ok")

    def push_commit(text):
        other = tmp_path / "other"
        if not other.exists():
            sh(f"git clone -q {origin} {other}", tmp_path)
        sh("git pull -q", other)
        (other / "v.txt").write_text(text)
        sh(f"{g} commit -qam {text} && git push -q origin main", other)
        return sh("git rev-parse HEAD", other).stdout.strip()

    return {"work": work, "env": env, "state": state, "push": push_commit, "tmp": tmp_path}


def head(w):
    return sh("git rev-parse HEAD", w["work"]).stdout.strip()


def deploy_log(w):
    p = w["state"] / "deploys.log"
    return p.read_text() if p.exists() else ""


def run_update(w, **env):
    return sh("scripts/auto-update.sh", w["work"], {**w["env"], **env}, check=False)


def test_deploys_only_after_ci_success(world):
    before = head(world)
    new = world["push"]("2")
    for ci in ("pending", "missing", "failed", "down"):
        assert run_update(world, FAKE_CI=ci).returncode == 0
        assert head(world) == before, ci
    assert "CI failed" in deploy_log(world) and "unreachable" in deploy_log(world)
    assert run_update(world, FAKE_CI="success").returncode == 0
    assert head(world) == new and "DEPLOYED" in deploy_log(world)
    assert (world["state"] / "previous").read_text().strip() == before


def test_failed_health_rolls_back_and_pauses(world):
    before = head(world)
    world["push"]("broken")
    (world["tmp"] / "health").write_text("bad")
    assert run_update(world).returncode == 1
    assert head(world) == before  # back on the previous commit
    assert (world["state"] / "paused").exists() and "ROLLBACK" in deploy_log(world)
    (world["tmp"] / "health").write_text("ok")
    assert run_update(world).returncode == 0 and head(world) == before  # paused: no redeploy
    sh("scripts/resume-auto-update.sh", world["work"], world["env"])
    assert not (world["state"] / "paused").exists()


def test_manual_rollback(world):
    first = head(world)
    world["push"]("2")
    run_update(world)
    assert head(world) != first
    r = sh("scripts/rollback.sh", world["work"], world["env"], check=False)
    assert r.returncode == 0 and head(world) == first
    assert "MANUAL-ROLLBACK" in deploy_log(world) and (world["state"] / "paused").exists()
    assert sh("scripts/rollback.sh deadbeef", world["work"], world["env"], check=False).returncode == 2
