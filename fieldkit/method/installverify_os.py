"""fieldkit install verify, the Windows and macOS halves (and the remote runs): same rules as installverify.py.

A. Windows (per-user install, `gorilla-opencode install`): the exe on PATH and the exe the Desktop and Start-menu
   shortcuts launch are the same file, sha256 = the release's; the installed .ico holds every frame the release's exe
   embeds (hash per size) and so does the installed exe; both shortcuts exist, point at the exe (directly or through
   conhost.exe) and use the installed .ico; no Explorer icon cache older than the icons; no other copy or shortcut shadows.
B. macOS: the binary on PATH = the one the app bundle runs (when the release ships a bundle), sha256 = the release's;
   the bundle's .icns has the release's sha256; the icon services cache is newer than the icns; no second bundle.
C. Remote: `--peer` asks the paired computer's link listener for the `install-verify` probe; `--host macair` sends this
   one stdlib-only file to the Mac's python3 over ssh with the reference JSON.

Every check reads an environment dict, so the self-test runs all three operating systems' fixtures on any computer.
Python 3.6 syntax only (the Mac runs this file by itself).
"""
import hashlib
import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EXE = "gorilla-opencode.exe"
APP_NAME = "Gorilla OpenCode"


def sha256(p):
    h = hashlib.sha256()
    with open(str(p), "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def row(check, ok, line, fix=""):
    return {"check": check, "ok": ok, "line": line, "fix": fix if not ok else ""}


def same(a, b):
    try:
        return os.path.samefile(str(a), str(b))
    except OSError:
        return False


def mtime(p):
    try:
        return os.path.getmtime(str(p))
    except OSError:
        return 0


def which_all(name, path):
    out = []
    for d in path:
        p = os.path.join(d, name)
        if os.path.isfile(p) and p not in out and not any(same(p, o) for o in out):
            out.append(p)
    return out


# ---- icon containers ------------------------------------------------------------------------------------------------
def frame_hashes(path):
    """-> {size: sha256 of the decoded RGBA pixels} of every frame in an .ico, or in an .exe's first group icon.
    Pixels, not stored bytes: the exe embeds the frames re-encoded (PNG vs BMP) from the .ico the installer writes."""
    from ..icons import iconkit
    frames = iconkit.read_frames(path)
    return {f["size"]: hashlib.sha256(f["image"].convert("RGBA").tobytes()).hexdigest() for f in frames}


def read_lnk(path):
    """-> {target, args, icon} of a Windows shortcut (PowerShell's WScript.Shell), or None."""
    if not sys.platform.startswith("win"):
        return None
    ps = ("$l=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(path).replace("'", "''") + "');"
          "[ordered]@{target=$l.TargetPath;args=$l.Arguments;icon=$l.IconLocation}|ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=60)
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


# ---- Windows --------------------------------------------------------------------------------------------------------
def windows_path():
    """The PATH a new terminal gets: user + machine PATH from the registry, falling back to this process's."""
    try:
        import winreg
        parts = []
        for hive, sub in ((winreg.HKEY_CURRENT_USER, "Environment"),
                          (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            with winreg.OpenKey(hive, sub) as k:
                parts += os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]).split(";")
        return [p for p in parts if p]
    except (ImportError, OSError):
        return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]


def windows_environment(env=None):
    live = env is None
    env = dict(os.environ if live else env)
    local = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roam = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    desk = [Path(env["OneDrive"]) / "Desktop"] if env.get("OneDrive") else []
    desk += [Path.home() / "Desktop"]
    sm = "Microsoft/Windows/Start Menu/Programs"
    pd = Path(env.get("ProgramData") or "C:/ProgramData")
    return {"install_dir": local / "Programs" / APP_NAME,
            "lnk_dirs": desk + [roam / sm, Path(env.get("PUBLIC") or Path(env.get("SystemDrive", "C:") + "/") / "Users" / "Public") / "Desktop", pd / sm],
            "own_lnk": {"desktop": next((d for d in desk if d.is_dir()), desk[-1]), "startmenu": roam / sm},
            "other_dirs": [Path(env.get("ProgramFiles") or "C:/Program Files") / APP_NAME,
                           Path(env.get("ProgramFiles(x86)") or "C:/Program Files (x86)") / APP_NAME,
                           local / "Programs" / "gorilla-opencode"],
            "path": windows_path() if live else [p for p in env.get("PATH", "").split(os.pathsep) if p],
            "cache_files": [local / "IconCache.db"] + sorted((local / "Microsoft" / "Windows" / "Explorer").glob("iconcache_*.db")),
            "read_lnk": read_lnk, "frames": frame_hashes}


