"""observe, macOS reader: lsof, vmmap, fs_usage, log show (and psutil). Read-only; returns None on another system."""
import os
import shutil
import subprocess
import sys
import time

PLATFORM = "darwin"
NEEDS_ADMIN = "needs root (fs_usage reads the kernel trace): run it with sudo; nothing was asked or changed"


def _here():
    return sys.platform == "darwin"


def _run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _lsof(args):
    """lsof -F output as [{p, c, n...}] per process; the file/socket names under key 'n' (list)."""
    if not shutil.which("lsof"):
        return None
    rows, cur = [], None
    for line in _run(["lsof", "-nP", "-F", "pcfn"] + args).stdout.splitlines():
        if not line:
            continue
        k, v = line[0], line[1:]
        if k == "p":
            cur = {"pid": int(v), "name": "", "names": []}
            rows.append(cur)
        elif cur is not None and k == "c":
            cur["name"] = v
        elif cur is not None and k == "n":
            cur["names"].append(v)
    return rows


def open_file(path):
    if not _here():
        return None
    rows = _lsof(["--", os.path.realpath(path)])
    if rows is None:
        return {"error": "lsof is not installed", "rows": []}
    return [{"pid": r["pid"], "name": r["name"], "how": ["open"]} for r in rows]


def port(n):
    if not _here():
        return None
    rows = _lsof([f"-iTCP:{int(n)}", f"-iUDP:{int(n)}"])
    if rows is None:
        return {"error": "lsof is not installed", "rows": []}
    return [{"proto": "inet", "local": nm, "remote": "", "state": "", "pid": r["pid"], "name": r["name"]}
            for r in rows for nm in r["names"] or [""]]


def proc(pid):
    if not _here():
        return None
    import psutil
    pid = int(pid)
    try:
        p = psutil.Process(pid)
        cwd = p.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"pid": pid, "error": str(e)}
    vm = _run(["vmmap", "-summary", str(pid)]).stdout.splitlines() if shutil.which("vmmap") else []
    return {"pid": pid, "name": p.name(), "exe": p.exe(), "cwd": cwd, "user": p.username(), "cmdline": p.cmdline(),
            "children": [{"pid": c.pid, "name": c.name()} for c in p.children()], "map_regions": len(vm),
            "map_files": [l.strip() for l in vm[:40]]}


def watch(directory, seconds):
    """FSEvents needs a native binding Fieldkit does not ship: a snapshot comparison once a second instead."""
    if not _here():
        return None

    def snap():
        out = {}
        for root, _, files in os.walk(directory):
            for f in files:
                p = os.path.join(root, f)
                try:
                    out[p] = os.stat(p).st_mtime_ns
                except OSError:
                    pass
        return out
    prev, events, end = snap(), [], time.time() + float(seconds)
    while time.time() < end:
        time.sleep(1)
        cur = snap()
        events += [{"t": time.time(), "event": "created", "path": p} for p in cur.keys() - prev.keys()]
        events += [{"t": time.time(), "event": "deleted", "path": p} for p in prev.keys() - cur.keys()]
        events += [{"t": time.time(), "event": "modified", "path": p} for p in cur if p in prev and cur[p] != prev[p]]
        prev = cur
    return {"events": events, "method": "polling once a second"}


def trace(pid, seconds):
    if not _here():
        return None
    if os.geteuid() != 0:
        return {"error": NEEDS_ADMIN, "needs_admin": True, "events": []}
    p = subprocess.Popen(["fs_usage", "-w", "-f", "filesys", str(int(pid))], stdout=subprocess.PIPE, text=True)
    time.sleep(float(seconds))
    p.terminate()
    return {"events": [l[:300] for l in p.communicate(timeout=10)[0].splitlines() if "open" in l or "exec" in l]}


def usage(pid, seconds):
    if not _here():
        return None
    import psutil
    try:
        p = psutil.Process(int(pid))
        rows, io0 = [], p.io_counters() if hasattr(p, "io_counters") else None
        p.cpu_percent(None)
        for _ in range(max(1, int(float(seconds)))):
            time.sleep(1)
            rows.append({"cpu_pct": p.cpu_percent(None), "rss_kb": p.memory_info().rss // 1024})
        return {"samples": rows}
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"error": str(e), "samples": []}


def events(words):
    if not _here():
        return None
    pred = " AND ".join(f'eventMessage CONTAINS[c] "{w.replace(chr(34), "")}"' for w in words) or "TRUEPREDICATE"
    r = _run(["log", "show", "--last", "1h", "--predicate", pred, "--style", "compact"], timeout=120)
    return {"lines": r.stdout.splitlines()[-200:]}
