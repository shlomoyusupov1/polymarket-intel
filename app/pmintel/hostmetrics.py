"""Host resource sampling (CPU, load, RAM, disk, DB size) into host_metrics, every collector cycle.

Inside a Docker container /proc/stat, /proc/loadavg and /proc/meminfo describe the host (no lxcfs),
and the root filesystem usage is the host disk backing Docker — so these are host numbers.
"""
from __future__ import annotations

import os
import shutil

_prev: tuple[int, int] | None = None  # (busy, total) jiffies from the previous sample


def _cpu_times(stat_text: str) -> tuple[int, int]:
    parts = [int(x) for x in stat_text.splitlines()[0].split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
    total = sum(parts[:8])
    return total - idle, total


def parse_meminfo(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.strip():
            out[k] = int(v.split()[0]) * 1024
    return out


def sample(proc: str = "/proc", disk_path: str = "/") -> dict:
    global _prev
    read = lambda name: open(os.path.join(proc, name)).read()  # noqa: E731
    busy, total = _cpu_times(read("stat"))
    cpu_pct = None
    if _prev and total > _prev[1]:
        cpu_pct = 100.0 * (busy - _prev[0]) / (total - _prev[1])
    _prev = (busy, total)
    load = [float(x) for x in read("loadavg").split()[:3]]
    mem = parse_meminfo(read("meminfo"))
    du = shutil.disk_usage(disk_path)
    return {"cpu_pct": cpu_pct, "load1": load[0], "load5": load[1], "load15": load[2],
            "cpus": os.cpu_count(), "mem_total": mem.get("MemTotal"), "mem_available": mem.get("MemAvailable"),
            "disk_total": du.total, "disk_used": du.used}


def record(conn) -> dict:
    s = sample()
    db_bytes = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
    conn.execute("""INSERT INTO host_metrics (cpu_pct, load1, load5, load15, cpus, mem_total, mem_available,
                    disk_total, disk_used, db_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (s["cpu_pct"], s["load1"], s["load5"], s["load15"], s["cpus"], s["mem_total"],
                  s["mem_available"], s["disk_total"], s["disk_used"], db_bytes))
    return {**s, "db_bytes": db_bytes}
