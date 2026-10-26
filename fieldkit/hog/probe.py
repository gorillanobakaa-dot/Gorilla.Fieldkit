"""The hog's measurements: RAM, commit charge, processes, loaded local models, disks, CPU temperature. Read-only.

Local only: the one network call is Ollama's /api/ps on a loopback address (any other host is refused), and it is
made only when an Ollama process is running. `lms` is run only when an LM Studio process is running, so the watch
never starts LM Studio's service by itself.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import psutil

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
LMS_PROC = ("lm studio", "lms", "llmster", "llama-server")


def ct(x):
    """Creation times compared to 2 decimals everywhere (the registry, the live table): a float round trip through
    JSON must not make the same process look like a different one."""
    return round(float(x), 2)


def ram():
    v = psutil.virtual_memory()
    return {"total": v.total, "used": v.total - v.available}


def commit(platform=None):
    platform = platform or sys.platform
    if platform == "win32":
        class PI(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("CommitTotal", ctypes.c_size_t), ("CommitLimit", ctypes.c_size_t),
                        ("CommitPeak", ctypes.c_size_t), ("PhysicalTotal", ctypes.c_size_t),
                        ("PhysicalAvailable", ctypes.c_size_t), ("SystemCache", ctypes.c_size_t),
                        ("KernelTotal", ctypes.c_size_t), ("KernelPaged", ctypes.c_size_t),
                        ("KernelNonpaged", ctypes.c_size_t), ("PageSize", ctypes.c_size_t),
                        ("HandleCount", ctypes.c_ulong), ("ProcessCount", ctypes.c_ulong),
                        ("ThreadCount", ctypes.c_ulong)]
        pi = PI()
        pi.cb = ctypes.sizeof(PI)
        if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(pi), pi.cb):
            return None
        return {"used": pi.CommitTotal * pi.PageSize, "limit": pi.CommitLimit * pi.PageSize}
    if platform.startswith("linux"):
        return parse_meminfo(Path("/proc/meminfo").read_text())
    return None     # macOS has no commit limit to run into; swap is in psutil.swap_memory()


def parse_meminfo(text):
    kv = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        if v.strip().endswith("kB"):
            kv[k.strip()] = int(v.split()[0]) * 1024
    if "Committed_AS" not in kv or "CommitLimit" not in kv:
        return None
    return {"used": kv["Committed_AS"], "limit": kv["CommitLimit"]}


def processes(top=40, keep=()):
    """-> (procs, alive): the `top` biggest by working set plus every (pid, ctime) in `keep`, and the whole table's
    (pid, ctime) pairs (the orphan rule checks registry entries against it)."""
    rows, alive = [], []
    keep = set(keep)
    for p in psutil.process_iter(["name", "memory_info", "create_time", "cpu_times"]):
        i = p.info
        if not i.get("create_time"):
            continue
        c = ct(i["create_time"])
        alive.append([p.pid, c])
        mi, cpu = i.get("memory_info"), i.get("cpu_times")
        rows.append({"pid": p.pid, "ctime": c, "name": i.get("name") or "?", "rss": mi.rss if mi else 0,
                     "cpu_s": round(cpu.user + cpu.system, 2) if cpu else None})
    rows.sort(key=lambda r: -r["rss"])
    procs = rows[:top] + [r for r in rows[top:] if (r["pid"], r["ctime"]) in keep]
    return procs, alive, rows


def running(rows, needles):
    return any(any(n in r["name"].lower() for n in needles) for r in rows)


def lms_path(cfg=None):
    cand = [(cfg or {}).get("lms"), shutil.which("lms")]
    for base in (Path.home() / ".cache" / "lm-studio" / "bin", Path.home() / ".lmstudio" / "bin"):
        cand += [str(base / "lms.exe"), str(base / "lms")]
    return next((c for c in cand if c and Path(c).is_file()), None)


def parse_lms_ps(text):
    """`lms ps --json` -> [{id, key, size, status}]. Field names differ between lms versions; take what is there."""
    data = json.loads(text or "[]")
    if isinstance(data, dict):
        data = data.get("models") or data.get("loaded") or []
    out = []
    for m in data:
        ident = m.get("identifier") or m.get("instanceReference") or m.get("modelKey") or m.get("path")
        if not ident:
            continue
        used = m.get("lastUsedTime")         # ms since the epoch (lms 0.3.x, measured 2026-10-26)
        out.append({"id": ident, "key": m.get("modelKey") or m.get("path"), "size": m.get("sizeBytes") or 0,
                    "status": str(m.get("status") or m.get("state") or "").lower(),
                    "last_used": used / 1000.0 if isinstance(used, (int, float)) and used > 0 else None})
    return out


def lms_activity(lines):
    """-> {last_any, by_model}: the newest server-log line that is real work, overall and per model tag. `lms`'s own
    questions (Client=lms-cli, the LMSAuthenticator lines this watch causes) are not work."""
    from .. import lmstudio
    last_any, by = None, {}
    for raw in lines:
        m = lmstudio.HEAD.match(raw.rstrip("\r\n"))
        if not m or "LMSAuthenticator" in raw or "Client=lms-cli" in raw:
            continue
        t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        last_any = max(last_any or 0, t)
        if m.group(3):
            by[m.group(3)] = max(by.get(m.group(3), 0), t)
    return {"last_any": last_any, "by_model": by}


def lms(rows, cfg, run=subprocess.run, logs=None):
    if not running(rows, LMS_PROC):
        return {"ok": True, "models": [], "why": "LM Studio not running"}
    exe = lms_path(cfg)
    if not exe:
        return {"ok": False, "models": [], "why": "LM Studio runs but no lms CLI was found"}
    try:
        r = run([exe, "ps", "--json"], capture_output=True, text=True, timeout=20)
        models = parse_lms_ps(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": False, "models": [], "why": f"lms ps failed: {e}"}
    if not models:
        return {"ok": True, "models": [], "why": "no model loaded"}
    from .. import lmstudio
    paths = logs if logs is not None else _recent_logs(lmstudio)
    lines = []
    for p in paths:
        try:
            lines += Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            pass
    return {"ok": True, "exe": exe, "models": models, "activity": lms_activity(lines),
            "logs": [str(p) for p in paths]}


def _recent_logs(lmstudio, days=2):
    root = Path.home() / ".cache" / "lm-studio" / "server-logs"
    out = []
    for d in range(days):
        out += lmstudio.default_logs(time.strftime("%Y-%m-%d", time.localtime(time.time() - d * 86400)), root)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def loopback(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if host not in LOOPBACK:
        raise ValueError(f"refused: {url} is not a loopback address (the hog talks to this machine only)")
    return url.rstrip("/")


def ollama(rows, cfg, opener=urllib.request.urlopen):
    if not running(rows, ("ollama",)):
        return {"ok": True, "models": [], "why": "Ollama not running"}
    try:
        with opener(loopback(cfg["ollama_url"]) + "/api/ps", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "models": [], "why": f"Ollama /api/ps: {e}"}
    return {"ok": True, "models": [{"name": m.get("name") or m.get("model"), "size": m.get("size") or 0,
                                    "expires_at": m.get("expires_at")} for m in data.get("models") or []]}


def disks(platform=None):
    platform = platform or sys.platform
    out, seen = [], set()
    if platform == "win32":
        paths = [p.mountpoint for p in psutil.disk_partitions(all=False) if "fixed" in (p.opts or "") or not p.opts]
    else:
        paths = ["/", str(Path.home())]
    for path in paths:
        try:
            u = shutil.disk_usage(path)
        except OSError:
            continue
        key = (u.total, u.free)
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": path, "total": u.total, "free": u.free})
    return out


def temp():
    try:
        from ..thermal import sensors
        name, fn, _why = sensors.best(prove=False)
        c = fn() if fn else None
        return {"c": float(c) if c is not None else None, "sensor": name}
    except Exception as e:      # a missing sensor is a fact to report, never a crash of the watch
        return {"c": None, "sensor": None, "why": str(e)[:120]}


def gather(cfg, keep=(), with_temp=True, now=None):
    procs, alive, rows = processes(cfg["sample_top"], keep)
    return {"t": now or time.time(), "host": sys.platform, "ram": ram(), "commit": commit(), "procs": procs,
            "alive": alive, "lms": lms(rows, cfg), "ollama": ollama(rows, cfg), "disks": disks(),
            "temp": temp() if with_temp else {"c": None, "sensor": None}}