def lnk_exe(info):
    """The program a shortcut launches: its target, or the quoted first argument when the target is conhost.exe."""
    t = info.get("target") or ""
    if os.path.basename(t).lower() == "conhost.exe":
        a = (info.get("args") or "").strip()
        return a[1:a.index('"', 1)] if a.startswith('"') and '"' in a[1:] else a.split(" ")[0]
    return t


def verify_windows(ref, e):
    rows = []
    exe = e["install_dir"] / EXE
    ico = e["install_dir"] / "gorilla-opencode.ico"
    want = ref.get("binary_sha")
    onpath = which_all(EXE, e["path"])
    # (a) the exe on PATH and the one each shortcut launches
    lnks = {}
    for kind in ("desktop", "startmenu"):
        p = e["own_lnk"][kind] / (APP_NAME + ".lnk")
        lnks[kind] = (p, e["read_lnk"](p) if p.is_file() else None)
    targets = {k: lnk_exe(v[1]) if v[1] else None for k, v in lnks.items()}
    first = onpath[0] if onpath else None
    ok = bool(first) and all(t and same(first, t) for t in targets.values())
    rows.append(row("binary.path", ok,
                    "PATH first = %s; Desktop shortcut runs %s; Start-menu shortcut runs %s%s"
                    % (first, targets["desktop"], targets["startmenu"],
                       "; also on PATH: " + ", ".join(onpath[1:]) if len(onpath) > 1 else ""),
                    "make PATH, the Desktop and the Start-menu shortcut use one exe: run `fieldkit parity align --apply` "
                    "(it runs `gorilla-opencode install`), remove the older copy from PATH"))
    for p in dict.fromkeys(x for x in [first, *targets.values()] if x):
        got = sha256(p) if os.path.isfile(p) else None
        rows.append(row("binary.sha256", got is not None and got == want,
                        "%s sha256 %s %s the release's %s" % (p, got, "==" if got == want else "!=", want),
                        "reinstall the release's exe: fieldkit parity align --apply"))
    # (c) shortcuts exist, run the installed exe, and use an icon file that holds the release's frames
    want_frames = {int(k): v for k, v in ref.get("exe_icons", {}).items()}

    def frames_of(path):
        try:
            return e["frames"](path) if os.path.isfile(str(path)) else {}
        except Exception as ex:                                # pillow or pefile missing, or a damaged file
            return {"error": str(ex)}

    def gaps(have):
        return [str(k) for k, v in sorted(want_frames.items()) if have.get(k) != v]
    for kind, (p, info) in lnks.items():
        if info is None:
            rows.append(row("shortcut." + kind, False, "%s is missing or unreadable" % p, "run `gorilla-opencode install`"))
            continue
        icon = (info.get("icon") or "").rpartition(",")[0] or info.get("icon") or ""
        miss = gaps(frames_of(icon)) if icon else ["no icon"]
        good = same(lnk_exe(info), exe) and not miss
        rows.append(row("shortcut." + kind, good,
                        "%s runs %s, icon %s%s" % (p, lnk_exe(info), icon, "" if not miss else
                                                    " lacks or differs from the release's frames: " + ", ".join(miss)),
                        "run `gorilla-opencode install` (rewrites both shortcuts and the .ico)"))
    # (b) every frame the release's exe embeds: in the installed .ico and in the installed exe
    if not want_frames:
        rows.append(row("icon.release", False, "the reference holds no icon frames", "rebuild the reference from the release"))
    for label, path in (("ico", ico), ("exe", exe)):
        have = frames_of(path)
        for size, sha in sorted(want_frames.items()):
            rows.append(row("icon.%s:%s" % (label, size), have.get(size) == sha,
                            "%s frame %sx%s: %s the release's" % (path, size, size, "==" if have.get(size) == sha else "!= (or absent)"),
                            "run `gorilla-opencode install` (rewrites the .ico)" if label == "ico"
                            else "reinstall the release's exe: fieldkit parity align --apply"))
    # (d) the Explorer icon caches are not older than the icons
    newest = max([mtime(ico), mtime(lnks["desktop"][0]), mtime(lnks["startmenu"][0])] + [0])
    caches = [c for c in e["cache_files"] if os.path.isfile(str(c))]
    old = [str(c) for c in caches if mtime(c) < newest]
    rows.append(row("cache.icons", not old,
                    "no Explorer icon cache is older than the icons" if not old else "older than the icons: " + ", ".join(old),
                    "fieldkit icons cache --apply (moves the caches to a backup; Explorer rebuilds them)"))
    # (e) no older install or shortcut shadows
    stray = [str(d / EXE) for d in e["other_dirs"] if (d / EXE).is_file() and not same(d / EXE, exe)]
    strays = [str(x) for x in onpath[1:] if not same(x, exe)]
    for d in e["lnk_dirs"]:
        for p in sorted(x for x in Path(d).glob("*.lnk") if "opencode" in x.name.lower()) if Path(d).is_dir() else []:
            if not any(same(p, v[0]) for v in lnks.values()):
                info = e["read_lnk"](p)
                if info and not same(lnk_exe(info), exe):
                    stray.append("%s -> %s" % (p, lnk_exe(info)))
    rows.append(row("shadow.older", not stray and not strays,
                    "no other install, PATH copy or shortcut" if not (stray or strays) else "other copies: " + ", ".join(stray + strays),
                    "recycle them (`fieldkit size recycle PATH`; under Program Files it needs the elevate tool)"))
    return rows


