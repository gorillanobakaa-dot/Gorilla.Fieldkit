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

Names are file names, so only letters, digits, '.', '_' and '-' are accepted
(no folders, no '..'). A part whose query fails, times out or answers with
something unreadable is stored as "not captured" with the reason, and diff
refuses to compare that part instead of reporting everything as removed.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import settings
from .host import host

SNAP_DIR = settings.ROOT / "state" / "snapshots"
HASH_LIMIT = 64 * 1024 * 1024
NAME_RX = re.compile(r"^[A-Za-z0-9._-]+$")
NOT_CAPTURED = "_not_captured"          # a part dict holding only this key: the query failed
QUERY_TIMEOUT = 180
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


class NotCaptured(Exception):
    """A system query failed, timed out or answered with something unreadable."""


def check_name(name):
    """A snapshot name becomes a file name: letters, digits, '.', '_', '-' only, and not just dots."""
    if not isinstance(name, str) or not NAME_RX.fullmatch(name) or set(name) == {"."}:
        raise ValueError(f"snapshot name {name!r} refused: use only letters, digits, '.', '_' and '-'")
    return name


def _run(cmd, timeout=QUERY_TIMEOUT):
    """Run a read-only query. -> stdout. Raises NotCaptured when it cannot be trusted."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NotCaptured(f"{cmd[0]} timed out after {timeout} s") from None
    except OSError as e:
        raise NotCaptured(f"{cmd[0]} could not start: {e}") from None
    if r.returncode != 0:
        raise NotCaptured(f"{cmd[0]} exit {r.returncode}: {(r.stderr or '').strip()[:200]}")
    return r.stdout or ""


def _ps_json(command):
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"{command} | ConvertTo-Json -Compress -Depth 3"])
    if not out.strip():
        return []                                   # PowerShell prints nothing for an empty result
    try:
        data = json.loads(out)
    except ValueError:
        raise NotCaptured("PowerShell answer is not readable JSON") from None
    rows = data if isinstance(data, list) else [data]
    if not all(isinstance(r, dict) for r in rows):
        raise NotCaptured("PowerShell answer has an unexpected shape")
    return rows


def _systemd_services():
    """-> {unit: {"state": active state, "sub": sub state, "start": enabled state}} from systemctl."""
    units = {}
    for line in _run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"]).splitlines():
        parts = line.replace("\u25cf", " ").split(None, 4)      # UNIT LOAD ACTIVE SUB DESCRIPTION
        if len(parts) >= 4:
            units[parts[0]] = {"state": parts[2], "sub": parts[3]}
    enabled = {}
    for line in _run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--plain"]).splitlines():
        parts = line.split()                                     # UNIT STATE [PRESET]
        if len(parts) >= 2:
            enabled[parts[0]] = parts[1]
    out = {}
    for unit in sorted(set(units) | set(enabled)):
        row = dict(units.get(unit) or {"state": "not-loaded", "sub": "-"})
        start = enabled.get(unit)
        if start is None:                                        # e.g. template instances: ask for this unit
            try:
                r = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True,
                                   timeout=30)
                start = (r.stdout or "").strip().splitlines()[0] if (r.stdout or "").strip() else "unknown"
            except (OSError, subprocess.TimeoutExpired):
                start = "unknown"
        row["start"] = start
        out[unit] = row
    return out


def services():
    if host()["is_windows"]:
        rows = _ps_json("Get-CimInstance Win32_Service | Select-Object Name,State,StartMode")
        return {r["Name"]: {"state": r.get("State"), "start": r.get("StartMode")} for r in rows if r.get("Name")}
    return _systemd_services()


def tasks():
    if host()["is_windows"]:
        rows = _ps_json("Get-ScheduledTask | Select-Object TaskPath,TaskName,@{n='State';e={[string]$_.State}}")
        return {f"{r.get('TaskPath', '')}{r.get('TaskName', '')}": {"state": r.get("State")} for r in rows}
    out = _run(["systemctl", "list-timers", "--all", "--no-legend", "--plain"])
    return {line.split()[-2]: {"state": "timer"} for line in out.splitlines() if len(line.split()) >= 2}


def programs():
    if host()["is_windows"]:
        rows = _ps_json("Get-ItemProperty 'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                        "'HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
                        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue"
                        " | Where-Object DisplayName | Select-Object DisplayName,DisplayVersion")
        return {r["DisplayName"]: {"version": r.get("DisplayVersion")} for r in rows if r.get("DisplayName")}
    out = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n"])
    return {a: {"version": b} for a, _, b in (l.partition("\t") for l in out.splitlines()) if a}


def processes():
    if host()["is_windows"]:
        out = _run(["tasklist", "/fo", "csv", "/nh"])
        names = [line.split('","')[0].strip('"') for line in out.splitlines() if line]
    else:
        out = _run(["ps", "-eo", "comm="])
        names = [n.strip() for n in out.splitlines() if n.strip()]
    if not names:
        raise NotCaptured("the process list came back empty")
    counts = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return {n: {"count": c} for n, c in counts.items()}


QUERIES = {"services": services, "tasks": tasks, "programs": programs, "processes": processes}


def capture(part):
    """-> the part's dict, or {NOT_CAPTURED: reason} when its query failed. Never raises for a query failure."""
    try:
        return QUERIES[part]()
    except NotCaptured as e:
        return {NOT_CAPTURED: str(e)}


def _guarded(part):
    def run():
        return capture(part)
    run.__name__ = part
    return run


PARTS = {p: _guarded(p) for p in QUERIES}


def not_captured(section):
    """-> the reason a stored part was not captured, or None when it holds real data."""
    if isinstance(section, dict) and NOT_CAPTURED in section:
        return section[NOT_CAPTURED] or "not captured"
    if not isinstance(section, dict):
        return "not captured (unreadable section)"
    return None


def take(name, paths=(), parts=()):
    check_name(name)
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
    """A snapshot by name (state/snapshots/NAME.json), or an explicit existing .json file path."""
    if isinstance(name, str) and NAME_RX.fullmatch(name) and set(name) != {"."}:
        return json.loads((SNAP_DIR / f"{name}.json").read_text(encoding="utf-8"))
    p = Path(name)
    if p.suffix.lower() == ".json" and p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    check_name(name)                                 # raises with the reason


def diff(a, b):
    """-> {part: {"added": [...], "removed": [...], "changed": {key: {"before", "after"}}}} for parts in both.

    A part not captured on either side is not compared: it comes back with empty lists and
    "not_compared": the reason, so a failed query can never look like everything was removed."""
    a = a if isinstance(a, dict) else load(a)
    b = b if isinstance(b, dict) else load(b)
    out = {}
    for part in ("files",) + tuple(PARTS):
        if part not in a or part not in b:
            continue
        x, y = a[part], b[part]
        why = [f"{side}: {r}" for side, r in (("before", not_captured(x)), ("after", not_captured(y))) if r]
        if why:
            out[part] = {"added": [], "removed": [], "changed": {}, "not_compared": "; ".join(why)}
            continue
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
        if ch.get("not_compared"):
            lines.append(f"{part}: NOT COMPARED - not captured ({ch['not_compared']})")
            continue
        lines.append(f"{part}: +{len(ch['added'])} added, -{len(ch['removed'])} removed, ~{len(ch['changed'])} changed")
        for k in ch["added"][:limit]:
            lines.append(f"   + {k}")
        for k in ch["removed"][:limit]:
            lines.append(f"   - {k}")
        for k in list(ch["changed"])[:limit]:
            lines.append(f"   ~ {k}")
    return lines or ["no changes"]
