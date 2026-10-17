"""Off-site backup + restore test scripts, run for real with fake Object Storage (curl) and fake
docker; openssl and sha256sum are real, so encryption and integrity checks are exercised."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")


@pytest.fixture()
def world(tmp_path):
    work, bin_, store, state = tmp_path / "work", tmp_path / "bin", tmp_path / "store", tmp_path / "state"
    for d in (work / "scripts", bin_, store):
        d.mkdir(parents=True)
    for s in ("offsite-backup.sh", "offsite-restore-test.sh"):
        shutil.copy(REPO / "scripts" / s, work / "scripts" / s)
    (work / ".env").write_text("OFFSITE_PAR_URL=https://objectstorage.example/p/TOKEN/n/ns/b/bk/o/\nBACKUP_KEY=k3y\n")
    (bin_ / "curl").write_text(f"""#!/usr/bin/env bash
[ -n "$FAKE_STORAGE_DOWN" ] && exit 22
url="${{@: -1}}"; name="${{url##*/}}"
for ((i=1;i<=$#;i++)); do
  case "${{!i}}" in
    -T) j=$((i+1)); cp "${{!j}}" "{store}/$name"; exit 0;;
    -o) j=$((i+1)); [ -f "{store}/$name" ] || exit 22; cp "{store}/$name" "${{!j}}"; exit 0;;
  esac
done
exit 2
""")
    (bin_ / "sudo").write_text('#!/usr/bin/env bash\n[ "$1" = "-n" ] && shift\nexec "$@"\n')
    (bin_ / "docker").write_text(f"""#!/usr/bin/env bash
args="$*"
case "$args" in
  *pg_dump*) printf 'PGDMP-fake-dump-%s' "$FAKE_DUMP_ID";;
  *pg_restore*) cat > "{tmp_path}/restored.bin";;
  *"-d restore_offsite"*"count(*) FROM markets"*) echo "$FAKE_RESTORED_MARKETS";;
  *"-d pmintel"*"count(*) FROM markets"*) echo 1000;;
  *count*) echo 5;;
esac
exit 0
""")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "PMINTEL_STATE": str(state),
           "FAKE_DUMP_ID": "1", "FAKE_RESTORED_MARKETS": "990"}
    run = lambda script, **e: subprocess.run(f"scripts/{script}", cwd=work, env={**env, **e}, shell=True,  # noqa: E731
                                             text=True, capture_output=True)
    return {"run": run, "store": store, "state": state, "tmp": tmp_path}


def test_backup_is_encrypted_uploaded_and_restorable(world):
    r = world["run"]("offsite-backup.sh")
    assert r.returncode == 0, r.stderr
    objs = list(world["store"].iterdir())
    assert len(objs) == 1 and objs[0].name.endswith(".dump.enc")
    assert b"PGDMP-fake-dump" not in objs[0].read_bytes()  # encrypted at rest
    r = world["run"]("offsite-restore-test.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (world["tmp"] / "restored.bin").read_bytes() == b"PGDMP-fake-dump-1"  # decrypted bytes restored
    assert "RESTORE-OK" in (world["state"] / "offsite.log").read_text()


def test_restore_detects_tampering_and_too_few_rows(world):
    assert world["run"]("offsite-backup.sh").returncode == 0
    obj = next(world["store"].iterdir())
    data = bytearray(obj.read_bytes()); data[-1] ^= 1; obj.write_bytes(bytes(data))
    r = world["run"]("offsite-restore-test.sh")
    assert r.returncode == 1 and "sha256 mismatch" in r.stdout
    assert world["run"]("offsite-backup.sh", FAKE_DUMP_ID="2").returncode == 0
    r = world["run"]("offsite-restore-test.sh", FAKE_RESTORED_MARKETS="10")
    assert r.returncode == 1 and "rows too low" in r.stdout


def test_missing_config_and_failed_upload(world):
    assert world["run"]("offsite-backup.sh", FAKE_STORAGE_DOWN="1").returncode == 1
    assert "UPLOAD-FAILED" in (world["state"] / "offsite.log").read_text()
    env_file = world["tmp"] / "work" / ".env"
    env_file.write_text("BACKUP_KEY=k\n")
    assert world["run"]("offsite-backup.sh").returncode == 2