# ---- macOS ----------------------------------------------------------------------------------------------------------
def mac_environment(home=None, env=None):
    env = dict(os.environ if env is None else env)
    home = Path(home or Path.home())
    return {"home": home, "path": [p for p in env.get("PATH", "").split(":") if p],
            "app_dirs": [home / "Applications", Path("/Applications")],
            "cache_dirs": [home / "Library" / "Caches" / "com.apple.iconservices.store",
                           Path("/Library/Caches/com.apple.iconservices.store")]}


def verify_macos(ref, e):
    rows = []
    want = ref.get("binary_sha")
    onpath = which_all("gorilla-opencode", e["path"])
    bundles = [d / (APP_NAME + ".app") for d in e["app_dirs"] if (d / (APP_NAME + ".app")).is_dir()]
    bexec, plist = None, {}
    if bundles:
        try:
            with open(str(bundles[0] / "Contents" / "Info.plist"), "rb") as f:
                plist = plistlib.load(f)
            bexec = bundles[0] / "Contents" / "MacOS" / plist.get("CFBundleExecutable", "")
        except Exception:
            plist = {}
    first = onpath[0] if onpath else None
    if not first:
        rows.append(row("binary.path", False, "`which -a gorilla-opencode` finds nothing on PATH",
                        "fieldkit parity align --apply (installs to ~/.local/bin) and put it on PATH"))
    else:
        ok = bexec is None or same(first, bexec)
        rows.append(row("binary.path", ok, "PATH first = %s%s%s" % (first, "; the app bundle runs %s" % bexec if bexec else "",
                        "; also on PATH: " + ", ".join(onpath[1:]) if len(onpath) > 1 else ""),
                        "make the bundle's executable the PATH binary (link or reinstall) and remove the older copy"))
        got = sha256(first)
        rows.append(row("binary.sha256", got == want, "%s sha256 %s %s the release's %s" % (first, got, "==" if got == want else "!=", want),
                        "fieldkit parity align --apply"))
    if not ref.get("app"):
        rows.append({"check": "app.bundle", "ok": True, "info": True, "fix": "",
                     "line": "the release ships no app bundle or .icns (zip holds the binary only): nothing to compare"})
        return rows
    if not bundles:
        rows.append(row("app.bundle", False, "no %s.app in %s" % (APP_NAME, " or ".join(str(d) for d in e["app_dirs"])),
                        "run `gorilla-opencode install` (builds the bundle)"))
        return rows
    b = bundles[0]
    name = plist.get("CFBundleIconFile", "")
    icns = b / "Contents" / "Resources" / (name if name.endswith(".icns") else name + ".icns")
    got = sha256(icns) if name and icns.is_file() else None
    rows.append(row("icon.icns", got is not None and got == ref["app"].get("icns_sha"),
                    "%s sha256 %s %s the release's %s" % (icns, (got or "absent")[:12], "==" if got == ref["app"].get("icns_sha") else "!=",
                                                          (ref["app"].get("icns_sha") or "")[:12]),
                    "reinstall the bundle: gorilla-opencode install"))
    rows.append(row("app.bundle", bexec is not None and bexec.is_file(),
                    "%s runs %s" % (b, bexec), "reinstall the bundle: gorilla-opencode install"))
    caches = [c for c in e["cache_dirs"] if c.exists()]
    old = [str(c) for c in caches if mtime(c) < mtime(icns)]
    rows.append(row("cache.icons", not old, "the icon services cache is not older than the icns" if not old else "older than the icns: " + ", ".join(old),
                    "touch the bundle and `killall Dock` (macOS rebuilds the icon cache)"))
    others = [str(d / (APP_NAME + ".app")) for d in e["app_dirs"] if (d / (APP_NAME + ".app")).is_dir() and (d / (APP_NAME + ".app")) != b]
    rows.append(row("shadow.older", not others, "no second bundle" if not others else "a second bundle: " + ", ".join(others),
                    "recycle it (`fieldkit size recycle PATH`)"))
    return rows


