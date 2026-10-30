"""Every browser installed on this computer, found without running any of them (except `--version` for a version).

Windows: the registry's registered browsers and App Paths (winreg), then the known install folders. Linux: .desktop
files and known names on PATH. macOS: application bundles whose Info.plist handles http(s); Safari always. Plus the
owner's own: fieldkit.local.json "browser_fetch": {"extra": [PATHS], "prefer": [NAMES]} and the Gorilla Firefox tree
("firefox": {"root": ...}). Each row: name, path, version, engine (chromium|gecko|webkit|unknown), method, why (when
no method), source. Every function takes its inputs, so the tests feed fixtures.
"""
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

GECKO_NAMES = ("firefox", "librewolf", "waterfox", "icecat", "zen", "floorp", "gorilla", "tor browser", "basilisk",
               "palemoon", "seamonkey")
KNOWN_EXES = {"chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "brave.exe": "Brave",
              "vivaldi.exe": "Vivaldi", "opera.exe": "Opera", "firefox.exe": "Mozilla Firefox",
              "librewolf.exe": "LibreWolf", "waterfox.exe": "Waterfox", "chromium.exe": "Chromium"}
WIN_FOLDERS = [  # (path under a base folder, display name)
    ("Google/Chrome/Application/chrome.exe", "Google Chrome"), ("Microsoft/Edge/Application/msedge.exe", "Microsoft Edge"),
    ("BraveSoftware/Brave-Browser/Application/brave.exe", "Brave"), ("Vivaldi/Application/vivaldi.exe", "Vivaldi"),
    ("Opera/opera.exe", "Opera"), ("Programs/Opera/opera.exe", "Opera"), ("Chromium/Application/chrome.exe", "Chromium"),
    ("Mozilla Firefox/firefox.exe", "Mozilla Firefox"), ("LibreWolf/librewolf.exe", "LibreWolf"),
    ("Waterfox/waterfox.exe", "Waterfox")]
LINUX_NAMES = ("firefox", "librewolf", "waterfox", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
               "brave-browser", "brave", "microsoft-edge", "microsoft-edge-stable", "vivaldi", "vivaldi-stable", "opera")
LINUX_DIRS = ("/usr/share/applications", "~/.local/share/applications", "/var/lib/flatpak/exports/share/applications",
              "~/.local/share/flatpak/exports/share/applications", "/var/lib/snapd/desktop/applications")
MAC_DIRS = ("/Applications", "~/Applications")
BIDI_MIN_FIREFOX = 129


def engine_of(name, path=""):
    s = f"{name} {Path(path).stem}".lower()
    if "safari" in s:
        return "webkit"
    if "internet explorer" in s or "iexplore" in s:
        return "unknown"
    if any(g in s for g in GECKO_NAMES):
        return "gecko"
    return "chromium"


def major(version):
    m = re.match(r"\s*(\d+)", version or "")
    return int(m.group(1)) if m else 0


def method_for(engine, version, have_geckodriver=False, platform=None):
    """-> (method, why-not). One method per engine; the doctor explains a missing one."""
    platform = platform or sys.platform
    if engine == "chromium":
        return "chromium-dump-dom", ""
    if engine == "gecko":
        if not version or major(version) >= BIDI_MIN_FIREFOX:
            return "gecko-bidi", ""
        if have_geckodriver:
            return "geckodriver", ""
        return "", (f"version {version} is too old for WebDriver BiDi (needs Firefox {BIDI_MIN_FIREFOX}+): "
                    f"update it, or install geckodriver")
    if engine == "webkit":
        return ("safaridriver", "") if platform == "darwin" else ("", "Safari is only on macOS")
    return "", "no automation interface for this browser"


def _clean_exe(command):
    """'"C:\\x\\chrome.exe" --flag %1' or 'C:\\x\\a.exe -url' -> the executable path."""
    command = (command or "").strip()
    if command.startswith('"'):
        return command[1:].split('"', 1)[0]
    m = re.match(r"(.+?\.exe)", command, re.I)
    return m.group(1) if m else command.split(" ", 1)[0]


