# Version: 1.0.0 · updated 26-11-23-12-00
"""CI runner fingerprint: ask the machine what it has before a shard loads it (owner, 2026-11-23: a huge test load must
not crash the GitHub runner).

    fieldkit ci runner [--json] [--check]     CPUs, RAM, free disk, and the pytest-xdist workers that fit

Workers = min(CPUs, (RAM - reserve) / mb_per_worker), at least 1. `.github/ci.yaml` `runner:` holds the numbers.
`--check` exits 3 when the runner has less RAM than `min_ram_mb` (a shard on it would be killed, not slow).
Stdlib only; reads /proc/meminfo on Linux and GlobalMemoryStatusEx on Windows; macOS reports RAM as unknown (None),
and then only the CPU count limits the workers.
"""
import argparse
import json
import os
import shutil
import sys

DEFAULTS = {"mb_per_worker": 700, "reserve_mb": 1500, "min_ram_mb": 3000, "max_workers": 8}


def ram_mb():
    """-> total RAM in MB, or None when this system cannot say."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) // 1024
        elif sys.platform.startswith("win"):
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("ptotal", ctypes.c_ulonglong), ("pavail", ctypes.c_ulonglong),
                            ("vtotal", ctypes.c_ulonglong), ("vavail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

            st = Status()
            st.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.total // (1024 * 1024))
    except (OSError, ValueError, AttributeError):
        pass
    return None


def fingerprint(path="."):
    free = None
    try:
        free = shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        pass
    return {"os": "windows" if sys.platform.startswith("win") else "linux" if sys.platform.startswith("linux") else sys.platform,
            "cpus": os.cpu_count() or 1, "ram_mb": ram_mb(), "disk_free_mb": free}


def workers(fp, cfg=None):
    """-> pytest-xdist workers that fit this runner: CPU-bound and RAM-bound, at least 1."""
    c = {**DEFAULTS, **(cfg or {})}
    n = min(int(fp["cpus"]), int(c["max_workers"]))
    if fp.get("ram_mb") is not None:
        n = min(n, (int(fp["ram_mb"]) - int(c["reserve_mb"])) // int(c["mb_per_worker"]))
    return max(n, 1)


def problems(fp, cfg=None):
    c = {**DEFAULTS, **(cfg or {})}
    if fp.get("ram_mb") is not None and fp["ram_mb"] < int(c["min_ram_mb"]):
        return [f"runner has {fp['ram_mb']} MB RAM, under the {c['min_ram_mb']} MB a shard needs"]
    return []


def describe(fp, n):
    ram = "unknown" if fp["ram_mb"] is None else f"{fp['ram_mb']} MB"
    return f"ci-runner: {fp['os']} cpus={fp['cpus']} ram={ram} disk_free={fp['disk_free_mb']} MB -> xdist workers={n}"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ci runner", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--config", default=".github/ci.yaml")
    a = ap.parse_args(argv)
    from fieldkit.method import ciplan

    cfg = ciplan.load_config(a.config).get("runner")
    fp = fingerprint()
    n = workers(fp, cfg)
    bad = problems(fp, cfg)
    if a.json:
        print(json.dumps({**fp, "workers": n, "problems": bad}))
    else:
        print(describe(fp, n))
    for line in bad:
        print("ci-runner: PROBLEM:", line)
    return 3 if (a.check and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