# ---- the reference: the release itself --------------------------------------------------------------------------------
def reference_from_release(product, tag, repo, folder, os_name, fetch_fn=None):
    """Download the release's Windows exe or macOS zip (checked against SHA256SUMS by fetch) and read what a correct install holds."""
    from .. import parity
    fetch_fn = fetch_fn or parity.fetch_public
    ver = tag.lstrip("v")
    name = parity.ASSETS["windows" if os_name == "windows" else "darwin"].format(v=ver)
    f, sha = fetch_fn(tag, repo, name, Path(folder) / "dl")
    f = Path(f)
    ref = {"tag": tag, "repo": repo, "os": os_name, "asset": name}
    if os_name == "windows":
        ref.update(binary_sha=sha256(f), exe_icons={str(k): v for k, v in frame_hashes(f).items()})
        return ref
    import zipfile
    with zipfile.ZipFile(str(f)) as z:
        member = next((n for n in z.namelist() if Path(n).name == "gorilla-opencode" and not n.endswith("/")), None)
        if not member:
            raise RuntimeError("%s holds no gorilla-opencode binary" % name)
        ref["binary_sha"] = hashlib.sha256(z.read(member)).hexdigest()
        icns = [n for n in z.namelist() if n.endswith(".icns")]
        ref["app"] = {"icns_sha": hashlib.sha256(z.read(icns[0])).hexdigest()} if icns else None
    return ref


# ---- self-test fixtures (every OS, on any computer) --------------------------------------------------------------------
ICO_SIZES = (16, 32, 48, 256)


class WinFake:
    os_name = "windows"

    def __init__(self, root):
        self.root = Path(root)
        self.frames = {s: hashlib.sha256(("frame%d" % s).encode()).hexdigest() for s in ICO_SIZES}
        self.exe_bytes = b"MZ-release-exe"
        self.install = self.root / "local" / "Programs" / APP_NAME
        self.desk = self.root / "home" / "Desktop"
        self.sm = self.root / "roam" / "Start Menu"
        self.cache = self.root / "local" / "IconCache.db"
        self.put(self.install / EXE, self.exe_bytes)
        self.put(self.install / "gorilla-opencode.ico", json.dumps(self.frames).encode())
        for d in (self.desk, self.sm):
            self.lnk(d / (APP_NAME + ".lnk"), self.install / EXE, self.install / "gorilla-opencode.ico")
        self.put(self.cache, b"c")
        os.utime(str(self.cache), (time.time() + 10,) * 2)

    def put(self, p, data):
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def lnk(self, p, exe, ico, via_conhost=True):
        info = ({"target": str(self.root / "conhost.exe"), "args": '"%s"' % exe, "icon": "%s,0" % ico} if via_conhost
                else {"target": str(exe), "args": "", "icon": "%s,0" % ico})
        self.put(p, json.dumps(info).encode())

    @property
    def ref(self):
        return {"binary_sha": hashlib.sha256(self.exe_bytes).hexdigest(),
                "exe_icons": {str(s): d for s, d in self.frames.items()}}

    @property
    def env(self):
        known = {self.exe_bytes: self.frames}

        def frames(p):
            if str(p).endswith(".ico"):
                return {int(k): v for k, v in json.loads(Path(p).read_text()).items()}
            return known.get(Path(p).read_bytes(), {})
        return {"install_dir": self.install, "lnk_dirs": [self.desk, self.sm, self.root / "public"],
                "own_lnk": {"desktop": self.desk, "startmenu": self.sm}, "other_dirs": [self.root / "pf" / APP_NAME],
                "path": [str(self.install), str(self.root / "oldbin")], "cache_files": [self.cache],
                "read_lnk": lambda p: json.loads(Path(p).read_text()),
                "frames": frames}

    def verify(self):
        return verify_windows(self.ref, self.env)


def _w_old_exe(f):
    f.put(f.install / EXE, b"MZ-old")


