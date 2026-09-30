"""snapshot - record the state of things, then prove what changed.

Harvested from an agent-evaluation snapshot tool ("record the state of everything
the agent must not touch, and compare two records"), a Windows tweaks framework's
state reports, and uninstall tests (check nothing was left behind).

After an agent acts, the harness diffs two snapshots and reports exactly what
changed - the model's own account is never needed.

    fieldkit snapshot take NAME [--path DIR ...] [--services] [--tasks] [--programs] [--processes]
    fieldkit snapshot diff A B
    fieldkit snapshot list

What a snapshot holds (each part optional):
  files      path -> size, mtime, sha256 (hash only for files <= 64 MB)
  services   Windows services (name -> status, start type) / systemd units
  tasks      Windows scheduled tasks (path -> state) / systemd timers
  programs   installed programs (Windows uninstall registry / dpkg)
  processes  running program names with counts (not PIDs: they change every run)

Snapshots are JSON under state/snapshots/. Read-only: taking one changes nothing.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import settings
from .host import host

SNAP_DIR = settings.ROOT / "state" / "snapshots"
HASH_LIMIT = 64 * 1024 * 1024
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".venv", "venv"}


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def files(roots):
    out = {}
    for root in roots:
        root = Path(root)
        if root.is_file():
            items = [root]
        else:
            items = []
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
                items += [Path(dirpath) / f for f in filenames]
        for p in items:
            try:
                st = p.stat()
                out[str(p)] = {"size": st.st_size, "mtime": int(st.st_mtime),
                               "sha256": _sha(p) if st.st_size <= HASH_LIMIT else None}
            except OSError:
                continue
    return out


def _ps_json(command):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"{command} | ConvertTo-Json -Compress -Depth 3"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else [data]


def services():
    if host()["is_windows"]:
        rows = _ps_json("Get-CimInstance Win32_Service | Select-Object Name,State,StartMode")
        return {r["Name"]: {"state": r.get("State"), "start": r.get("StartMode")} for r in rows if r.get("Name")}
    r = subprocess.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"],
                       capture_output=True, text=True)
    out = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            out[parts[0]] = {"state": parts[3], "start": parts[2]}
    return out


def tasks():
    if host()["is_windows"]:
        rows = _ps_json("Get-ScheduledTask | Select-Object TaskPath,TaskName,@{n='State';e={[string]$_.State}}")
        return {f"{r.get('TaskPath', '')}{r.get('TaskName', '')}": {"state": r.get("State")} for r in rows}
    r = subprocess.run(["systemctl", "list-timers", "--all", "--no-legend", "--plain"], capture_output=True, text=True)
    return {line.split()[-2]: {"state": "timer"} for line in r.stdout.splitlines() if len(line.split()) >= 2}


def programs():
    if host()["is_windows"]:
        rows = _ps_json("Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue"
                        " | Where-Object DisplayName | Select-Object DisplayName,DisplayVersion")
        return {r["DisplayName"]: {"version": r.get("DisplayVersion")} for r in rows if r.get("DisplayName")}
    r = subprocess.run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"], capture_output=True, text=True)
    return {a: {"version": b} for a, _, b in (l.partition("\t") for l in r.stdout.splitlines()) if a}


def processes():
    if host()["is_windows"]:
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, errors="replace")
        names = [line.split('","')[0].strip('"') for line in r.stdout.splitlines() if line]
    else:
        r = subprocess.run(["ps", "-eo", "comm="], capture_output=True, text=True)
        names = [n.strip() for n in r.stdout.splitlines() if n.strip()]
    counts = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return {n: {"count": c} for n, c in counts.items()}


PARTS = {"services": services, "tasks": tasks, "programs": programs, "processes": processes}


def take(name, paths=(), parts=()):
    snap = {"name": name, "taken": time.strftime("%Y-%m-%d %H:%M:%S"), "host": host()["system"],
            "paths": [str(Path(p).resolve()) for p in paths]}
    if paths:
        snap["files"] = files(paths)
    for p in parts:
        snap[p] = PARTS[p]()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAP_DIR / f"{name}.json"
    out.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def load(name):
    p = Path(name)
    if not p.is_file():
        p = SNAP_DIR / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def diff(a, b):
    """-> {part: {"added": [...], "removed": [...], "changed": {key: {"before", "after"}}}} for parts in both."""
    a = a if isinstance(a, dict) else load(a)
    b = b if isinstance(b, dict) else load(b)
    out = {}
    for part in ("files",) + tuple(PARTS):
        if part not in a or part not in b:
            continue
        x, y = a[part], b[part]
        changed = {}
        for k in x.keys() & y.keys():
            before, after = x[k], y[k]
            if part == "files":        # content, not timestamps, decides
                if before.get("sha256") and after.get("sha256"):
                    same = before["sha256"] == after["sha256"]
                else:
                    same = before["size"] == after["size"] and before["mtime"] == after["mtime"]
                if not same:
                    changed[k] = {"before": before, "after": after}
            elif before != after:
                changed[k] = {"before": before, "after": after}
        added, removed = sorted(y.keys() - x.keys()), sorted(x.keys() - y.keys())
        if added or removed or changed:
            out[part] = {"added": added, "removed": removed, "changed": changed}
    return out


def summary_lines(d, limit=15):
    lines = []
    for part, ch in d.items():
        lines.append(f"{part}: +{len(ch['added'])} added, -{len(ch['removed'])} removed, ~{len(ch['changed'])} changed")
        for k in ch["added"][:limit]:
            lines.append(f"   + {k}")
        for k in ch["removed"][:limit]:
            lines.append(f"   - {k}")
        for k in list(ch["changed"])[:limit]:
            lines.append(f"   ~ {k}")
    return lines or ["no changes"]