# -- versions ---------------------------------------------------------------------------------------------------
def version_of(path, run=subprocess.run, platform=None):
    p = Path(path)
    platform = platform or sys.platform
    try:
        ini = p.parent / "application.ini"               # Firefox family
        if ini.is_file():
            m = re.search(r"^Version=(.+)$", ini.read_text(errors="replace"), re.M)
            if m:
                return m.group(1).strip()
        for d in sorted((x for x in p.parent.iterdir() if x.is_dir() and re.match(r"\d+\.\d+\.\d+\.\d+$", x.name)),
                        reverse=True):                   # Chrome, Edge, Brave, Vivaldi: a folder named by version
            return d.name
    except OSError:
        pass
    if platform.startswith("win"):
        return ""                                        # chrome.exe --version prints nothing on Windows
    try:
        out = run([str(p), "--version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"(\d+(?:\.\d+)+)", out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def row(name, path, source, version=None, run=subprocess.run, have_geckodriver=False, why="", platform=None):
    path = str(path)
    eng = engine_of(name, path)
    ver = version if version is not None else version_of(path, run, platform)
    method, nope = method_for(eng, ver, have_geckodriver, platform)
    return {"name": name, "path": path, "version": ver, "engine": eng, "method": method, "why": why or nope,
            "source": source}


# -- Windows ----------------------------------------------------------------------------------------------------
def windows_registry():
    """-> [(display name, command line, source)] from HKLM/HKCU StartMenuInternet and App Paths. Empty off Windows."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for hive, hname in ((winreg.HKEY_LOCAL_MACHINE, "HKLM"), (winreg.HKEY_CURRENT_USER, "HKCU")):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Clients\StartMenuInternet", 0, winreg.KEY_READ | view) as k:
                    i = 0
                    while True:
                        try:
                            sub = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(k, sub) as sk:
                                try:
                                    name = winreg.QueryValue(sk, None) or sub
                                except OSError:
                                    name = sub
                                with winreg.OpenKey(sk, r"shell\open\command") as ck:
                                    cmd = winreg.QueryValue(ck, None)
                            out.append((name, cmd, f"{hname} StartMenuInternet"))
                        except OSError:
                            continue
            except OSError:
                pass
            for exe, name in KNOWN_EXES.items():
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}", 0,
                                        winreg.KEY_READ | view) as k:
                        out.append((name, winreg.QueryValue(k, None), f"{hname} App Paths"))
                except OSError:
                    pass
    return out


def windows_candidates(registry, env, is_file=os.path.isfile):
    """registry: [(name, command, source)]. -> [(name, path, source)] that exist: the registry first, then folders."""
    out = []
    for name, cmd, src in registry:
        exe = _clean_exe(cmd)
        if exe and is_file(exe):
            out.append((name, exe, src))
    for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = env.get(k)
        for rel, name in WIN_FOLDERS if base else ():
            p = os.path.join(base, *rel.split("/"))
            if is_file(p):
                out.append((name, p, "install folder"))
    return out


# -- Linux ------------------------------------------------------------------------------------------------------
def parse_desktop(text):
    """-> {"name","exec","categories","mime"} of the [Desktop Entry] section."""
    d, inside = {}, False
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("["):
            inside = ln == "[Desktop Entry]"
        elif inside and "=" in ln and not ln.startswith("#"):
            k, _, v = ln.partition("=")
            d.setdefault(k.strip(), v.strip())          # the unlocalised key comes first
    return {"name": d.get("Name", ""), "exec": d.get("Exec", ""), "categories": d.get("Categories", ""),
            "mime": d.get("MimeType", "")}


def is_browser_entry(e):
    return "WebBrowser" in e["categories"].split(";") or "x-scheme-handler/http" in e["mime"].split(";")


def exec_binary(cmd, which=shutil.which):
    """The Exec line without field codes -> (binary path or '', note)."""
    try:
        parts = [a for a in shlex.split(cmd) if not re.fullmatch(r"%[a-zA-Z%]", a)]
    except ValueError:
        return "", "unreadable Exec line"
    while parts and (parts[0] == "env" or "=" in parts[0]):
        parts = parts[1:]
    if not parts:
        return "", "empty Exec line"
    if parts[0].endswith("flatpak"):
        return "", "a Flatpak app: its sandbox hides the profile folder, so it is not driven"
    p = parts[0] if os.path.isabs(parts[0]) else which(parts[0])
    return (p or ""), ("" if p else f"{parts[0]} is not on PATH")


def linux_candidates(dirs=LINUX_DIRS, names=LINUX_NAMES, which=shutil.which, listdir=os.listdir,
                     read=lambda p: Path(p).read_text(errors="replace")):
    """-> [(name, path, source, note)]; a note instead of a path when the entry cannot be driven."""
    out = []
    for d in dirs:
        d = os.path.expanduser(d)
        try:
            files = sorted(listdir(d))
        except OSError:
            continue
        for f in files:
            if not f.endswith(".desktop"):
                continue
            try:
                e = parse_desktop(read(os.path.join(d, f)))
            except OSError:
                continue
            if is_browser_entry(e):
                p, note = exec_binary(e["exec"], which)
                out.append((e["name"] or f[:-8], p, f".desktop {os.path.join(d, f)}", note))
    for n in names:
        p = which(n)
        if p:
            out.append((n, p, "PATH", ""))
    return out


# -- macOS ------------------------------------------------------------------------------------------------------
def mac_candidates(dirs=MAC_DIRS, listdir=os.listdir, load=lambda p: plistlib.loads(Path(p).read_bytes()),
                   exists=os.path.exists):
    """-> [(name, binary path, version, source)] for bundles whose Info.plist handles http(s); Safari always."""
    out = []
    for d in dirs:
        d = os.path.expanduser(d)
        try:
            apps = sorted(listdir(d))
        except OSError:
            continue
        for a in apps:
            if not a.endswith(".app"):
                continue
            bundle = os.path.join(d, a)
            try:
                info = load(os.path.join(bundle, "Contents", "Info.plist"))
            except Exception:
                continue
            schemes = {s.lower() for t in info.get("CFBundleURLTypes", []) for s in t.get("CFBundleURLSchemes", [])}
            if {"http", "https"} & schemes or a == "Safari.app":
                exe = info.get("CFBundleExecutable", a[:-4])
                out.append((info.get("CFBundleName") or a[:-4], os.path.join(bundle, "Contents", "MacOS", exe),
                            info.get("CFBundleShortVersionString", ""), "Info.plist"))
    if not any(n == "Safari" for n, *_ in out) and exists("/Applications/Safari.app"):
        out.append(("Safari", "/Applications/Safari.app", "", "Safari.app"))
    return out


# -- the owner's own --------------------------------------------------------------------------------------------
def gorilla_firefox(root, windows=None):
    exe = "firefox.exe" if (sys.platform.startswith("win") if windows is None else windows) else "firefox"
    try:
        return [("Gorilla Firefox", str(p), f"firefox.root {root}")
                for p in sorted(Path(root).glob("obj-*/dist/bin/*")) if p.name == exe and p.is_file()]
    except OSError:
        return []


def discover(cfg=None, local=None, env=None, run=subprocess.run, platform=None):
    """-> list of rows (see choose for the order to use). cfg = browser_fetch, local = the whole local settings."""
    cfg, local = cfg or {}, local or {}
    env = os.environ if env is None else env
    platform = platform or sys.platform
    gd = bool(cfg.get("geckodriver") and Path(cfg["geckodriver"]).is_file()) or bool(shutil.which("geckodriver"))
    rows, seen = [], set()

    def add(name, path, source, version=None, why=""):
        if not path:
            rows.append({"name": name, "path": "", "version": "", "engine": engine_of(name), "method": "",
                         "why": why, "source": source})
            return
        key = os.path.normcase(os.path.realpath(path))
        if key not in seen:
            seen.add(key)
            rows.append(row(name, path, source, version, run, gd, why, platform))

    for p in cfg.get("extra") or []:
        if os.path.isfile(p):
            add(Path(p).stem, p, "browser_fetch.extra")
    root = (local.get("firefox") or {}).get("root")
    for n, p, s in gorilla_firefox(root) if root else ():
        add(n, p, s)
    if platform.startswith("win"):
        for n, p, s in windows_candidates(windows_registry(), env):
            add(n, p, s)
    elif platform == "darwin":
        for n, p, v, s in mac_candidates():
            add(n, p, s, version=v)
    else:
        for n, p, s, note in linux_candidates():
            add(n, p, s, why=note)
    return rows


def _label(r):
    return f"{r['name']} {Path(r['path']).stem}".lower()


def choose(rows, prefer=(), name=None, engine=None):
    """The order to try: prefer list, Gorilla Firefox, other gecko, chromium family, Safari; only drivable ones.
    name / engine narrow it (name: substring of the name or file name, case-insensitive)."""
    def pri(r):
        for i, pn in enumerate(prefer):
            if pn.lower() in _label(r):
                return (0, i)
        if "gorilla" in r["name"].lower():
            return (1, 0)
        return (2, {"gecko": 0, "chromium": 1, "webkit": 2}.get(r["engine"], 3))
    out = [r for r in rows if r["method"] and (not engine or r["engine"] == engine)
           and (not name or name.lower() in _label(r))]
    return sorted(out, key=pri)