def _w_ico_old(f):
    f.put(f.install / "gorilla-opencode.ico", json.dumps({**f.frames, 256: "old"}).encode())


def _w_ico_missing_size(f):
    f.put(f.install / "gorilla-opencode.ico", json.dumps({s: d for s, d in f.frames.items() if s != 48}).encode())


def _w_no_desktop(f):
    (f.desk / (APP_NAME + ".lnk")).unlink()


def _w_no_startmenu(f):
    (f.sm / (APP_NAME + ".lnk")).unlink()


def _w_lnk_other_exe(f):
    f.put(f.root / "elsewhere" / EXE, f.exe_bytes)
    f.lnk(f.sm / (APP_NAME + ".lnk"), f.root / "elsewhere" / EXE, f.install / "gorilla-opencode.ico")


def _w_lnk_old_icon(f):
    f.put(f.root / "old.ico", json.dumps({16: "x"}).encode())
    f.lnk(f.desk / (APP_NAME + ".lnk"), f.install / EXE, f.root / "old.ico")


def _w_lnk_good_other_icon(f):
    f.put(f.root / "mine.ico", json.dumps(f.frames).encode())
    f.lnk(f.desk / (APP_NAME + ".lnk"), f.install / EXE, f.root / "mine.ico")


def _w_path_shadow(f):
    f.put(f.root / "oldbin" / EXE, b"MZ-old")


def _w_stale_cache(f):
    os.utime(str(f.cache), (time.time() - 7200,) * 2)


def _w_old_install_dir(f):
    f.put(f.root / "pf" / APP_NAME / EXE, b"MZ-ancient")


def _w_public_lnk(f):
    f.put(f.root / "pf" / APP_NAME / EXE, b"MZ-ancient")
    f.lnk(f.root / "public" / "Gorilla OpenCode old.lnk", f.root / "pf" / APP_NAME / EXE, f.root / "pf" / "x.ico")


def _w_embedded_icon(f):
    f.put(f.install / EXE, b"MZ-release-exe-but-icon-differs")


WIN_BREAKS = [
    ("exe differs from the release's", _w_old_exe, "binary.sha256"),
    ("installed .ico has an old 256 frame", _w_ico_old, "icon.ico:256"),
    ("installed .ico lacks the 48 frame", _w_ico_missing_size, "icon.ico:48"),
    ("Desktop shortcut missing", _w_no_desktop, "shortcut.desktop"),
    ("Start-menu shortcut missing", _w_no_startmenu, "shortcut.startmenu"),
    ("a shortcut runs another exe", _w_lnk_other_exe, "binary.path"),
    ("a shortcut uses an old icon", _w_lnk_old_icon, "shortcut.desktop"),
    ("a second exe on PATH", _w_path_shadow, "shadow.older"),
    ("Explorer icon cache older than the icons", _w_stale_cache, "cache.icons"),
    ("an older install in Program Files", _w_old_install_dir, "shadow.older"),
    ("an old all-users shortcut", _w_public_lnk, "shadow.older"),
    ("the exe's embedded icon is not the release's", _w_embedded_icon, "icon.exe"),
]


class MacFake:
    os_name = "macos"

    def __init__(self, root):
        self.root = Path(root)
        self.bin = self.root / "home" / ".local" / "bin"
        self.binary = b"\xcf\xfa-mach-release"
        self.icns = b"icns-release" * 20
        self.app = self.root / "home" / "Applications" / (APP_NAME + ".app")
        self.cache = self.root / "home" / "Library" / "Caches" / "com.apple.iconservices.store"
        self.put(self.bin / "gorilla-opencode", self.binary)
        os.chmod(str(self.bin / "gorilla-opencode"), 0o755)
        (self.app / "Contents" / "MacOS").mkdir(parents=True)
        os.link(str(self.bin / "gorilla-opencode"), str(self.app / "Contents" / "MacOS" / "gorilla-opencode"))
        (self.app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "gorilla-opencode",
                                                                           "CFBundleIconFile": "gorilla-opencode"}))
        self.put(self.app / "Contents" / "Resources" / "gorilla-opencode.icns", self.icns)
        self.cache.mkdir(parents=True)
        os.utime(str(self.cache), (time.time() + 10,) * 2)

    def put(self, p, data):
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    @property
    def ref(self):
        return {"binary_sha": hashlib.sha256(self.binary).hexdigest(), "app": {"icns_sha": hashlib.sha256(self.icns).hexdigest()}}

    @property
    def env(self):
        return {"home": self.root / "home", "path": [str(self.bin), str(self.root / "usr" / "local" / "bin")],
                "app_dirs": [self.root / "home" / "Applications", self.root / "Applications"], "cache_dirs": [self.cache]}

    def verify(self):
        return verify_macos(self.ref, self.env)


