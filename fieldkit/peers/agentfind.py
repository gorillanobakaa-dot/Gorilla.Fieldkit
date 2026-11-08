"""Find every agent's program wherever it is: PATH, the running process's own executable, the known install places.

Owner, 2026-10-31: the VAIO said `claude  running 5071` with no path and no version, because the desktop app carries
its own Claude Code outside PATH and `link.agents` looked only on PATH. One finder, used by `peers link hello/agents`,
the `task start --on` precheck and the listener's start. It runs nothing but `PROGRAM --version` (5 s) of a found file.
Every place (PATH, process lister, folders, runner) is a parameter, so a test fakes each system.
"""
from __future__ import annotations

import glob as _glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

# name -> (executable stems, version args, text the version line must contain to count, or "")
AGENTS = {
    "claude": (["claude"], ["--version"], "Claude Code"),      # the desktop app's own claude.exe prints no "Claude Code"
    "codex": (["codex"], ["--version"], ""),
    "gemini": (["gemini"], ["--version"], ""),
    "opencode": (["opencode"], ["--version"], ""),
    "gorilla-opencode": (["gorilla-opencode", "gorilla"], ["--version"], ""),
    "antigravity": (["antigravity", "agy", "Antigravity"], None, ""),
}
VERSION_TIMEOUT = 5


def _exts(osname):
    return [".exe", ".cmd", ".bat", ""] if osname == "win32" else [""]


def known_dirs(stem, osname=None, home=None, env=None):
    """Glob patterns of the places an agent's program may live on this system (folders first, newest last by sort)."""
    osname = osname or sys.platform
    home = str(home or Path.home())
    env = os.environ if env is None else env
    j = os.path.join
    pats = []
    if osname == "win32":
        la, ra = env.get("LOCALAPPDATA", ""), env.get("APPDATA", "")
        for e in _exts(osname):
            n = stem + e
            pats += [j(home, ".local", "bin", n), j(home, ".claude", "local", n), j(ra, "npm", n),
                     j(la, "Programs", "*", n), j(la, "Programs", "*", "bin", n), j(la, "Programs", stem, n)]
        if stem == "claude":      # the desktop app's bundled Claude Code (MSIX package folder is real for everyone)
            pats += [j(la, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code", "*", "*", "claude.exe"),
                     j(ra, "Claude", "claude-code", "*", "*", "claude.exe"),
                     j(la, "Claude*", "claude-code", "*", "*", "claude.exe"),
                     j(la, "Claude*", "app-*", "resources", "claude.exe")]
    else:
        mac = osname == "darwin"
        pats += [j(home, ".local", "bin", stem), j(home, ".claude", "local", stem), j(home, ".npm-global", "bin", stem),
                 j(home, ".nvm", "versions", "node", "*", "bin", stem), j(home, ".bun", "bin", stem),
                 j(home, "go", "bin", stem), "/usr/local/bin/" + stem, "/usr/bin/" + stem, "/opt/homebrew/bin/" + stem,
                 "/opt/*/" + stem, "/opt/*/bin/" + stem, "/usr/lib/*/" + stem, "/usr/lib/*/bin/" + stem,
                 "/snap/bin/" + stem]
        if stem == "claude":      # the desktop app's bundled Claude Code
            cfg = j(home, "Library", "Application Support") if mac else env.get("XDG_CONFIG_HOME") or j(home, ".config")
            pats += [j(cfg, "Claude*", "claude-code", "*", stem), j(cfg, "Claude*", "claude-code", "*", "*", stem),
                     j(home, ".local", "share", "Claude*", "claude-code", "*", stem),
                     "/opt/Claude*/resources/" + stem, "/usr/lib/claude*/" + stem]
            if mac:
                pats += ["/Applications/Claude.app/Contents/Resources/" + stem,
                         j(home, "Applications", "Claude.app", "Contents", "Resources", stem)]
    return pats


def _vkey(p):
    """Sort key: the numbers in the path, so 2.1.10 outranks 2.1.9."""
    import re
    return [int(x) for x in re.findall(r"\d+", p)]


def running(procs, stems):
    """-> ([pids], [exe paths]) of processes whose name is one of `stems`. procs: [(pid, name, exe)]."""
    low = {s.lower() for s in stems}
    pids, exes = [], []
    for pid, name, exe in procs:
        if (name or "").lower().rsplit(".", 1)[0] in low:
            pids.append(pid)
            if exe and exe not in exes:
                exes.append(exe)
    return pids, exes


def process_list():
    """[(pid, name, exe)] of this computer's processes, exe "" when the system will not say."""
    out = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "exe"]):
            out.append((p.info["pid"], p.info.get("name") or "", p.info.get("exe") or ""))
    except Exception:  # noqa: BLE001 - no psutil or no right: the other ways still answer
        pass
    return out


def version_of(exe, args, run=subprocess.run):
    """First line of `exe --version` (5 s cap); "" when it cannot say. The only thing ever executed."""
    if not args:
        return ""
    try:
        r = run([exe, *args], capture_output=True, text=True, timeout=VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (r.stdout or r.stderr or "").strip()
    return text.splitlines()[0][:80] if text else ""


def find_one(name, which=shutil.which, procs=None, glob=_glob.glob, isfile=os.path.isfile, run=subprocess.run,
             osname=None, home=None, env=None):
    """-> {agent, installed, path, version, how, running}. how: 'PATH', 'process', 'known place' or ''."""
    stems, vargs, must = AGENTS[name]
    procs = process_list() if procs is None else procs
    pids, pexes = running(procs, stems)
    cands = []                                                   # (how, path), in order of trust
    for s in stems:
        w = which(s)
        if w:
            cands.append(("PATH", w))
    cands += [("process", e) for e in pexes if isfile(e)]
    for s in stems:
        for pat in known_dirs(s, osname, home, env):
            hits = sorted((h for h in glob(pat) if isfile(h)), key=_vkey)
            cands += [("known place", h) for h in reversed(hits)]
    seen = set()
    for how, path in cands:
        if path in seen:
            continue
        seen.add(path)
        ver = version_of(path, vargs, run)
        if must and must not in ver:                             # e.g. the desktop app's own claude.exe: not Claude Code
            continue
        return {"agent": name, "installed": True, "path": path, "version": ver, "how": how, "running": pids}
    return {"agent": name, "installed": False, "path": "", "version": "", "how": "", "running": pids}


def find_all(names=None, **kw):
    kw.setdefault("procs", process_list())
    return [find_one(n, **kw) for n in (names or AGENTS)]


def find_path(name, **kw):
    """The program's path, or None."""
    return find_one(name, **kw)["path"] or None


def main(argv=None):
    import json
    argv = list(argv or [])
    rows = find_all([a for a in argv if a in AGENTS] or None)
    if "--json" in argv:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(f"  {r['agent']:<17} {r['path'] or 'not found':<60} {r['version'][:30]:<30} {r['how']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
