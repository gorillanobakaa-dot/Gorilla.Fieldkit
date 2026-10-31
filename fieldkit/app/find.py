"""Find any program by name on Windows, Linux and macOS: every match with its path, version when cheap, how it was found,
and whether it runs now.

Owner, 2026-10-31: the agent finder (peers/agentfind.py) should not be limited to Claude; "it should be able to start
every other program, on windows, linux and OSX". Every source is read-only and is a parameter (`env`), so a test fakes a
registry, a .desktop folder or mdfind's output. Nothing here runs a found program: a version comes from the registry,
the bundle's Info.plist or the file's own version resource, never from `PROGRAM --version` (notepad --version opens
Notepad). Only the known agents, whose `--version` is safe, get that through agentfind.
"""
from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

from ..peers import agentfind

TRUST = ("agent finder", "PATH", "process", "App Paths", "Uninstall", "Start Menu", "Programs folder", "Start apps",
         "desktop file", "flatpak", "snap", "application bundle", "Applications folder", "mdfind")


def norm(s):
    """'Gorilla OpenCode' == 'gorilla-opencode' == 'gorilla_opencode.exe' -> 'gorillaopencode'."""
    s = str(s or "").lower()
    s = re.sub(r"\.(exe|cmd|bat|lnk|app|desktop|com)$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def score(query, name):
    """2 = same name, 1 = the query is inside the name (or the name inside a longer query), 0 = no match."""
    q, n = norm(query), norm(name)
    if not q or not n:
        return 0
    if q == n:
        return 2
    return 1 if (q in n and len(q) >= 3) else 0


def _entry(name, path, how, launch, version="", **extra):
    return {"name": name, "path": path, "version": version, "how": how, "launch": launch, "running": [], **extra}


def _run(argv, run=subprocess.run, timeout=20):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout or ""


# ---- all systems ---------------------------------------------------------------------------------------------------

def src_path(query, env):
    out = []
    osname = env["osname"]
    exts = [e.lower() for e in (env["environ"].get("PATHEXT") or ".EXE;.CMD;.BAT;.COM").split(";")] if osname == "win32" else [""]
    for d in (env["environ"].get("PATH") or "").split(os.pathsep):
        for name in env["listdir"](d) if d else []:
            stem, ext = os.path.splitext(name)
            if osname == "win32" and ext.lower() not in exts:
                continue
            if score(query, stem):
                p = os.path.join(d, name)
                if env["isfile"](p) and (osname == "win32" or env["isexec"](p)):
                    out.append(_entry(stem, p, "PATH", {"kind": "exe", "target": p}))
    return out


def src_process(query, env):
    out = []
    for pid, name, exe in env["procs"]:
        if exe and score(query, os.path.splitext(name or "")[0]):
            out.append(_entry(os.path.splitext(name)[0], exe, "process", {"kind": "exe", "target": exe}))
    return out


def src_agents(query, env):
    out = []
    for key in agentfind.AGENTS:
        if score(query, key) == 2:
            r = agentfind.find_one(key, procs=env["procs"], which=env["which"], glob=env["glob"], isfile=env["isfile"],
                                   run=env["run"], osname=env["osname"], home=env["home"], env=env["environ"])
            if r["installed"]:
                out.append(_entry(key, r["path"], "agent finder", {"kind": "exe", "target": r["path"]},
                                  version=r["version"]))
    return out


# ---- Windows -------------------------------------------------------------------------------------------------------

def _icon_path(s):
    s = str(s or "").strip().strip('"')
    m = re.match(r'^(.*?\.(?:exe|ico))(?:,-?\d+)?$', s, re.I)
    return m.group(1).strip('"') if m else ""


def src_registry(query, env):
    """App Paths (name.exe -> path) and Uninstall (DisplayName, DisplayIcon, InstallLocation, DisplayVersion)."""
    out = []
    for hive, sub, rows in env["registry"]():
        if sub == "App Paths":
            for key, vals in rows:
                if score(query, key):
                    p = str(vals.get("") or "").strip('"')
                    if p:
                        out.append(_entry(norm_name(key), p, "App Paths", {"kind": "exe", "target": p}))
        else:
            for key, vals in rows:
                dn = vals.get("DisplayName") or ""
                if not score(query, dn):
                    continue
                exe = _icon_path(vals.get("DisplayIcon"))
                if not exe.lower().endswith(".exe"):
                    exe = ""
                loc = str(vals.get("InstallLocation") or "").strip('"')
                p = exe or loc
                if p:
                    kind = "exe" if exe else "folder"
                    out.append(_entry(dn, p, "Uninstall", {"kind": kind, "target": p},
                                      version=str(vals.get("DisplayVersion") or ""), install_location=loc))
    return out


def norm_name(key):
    return re.sub(r"\.exe$", "", key, flags=re.I)


def src_start_menu(query, env):
    out = []
    for folder in env["lnk_dirs"]:
        for p in env["walk_lnk"](folder):
            stem = Path(p).stem
            if score(query, stem):
                tgt = env["lnk_target"](p)
                out.append(_entry(stem, tgt or p, "Start Menu", {"kind": "lnk", "target": p}, lnk=p))
    return out


def src_programs(query, env):
    out = []
    la = env["environ"].get("LOCALAPPDATA", "")
    base = os.path.join(la, "Programs") if la else ""
    for d in env["listdir"](base) if base else []:
        if score(query, d):
            for name in env["listdir"](os.path.join(base, d)):
                if name.lower().endswith(".exe") and score(query, os.path.splitext(name)[0]) and \
                        "unins" not in name.lower():
                    p = os.path.join(base, d, name)
                    out.append(_entry(os.path.splitext(name)[0], p, "Programs folder", {"kind": "exe", "target": p}))
    return out


def src_start_apps(query, env):
    """Store apps and anything else Start lists (Get-StartApps: readable without admin)."""
    out = []
    for name, appid in env["start_apps"]():
        if score(query, name):
            out.append(_entry(name, "shell:AppsFolder\\" + appid, "Start apps", {"kind": "appsfolder", "target": appid}))
    return out


def _powershell_json(script, run):
    import json
    txt = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], run)
    try:
        d = json.loads(txt) if txt.strip() else []
    except ValueError:
        return []
    return d if isinstance(d, list) else [d]