def _m_binary(f):
    f.put(f.bin / "gorilla-opencode", b"old")


def _m_no_bundle(f):
    shutil.rmtree(str(f.app))


def _m_old_icns(f):
    f.put(f.app / "Contents" / "Resources" / "gorilla-opencode.icns", b"old icns")


def _m_bundle_other_exe(f):
    link = f.app / "Contents" / "MacOS" / "gorilla-opencode"
    link.unlink()
    f.put(link, f.binary)


def _m_stale_cache(f):
    os.utime(str(f.cache), (time.time() - 7200,) * 2)


def _m_second_bundle(f):
    shutil.copytree(str(f.app), str(f.root / "Applications" / (APP_NAME + ".app")))


def _m_path_shadow(f):
    f.put(f.root / "usr" / "local" / "bin" / "gorilla-opencode", b"old")
    os.chmod(str(f.root / "usr" / "local" / "bin" / "gorilla-opencode"), 0o755)
    (f.bin / "gorilla-opencode").unlink()
    os.link(str(f.root / "usr" / "local" / "bin" / "gorilla-opencode"), str(f.bin / "gorilla-opencode"))


MAC_BREAKS = [
    ("binary differs from the release's", _m_binary, "binary.sha256"),
    ("app bundle missing", _m_no_bundle, "app.bundle"),
    ("the .icns is not the release's", _m_old_icns, "icon.icns"),
    ("the bundle runs another file than PATH's", _m_bundle_other_exe, "binary.path"),
    ("icon services cache older than the icns", _m_stale_cache, "cache.icons"),
    ("a second bundle in /Applications", _m_second_bundle, "shadow.older"),
    ("PATH's binary is an older copy", _m_path_shadow, "binary.sha256"),
]


def self_test(which=("windows", "macos")):
    results = []
    cases = {"windows": (WinFake, WIN_BREAKS), "macos": (MacFake, MAC_BREAKS)}
    with tempfile.TemporaryDirectory(prefix="fk-install-selftest-os-") as t:
        for osn in which:
            fake, breaks = cases[osn]
            f = fake(Path(t) / osn / "base")
            bad = [r["check"] for r in f.verify() if not r["ok"]]
            results.append({"case": "%s: a correct install passes" % osn, "ok": not bad, "detail": bad})
            for i, (name, seed, check) in enumerate(breaks):
                f = fake(Path(t) / osn / ("case%d" % i))
                seed(f)
                failed = [r["check"] for r in f.verify() if not r["ok"]]
                caught = any(c.startswith(check) for c in failed)
                results.append({"case": "%s: caught: %s" % (osn, name), "ok": caught, "detail": [] if caught else failed})
    return results


# ---- remote runs ------------------------------------------------------------------------------------------------------
def verify_here(os_name, ref):
    """-> rows for THIS computer in the macOS or Windows half (used by the Mac's python3 and by the peer probe)."""
    return verify_windows(ref, windows_environment()) if os_name == "windows" else verify_macos(ref, mac_environment())


def ssh_run(host, ref, ssh=None, run=subprocess.run):
    """Run this file on a Mac over ssh (its python3, stdin = the script, the reference as argv). -> (rows or None, why)."""
    if ssh is None:
        import shlex
        from ..mac import macssh
        try:
            mac = macssh.get(macssh.load(), host)
        except SystemExit as e:
            return None, str(e).splitlines()[0]
        addr, how = macssh.find(mac)
        if not addr:
            return None, "the Mac is not reachable: " + how
        argv = macssh.ssh_argv(mac, addr, "python3 - " + shlex.quote(json.dumps(ref)))
    else:
        argv = ssh(ref)
    try:
        r = run(argv, input=Path(__file__).read_text(encoding="utf-8"), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "ssh failed: %s" % e
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]), ""
    except (IndexError, ValueError):
        return None, "ssh to %s: exit %s, no result (is python3 on the Mac?): %s" % (host, r.returncode, (r.stderr or r.stdout)[-200:])


if __name__ == "__main__":        # run on the Mac: python3 - 'REF_JSON' < installverify_os.py
    print(json.dumps(verify_macos(json.loads(sys.argv[1]), mac_environment())))