def real_start_apps(run=subprocess.run):
    rows = _powershell_json("Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress", run)
    return [(r.get("Name", ""), r.get("AppID", "")) for r in rows if r.get("Name") and r.get("AppID")]


def real_lnk_target(path, run=subprocess.run):
    s = "(New-Object -ComObject WScript.Shell).CreateShortcut('%s').TargetPath" % path.replace("'", "''")
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", s], run).strip()


def real_registry():
    """[(hive, 'App Paths'|'Uninstall', [(subkey, {value: data})])] for HKLM and HKCU, 64 and 32 bit views."""
    import winreg
    spec = [("App Paths", r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            ("Uninstall", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ("Uninstall", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")]
    out = []
    for hname, hive in (("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)):
        for kind, path in spec:
            rows = []
            try:
                with winreg.OpenKey(hive, path) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sk = winreg.EnumKey(k, i)
                        try:
                            with winreg.OpenKey(k, sk) as s:
                                vals = {}
                                for j in range(winreg.QueryInfoKey(s)[1]):
                                    n, v, _ = winreg.EnumValue(s, j)
                                    vals[n] = v
                            rows.append((sk, vals))
                        except OSError:
                            pass
            except OSError:
                continue
            out.append((hname, kind, rows))
    return out


def real_walk_lnk(folder):
    out = []
    for root, _dirs, files in os.walk(folder):
        out += [os.path.join(root, f) for f in files if f.lower().endswith(".lnk")]
    return out


def exe_version(path):
    """The file's own version resource (Windows); "" when there is none. Reads, never runs."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        v = ctypes.windll.version
        size = v.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        v.GetFileVersionInfoW(path, 0, size, buf)
        ptr, ln = ctypes.c_void_p(), wintypes.UINT()
        if not v.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
            return ""
        a = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 6)).contents
        return f"{a[4] >> 16}.{a[4] & 0xffff}.{a[5] >> 16}.{a[5] & 0xffff}"
    except Exception:  # noqa: BLE001
        return ""


# ---- Linux ---------------------------------------------------------------------------------------------------------

def desktop_dirs(environ, home):
    dirs = [os.path.join(environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), "applications")]
    for d in (environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(os.path.join(d, "applications"))
    dirs += ["/var/lib/flatpak/exports/share/applications", os.path.join(home, ".local/share/flatpak/exports/share/applications"),
             "/var/lib/snapd/desktop/applications"]
    seen = []
    for d in dirs:
        if d not in seen:
            seen.append(d)
    return seen


def parse_desktop(text):
    """[Desktop Entry] keys of a .desktop file (first Name= without a locale)."""
    d, ok = {}, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            ok = line == "[Desktop Entry]"
            continue
        if ok and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d.setdefault(k.strip(), v.strip())
    return d


def src_desktop(query, env):
    out = []
    for folder in desktop_dirs(env["environ"], env["home"]):
        for fn in env["listdir"](folder):
            if not fn.endswith(".desktop"):
                continue
            p = os.path.join(folder, fn)
            did = fn[:-len(".desktop")]
            exec_name = ""
            try:
                d = parse_desktop(env["read"](p))
            except OSError:
                continue
            if d.get("Type", "Application") != "Application" or d.get("Hidden", "").lower() == "true":
                continue
            ex = (d.get("TryExec") or d.get("Exec") or "").split(" ")[0].strip('"')
            exec_name = os.path.basename(ex)
            best = max(score(query, did.rsplit(".", 1)[-1]), score(query, did), score(query, d.get("Name", "")),
                       score(query, exec_name))
            if best:
                out.append(_entry(d.get("Name") or did, p, "desktop file", {"kind": "desktop", "target": p, "id": did},
                                  exec=d.get("Exec", ""), nodisplay=d.get("NoDisplay", "").lower() == "true"))
    return out


def src_flatpak(query, env):
    out = []
    for line in env["lines"](["flatpak", "list", "--app", "--columns=application,name,version"]):
        parts = line.split("\t")
        if len(parts) >= 2 and (score(query, parts[1]) or score(query, parts[0].rsplit(".", 1)[-1])):
            out.append(_entry(parts[1], parts[0], "flatpak", {"kind": "flatpak", "target": parts[0]},
                              version=parts[2] if len(parts) > 2 else ""))
    return out


def src_snap(query, env):
    out = []
    for line in env["lines"](["snap", "list"])[1:]:
        parts = line.split()
        if parts and score(query, parts[0]):
            out.append(_entry(parts[0], parts[0], "snap", {"kind": "snap", "target": parts[0]},
                              version=parts[1] if len(parts) > 1 else ""))
    return out


# ---- macOS ---------------------------------------------------------------------------------------------------------

def bundle_info(app, read_bytes):
    try:
        d = plistlib.loads(read_bytes(os.path.join(app, "Contents", "Info.plist")))
    except Exception:  # noqa: BLE001
        return {}
    return {"version": str(d.get("CFBundleShortVersionString") or d.get("CFBundleVersion") or ""),
            "id": str(d.get("CFBundleIdentifier") or ""), "name": str(d.get("CFBundleName") or "")}


def src_bundles(query, env):
    """mdfind application bundles (and /Applications, ~/Applications, /System/Applications when Spotlight is off)."""
    paths, how = [], {}
    for p in env["lines"](["mdfind", "kMDItemContentType == com.apple.application-bundle"]):
        if p.endswith(".app"):
            paths.append(p)
            how[p] = "mdfind"
    if "." in query:                                            # a bundle id such as com.apple.TextEdit
        for p in env["lines"](["mdfind", 'kMDItemCFBundleIdentifier == "%s"' % query.replace('"', "")]):
            if p.endswith(".app") and p not in how:
                paths.append(p)
                how[p] = "mdfind"
    for folder in ("/Applications", os.path.join(env["home"], "Applications"), "/System/Applications",
                   "/System/Applications/Utilities", "/Applications/Utilities"):
        for fn in env["listdir"](folder):
            p = os.path.join(folder, fn)
            if fn.endswith(".app") and p not in how:
                paths.append(p)
                how[p] = "Applications folder"
    out = []
    for p in paths:
        stem = os.path.basename(p)[:-4]
        info = None
        hit = score(query, stem)
        if not hit and "." in query:
            info = bundle_info(p, env["read_bytes"])
            hit = 2 if info.get("id", "").lower() == query.lower() else 0
        if hit:
            info = info or bundle_info(p, env["read_bytes"])
            out.append(_entry(stem, p, how[p], {"kind": "bundle", "target": p}, version=info.get("version", ""),
                              bundle_id=info.get("id", "")))
    return out


# ---- the finder ----------------------------------------------------------------------------------------------------

def real_env(osname=None):
    import glob as _g
    import shutil
    home = str(Path.home())
    environ = dict(os.environ)

    def listdir(d):
        try:
            return sorted(os.listdir(d))
        except OSError:
            return []

    def lines(argv):
        return [x for x in _run(argv).splitlines() if x.strip()] if shutil.which(argv[0]) else []

    ra, pd = environ.get("APPDATA", ""), environ.get("ProgramData", "")
    return {"osname": osname or sys.platform, "environ": environ, "home": home, "procs": None,
            "listdir": listdir, "isfile": os.path.isfile, "glob": _g.glob, "which": shutil.which, "run": subprocess.run,
            "isexec": lambda p: os.access(p, os.X_OK), "lines": lines,
            "read": lambda p: Path(p).read_text(encoding="utf-8", errors="replace"),
            "read_bytes": lambda p: Path(p).read_bytes(),
            "registry": real_registry if (osname or sys.platform) == "win32" else (lambda: []),
            "lnk_dirs": [d for d in (os.path.join(ra, "Microsoft", "Windows", "Start Menu", "Programs") if ra else "",
                                     os.path.join(pd, "Microsoft", "Windows", "Start Menu", "Programs") if pd else "",
                                     os.path.join(home, "Desktop"), os.path.join(environ.get("PUBLIC", ""), "Desktop"))
                         if d and os.path.isdir(d)],
            "walk_lnk": real_walk_lnk, "lnk_target": real_lnk_target, "start_apps": real_start_apps}


def sources_for(osname):
    base = [src_agents, src_path, src_process]
    if osname == "win32":
        return base + [src_registry, src_start_menu, src_programs]
    if osname == "darwin":
        return base + [src_bundles]
    return base + [src_desktop, src_flatpak, src_snap]


def find(query, env=None, osname=None):
    """-> matches, best first: each {name, path, version, how, launch, running, score}. Ambiguity is the caller's."""
    env = env or real_env(osname)
    env.setdefault("osname", osname or sys.platform)
    if env.get("procs") is None:
        env["procs"] = agentfind.process_list()
    rows = []
    for fn in sources_for(env["osname"]):
        try:
            rows += fn(query, env)
        except Exception:  # noqa: BLE001 - a source that fails says nothing; the others answer
            continue
    if not rows and env["osname"] == "win32":                  # the slow source (a PowerShell call), only as a last resort
        try:
            rows = src_start_apps(query, env)
        except Exception:  # noqa: BLE001
            rows = []
    seen, out = set(), []
    for r in rows:
        r["score"] = max(score(query, r["name"]), score(query, Path(r["path"]).stem if r["path"] else ""),
                         2 if r.get("bundle_id", "x").lower() == query.lower() else 0)
        if not r["score"]:
            continue
        key = os.path.normcase(os.path.realpath(r["path"])) if r["launch"]["kind"] == "exe" else (r["launch"]["kind"], r["path"])
        if key in seen:
            continue
        seen.add(key)
        if not r["version"] and r["launch"]["kind"] == "exe" and env["osname"] == "win32" and env["isfile"](r["path"]):
            r["version"] = exe_version(r["path"])
        stem = norm(Path(r["path"]).stem) if r["path"] else ""
        isexe = r["path"].lower().endswith((".exe", ".com")) or r["launch"]["kind"] == "exe"
        r["running"] = [pid for pid, name, exe in env["procs"]
                        if (isexe and exe and os.path.normcase(exe) == os.path.normcase(r["path"]))
                        or (not isexe and stem and norm(os.path.splitext(name or "")[0]) == stem)]
        out.append(r)
    out.sort(key=lambda r: (-r["score"], TRUST.index(r["how"]) if r["how"] in TRUST else 99))
    return out


def pick(matches):
    """-> (match | None, why). The best-scoring matches under one name (any number of paths) resolve to the
    first by trust; different names are ambiguous: the caller lists them and never guesses."""
    if not matches:
        return None, "not found"
    top = [m for m in matches if m["score"] == matches[0]["score"]]
    names = {norm(m["name"]) for m in top}
    if len(names) > 1:
        return None, "ambiguous: " + ", ".join(sorted({m["name"] for m in top}))
    return top[0], "ok"


def render(matches):
    if not matches:
        return ["not found"]
    return [f"  {m['name'][:28]:<28} {m['how']:<16} {('running ' + ','.join(map(str, m['running'][:4]))) if m['running'] else 'not running':<18}"
            f" {m['version'][:16]:<16} {m['path']}" for m in matches]
