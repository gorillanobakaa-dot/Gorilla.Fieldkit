"""Install a verified build on this machine, with a backup first and a way back.

Order of events, every one measured and journaled:
  1. backup   - the installed browser (install dir) and the profiles, zipped into
                ~/Documents/Gorilla.Firefox.Backups/<version>-<stamp>/ with a manifest (sha256, sizes, application.ini
                version, profiles.ini). Documents exists on Windows and Linux alike, so the backup lives in the same
                place on both.
  2. install  - the packaged installer, silently, into the SAME directory the current build uses (a per-user
                directory needs no elevation: C:\\Users\\<you>\\Gorilla Unleashed). Firefox must not be running.
  3. verify   - application.ini Version and `firefox.exe --version` must say the pinned version; a zip install
                also writes its own marker (gorilla-install.json, a fresh nonce) and the row checks THAT, so a
                stale uninstall entry of an earlier installer can never pass it; the installer route checks the
                uninstall registry entry.
  4. restore  - the backup unpacked over the install dir, when asked (`install --restore <backup dir>`), and the
                profiles from profiles.zip when the backup has one: the current profiles folder is first moved
                aside (kept, never deleted) as <folder>.before-restore-<stamp>.
Profiles are never touched by install; they are backed up because an upgrade migrates them in place.

The install target, with no --install-dir, is the ONE uninstall entry whose display name or install folder name
says Gorilla. An ordinary Mozilla Firefox is never picked; none or several -> refused with a NEXT hint.
"""
import configparser
import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import task

BACKUPS = Path.home() / "Documents" / "Gorilla.Firefox.Backups"


def documents():
    """~/Documents on Windows and Linux (XDG user-dirs honoured when present)."""
    if sys.platform != "win32":
        cfg = Path.home() / ".config" / "user-dirs.dirs"
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("XDG_DOCUMENTS_DIR="):
                    return Path(os.path.expandvars(line.split("=", 1)[1].strip().strip('"')))
    return Path.home() / "Documents"


def installed(install_dir):
    """-> {version, build_id, name} from application.ini, or None."""
    ini = Path(install_dir) / "application.ini"
    if not ini.is_file():
        return None
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")
    return {"version": cp.get("App", "Version", fallback=None), "build_id": cp.get("App", "BuildID", fallback=None),
            "name": cp.get("App", "CodeName", fallback=cp.get("App", "Name", fallback=None))}


def _uninstall_entries():
    """[(DisplayName, InstallLocation)] of every Windows uninstall entry (both hives); read only."""
    if sys.platform != "win32":
        return []
    import winreg
    out = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            root = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                name = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue
            try:
                loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
            except OSError:
                loc = ""
            out.append((str(name or ""), str(loc or "")))
    return out


def gorilla_installs(entries=None):
    """Install folders whose uninstall entry says Gorilla in its name or in the install folder's own name (not
    anywhere in the path: an account named, say, gorillafan puts Gorilla in every path under its home folder, so ordinary Mozilla Firefox would match)
    and that hold a firefox.exe, without duplicates. An entry that only says Firefox is never one."""
    found, seen = [], set()
    for name, loc in (_uninstall_entries() if entries is None else entries):
        if not loc:
            continue
        p = Path(loc)
        if "gorilla" not in name.lower() and "gorilla" not in p.name.lower():
            continue
        if not (p / "firefox.exe").is_file():
            continue
        key = os.path.normcase(str(p.resolve()))
        if key not in seen:
            seen.add(key)
            found.append(p)
    return found


def find_install():
    """The ONE registered Gorilla install, or None (none, or several: `no_target()` says which and what to do)."""
    found = gorilla_installs()
    return found[0] if len(found) == 1 else None


def no_target():
    """The refusal when no --install-dir was given and the registry does not name exactly one Gorilla install."""
    found = gorilla_installs()
    if not found:
        why = "no uninstall entry names a Gorilla install (an entry that only says Firefox is never picked)"
    else:
        why = f"{len(found)} Gorilla installs are registered, not guessing which: {[str(f) for f in found]}"
    return {"ok": False, "why": why, "next": "NEXT: pass --install-dir <the Gorilla install folder>"}


MARKER = "gorilla-install.json"


def new_marker(zip_path, zip_sha, version, task_id):
    return {"by": "fieldkit build-harness install", "nonce": secrets.token_hex(16), "zip": Path(zip_path).name,
            "zip_sha256": zip_sha, "version": version, "task": task_id, "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def marker_row(install_dir, marker):
    """This install's own record: the marker the zip install wrote, with this run's nonce and zip hash."""
    try:
        got = json.loads((Path(install_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        got = None
    ok = bool(got) and got.get("nonce") == marker["nonce"] and got.get("zip_sha256") == marker["zip_sha256"]
    return {"check": f"this install's own marker ({MARKER}) names this run and this zip", "ok": ok,
            "evidence": (f"{marker['zip']} sha256 {marker['zip_sha256'][:12]}..., nonce matches" if ok
                         else f"marker {'missing' if got is None else 'from another run: ' + str(got.get('zip'))}")}


def profiles_dir():
    return Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox" if sys.platform == "win32" else Path.home() / ".mozilla" / "firefox"


def running(install_dir):
    exe = str(Path(install_dir) / "firefox.exe").lower()
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process firefox -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
                       capture_output=True, text=True, errors="replace")
    return [l.strip() for l in (r.stdout or "").splitlines() if l.strip().lower() == exe]


def _zip_dir(src, dest):
    src = Path(src)
    n = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in src.rglob("*"):
            if p.is_file():
                try:
                    z.write(p, p.relative_to(src).as_posix())
                    n += 1
                except OSError:
                    pass                                   # a lock file of a background task: not part of a backup
    return n


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def backup(install_dir, profiles=None, dest_root=None, say=print):
    """-> backup directory. Zips the install dir and the profiles dir, writes manifest.json."""
    info = installed(install_dir) or {"version": "unknown"}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(dest_root or (documents() / "Gorilla.Firefox.Backups")) / f"{info['version']}-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {"taken": time.strftime("%Y-%m-%d %H:%M:%S"), "install_dir": str(install_dir), "installed": info, "files": {}}
    t0 = time.time()
    inst_zip = dest / f"install-{info['version']}.zip"
    n = _zip_dir(install_dir, inst_zip)
    manifest["files"]["install"] = {"zip": inst_zip.name, "entries": n, "bytes": inst_zip.stat().st_size, "sha256": _sha(inst_zip)}
    say(f"  backup: install dir -> {inst_zip.name} ({n} files, {inst_zip.stat().st_size // 2**20} MB, {time.time() - t0:.0f} s)")
    prof = Path(profiles) if profiles else profiles_dir()
    if prof.is_dir():
        t0 = time.time()
        prof_zip = dest / "profiles.zip"
        n = _zip_dir(prof, prof_zip)
        manifest["files"]["profiles"] = {"zip": prof_zip.name, "entries": n, "bytes": prof_zip.stat().st_size, "sha256": _sha(prof_zip),
                                         "source": str(prof)}
        say(f"  backup: profiles -> profiles.zip ({n} files, {prof_zip.stat().st_size // 2**20} MB, {time.time() - t0:.0f} s)")
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return dest


def local_profiles():
    """Every profile's LOCAL directory (startupCache lives there), from profiles.ini; Windows and Linux."""
    out = []
    ini = profiles_dir() / "profiles.ini"
    if not ini.is_file():
        return out
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(ini, encoding="utf-8")
    local_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla" / "Firefox" if sys.platform == "win32" else Path.home() / ".cache" / "mozilla" / "firefox"
    for sec in cp.sections():
        path = cp.get(sec, "Path", fallback=None)
        if path:
            out.append(local_root / path if cp.get(sec, "IsRelative", fallback="1") == "1" else Path(path))
    return out


def blocking_firefox(paths, temp=None):
    """The running firefox.exe paths that can hold a real profile's startup cache. Throwaway copies under the temp
    folder (netbench, probe, leak-gate runs) use their own throwaway profiles and do not count: 2026-10-04 the
    install of build 23 was refused because two bench browsers were running from %TEMP%/gnetbench_*."""
    temp = str(Path(temp or tempfile.gettempdir()).resolve()).lower()
    return [x.strip() for x in paths if x.strip() and not str(Path(x.strip()).resolve()).lower().startswith(temp)]


def clear_startup_caches(say=print):
    """Delete every profile's startupCache. Firefox keys that cache on the BuildID; two different builds with one
    BuildID (02 Oct, builds 2-6) left the owner's profile running the broken build's compiled scripts while a
    fresh profile ran the new one. -> [cleared dirs]. Refuses while Firefox runs."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process firefox -ErrorAction SilentlyContinue | ForEach-Object { $_.Path }"],
                       capture_output=True, text=True, errors="replace")
    users = blocking_firefox(r.stdout.splitlines())
    if users:
        raise task.Refused(f"Firefox is running ({users[0]}): close it before the startup caches are cleared")
    cleared = []
    for prof in local_profiles():
        sc = prof / "startupCache"
        if sc.is_dir():
            n = sum(1 for _ in sc.rglob("*") if _.is_file())
            shutil.rmtree(sc, ignore_errors=True)
            cleared.append(f"{sc} ({n} files)")
            say(f"  startup cache cleared: {sc.parent.name} ({n} files)")
    return cleared


ICONKIT = Path.home() / "Documents" / "Scripts" / "IconKit" / "iconkit.py"


def desktop_icons_row(say=print, apply=True):
    """Desktop icons sharp after an install. Every install replaces firefox.exe, whose icons Explorer's icon cache keeps,
    so the Gorilla shortcut (and others) go soft (2026-10-04, maintainer: "everytime you install or do stuff that messes
    around with the icon cache you need to check"). IconKit's `cache --apply` rebuilds the cache: Explorer restarts after
    a countdown (taskbar and File Explorer windows close and come back; programs and unsaved work are untouched), then
    `cache` checks that every desktop icon file holds a sharp frame at the desktop size. Announce installs in chat."""
    check = "desktop icons: icon cache rebuilt, every desktop icon file sharp"
    if os.name != "nt":
        return {"check": check, "ok": True, "evidence": "not Windows: nothing to do"}
    if not ICONKIT.is_file():
        return {"check": check, "ok": False, "evidence": f"IconKit missing ({ICONKIT}): cache not rebuilt; icons may be soft"}
    if apply:
        say("  desktop icons: rebuilding Explorer's icon cache (IconKit; Explorer restarts after a countdown)")
        subprocess.run([sys.executable, str(ICONKIT), "cache", "--apply"], capture_output=True, text=True, timeout=300)
    r = subprocess.run([sys.executable, str(ICONKIT), "cache"], capture_output=True, text=True, errors="replace", timeout=120)
    # IconKit marks each desktop icon "ok", "FILE" (the icon file lacks a sharp frame) or "????" (unreadable)
    bad = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(("FILE", "????"))]
    seen = [l for l in r.stdout.splitlines() if l.strip().startswith("ok ")]
    verdict = next((l for l in r.stdout.splitlines() if l.startswith("VERDICT")), "")
    return {"check": check, "ok": bool(seen) and not bad,
            "evidence": ("cache rebuilt; " if apply else "") + f"{len(seen)} icon(s) sharp" +
            (f"; not sharp: {bad[:3]}" if bad else "") + (f"; {verdict[9:90]}" if verdict and bad else "")}


def caches_row():
    left = [str(p / "startupCache") for p in local_profiles() if (p / "startupCache").is_dir() and any((p / "startupCache").iterdir())]
    return {"check": "profiles: no stale startup cache from an earlier build", "ok": not left,
            "evidence": "every profile's startupCache is empty" if not left else f"{len(left)} profile(s) still cached: {left[:2]}"}


def install_from_zip(zip_path, install_dir, say=print, marker=None):
    """The packaged zip (one top-level folder) unpacked into `install_dir`, which is emptied first. Deterministic:
    no installer heuristics, no elevation. 02 Oct: the NSIS installer, silent, into an empty directory with a stale
    per-machine uninstall entry around, went the elevated route, could not, and exited 0 having installed nothing.
    With `marker`, writes it as gorilla-install.json once every file is in place (verify checks it).
    -> (0, seconds)."""
    import zipfile
    if running(install_dir):
        raise task.Refused(f"Firefox is running from {install_dir}: close it first")
    t0 = time.time()
    dest = Path(install_dir)
    if dest.is_dir():
        shutil.rmtree(dest)
        say(f"  install: removed the old {dest} (backed up first)")
    dest.mkdir(parents=True)
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        names = [x for x in z.namelist() if not x.endswith("/")]
        top = names[0].split("/")[0]
        for x in names:
            if not x.startswith(top + "/"):
                continue
            target = (dest / x[len(top) + 1:]).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise task.Refused(f"{Path(zip_path).name}: entry {x!r} would land outside {dest}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(x) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    if marker:
        (dest / MARKER).write_text(json.dumps(marker, indent=1), encoding="utf-8")
    say(f"  install: {n} files from {Path(zip_path).name} into {dest} ({time.time() - t0:.0f} s)")
    return 0, time.time() - t0


def install(installer, install_dir, say=print, timeout=1800, fresh=True):
    """Run the packaged installer silently into `install_dir` (per-user: no elevation). With `fresh` the old
    install directory is removed first (the backup was taken before), so nothing of an earlier build lingers.
    -> (rc, seconds)."""
    if running(install_dir):
        raise task.Refused(f"Firefox is running from {install_dir}: close it first")
    if fresh and Path(install_dir).is_dir():
        shutil.rmtree(install_dir)
        say(f"  install: removed the old {install_dir} (backed up first)")
    t0 = time.time()
    # the Firefox installer: a 7-Zip SFX that unpacks and runs setup.exe; /S silent, /InstallDirectoryPath= exact dir
    cmd = [str(installer), "/S", f"/InstallDirectoryPath={install_dir}"]
    say(f"  install: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout)
    # the SFX returns when setup.exe exits; give the shell a moment to settle registry writes
    time.sleep(3)
    return r.returncode, time.time() - t0


def verify(install_dir, version, marker=None):
    """-> rows [{check, ok, evidence}] for the installed build. With `marker` (a zip install) the last row is the
    install's own marker; without, the uninstall registry entry (the installer writes it)."""
    rows = []
    info = installed(install_dir)
    rows.append({"check": "application.ini says the pinned version", "ok": bool(info) and info["version"] == version,
                 "evidence": str(info)})
    exe = Path(install_dir) / "firefox.exe"
    out = ""
    if exe.is_file():
        try:
            out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=60, errors="replace").stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as e:
            out = f"failed: {e}"
    rows.append({"check": "installed firefox.exe reports the pinned version", "ok": version in out, "evidence": out or "no firefox.exe"})
    if marker:
        rows.append(marker_row(install_dir, marker))
        return rows
    reg = find_install()
    rows.append({"check": "the uninstall entry points at this install", "ok": reg is not None and Path(reg).resolve() == Path(install_dir).resolve(),
                 "evidence": str(reg)})
    return rows


def any_firefox_running():
    """Any Firefox at all (an ordinary Mozilla Firefox shares the profiles folder)."""
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-Command", "Get-Process firefox -ErrorAction SilentlyContinue | Select-Object -First 1"]
    else:
        cmd = ["pgrep", "-x", "firefox"]
    try:
        return bool(subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout.strip())
    except OSError:
        return True                                    # cannot tell: treat as running (refuse)


def restore(backup_dir, install_dir, say=print, profiles=None):
    """Unpack install-<ver>.zip over `install_dir` (the dir is emptied first) and, when the backup has one,
    profiles.zip into the profiles folder it was taken from: the current profiles folder is moved aside first
    (kept as <folder>.before-restore-<stamp>, never deleted). Everything is checked before anything is touched:
    hashes, Firefox not running, the profiles folder the same one the backup came from - else refused, with what
    to unzip by hand."""
    if install_dir is None:
        raise task.Refused(no_target()["why"] + "; " + no_target()["next"])
    if running(install_dir):
        raise task.Refused(f"Firefox is running from {install_dir}: close it first")
    man = json.loads((Path(backup_dir) / "manifest.json").read_text(encoding="utf-8"))
    z = Path(backup_dir) / man["files"]["install"]["zip"]
    if _sha(z) != man["files"]["install"]["sha256"]:
        raise task.Refused(f"{z.name} does not match its manifest hash: not restoring from a damaged backup")
    plan = None
    pe = man["files"].get("profiles")
    if pe:
        pz = Path(backup_dir) / pe["zip"]
        target = Path(profiles) if profiles else profiles_dir()
        src = pe.get("source")
        if src and os.path.normcase(str(Path(src).resolve())) != os.path.normcase(str(target.resolve())):
            raise task.Refused(f"{pz.name} was taken from {src}, not {target}: nothing restored. To put the profiles "
                               f"back by hand: close every Firefox, rename {src} to keep it, then unzip {pz} into {src}")
        if _sha(pz) != pe["sha256"]:
            raise task.Refused(f"{pz.name} does not match its manifest hash: not restoring from a damaged backup")
        if any_firefox_running():
            raise task.Refused("a Firefox is running and may hold the profiles: close every Firefox first")
        plan = (pz, target)
    inst = Path(install_dir)
    if inst.exists():
        shutil.rmtree(inst)
    inst.mkdir(parents=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(inst)
    say(f"  restored {z.name} into {inst}")
    if plan:
        restore_profiles(*plan, say=say)
    return installed(inst)


def restore_profiles(prof_zip, target, say=print):
    """Move the current profiles folder aside (kept), unzip the backup into a fresh one; on any failure the
    half-written folder (ours) goes and the kept one is put back. -> the folder the old profiles were kept in."""
    target = Path(target)
    aside = target.with_name(f"{target.name}.before-restore-{time.strftime('%Y%m%d-%H%M%S')}")
    if target.exists():
        target.rename(aside)
        say(f"  current profiles kept as {aside}")
    try:
        target.mkdir(parents=True)
        with zipfile.ZipFile(prof_zip) as zf:
            zf.extractall(target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        if aside.exists():
            aside.rename(target)
        raise
    say(f"  restored {Path(prof_zip).name} into {target}")
    return aside


def run(task_id, do_backup=True, say=print, install_dir=None, use_installer=False):
    """The whole step: backup -> install -> verify, journaled. -> {"ok", "backup", "rows"}."""
    t = task.load(task_id)
    res = json.loads((task.STATE / task_id / "build-result.json").read_text(encoding="utf-8"))
    installer = Path(res["artifacts"]["installer"]["file"])
    if _sha(installer) != res["artifacts"]["installer"]["sha256"]:
        return {"ok": False, "why": "the installer on disk is not the one build-verify hashed"}
    zip_path = Path(res["artifacts"]["zip"]["file"]) if res["artifacts"].get("zip") else None
    if zip_path and _sha(zip_path) != res["artifacts"]["zip"]["sha256"]:
        return {"ok": False, "why": "the zip on disk is not the one build-verify hashed"}
    version = t["meta"]["upstream"]["version"]
    target = Path(install_dir) if install_dir else find_install()
    if not target:
        return no_target()
    before = installed(target)
    say(f"  installed now: {before} at {target}")
    bdir = None
    if do_backup:
        bdir = backup(target, say=say)
        task.journal(t, "backup", dest=str(bdir), installed=before)
    marker = None
    if zip_path and not use_installer:
        marker = new_marker(zip_path, res["artifacts"]["zip"]["sha256"], version, task_id)
        rc, secs = install_from_zip(zip_path, target, say=say, marker=marker)
    else:
        rc, secs = install(installer, target, say=say)
    cleared = clear_startup_caches(say=say)
    rows = verify(target, version, marker=marker) + [caches_row(), desktop_icons_row(say=say)]
    ok = rc == 0 and all(r["ok"] for r in rows)
    task.journal(t, "install", installer=str(installer), target=str(target), rc=rc, seconds=round(secs), ok=ok,
                 rows=[(r["check"], r["ok"]) for r in rows], backup=str(bdir) if bdir else None, caches_cleared=cleared)
    for r in rows:
        say(f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence'][:140]}")
    return {"ok": ok, "backup": str(bdir) if bdir else None, "rows": rows, "rc": rc, "target": str(target)}


# ---------------------------------------------------------------- post-install: the owner's checks against the new install
# Each script takes --install <dir>; two need --yes because they open a real window / make a local call.
# They are the owner's (Gorilla.firefox/working scripts); this only runs them in order and keeps the output.
POST_INSTALL = (
    ("verify_installed_build", ["--install", "{dir}"]),        # the installed omni.ja carries every tracked fix
    ("verify_no_phone_home", ["--install", "{dir}"]),          # clean profile, 60 s, socket table: nothing contacted
    ("webrtc_selftest", ["--install", "{dir}", "--yes"]),       # headless loopback call: ICE, DTLS 1.2, SCTP, RTP
    ("verify_address_bar", ["--install", "{dir}", "--yes"]),    # real window: typing an address navigates (title changes)
)
# the production proof rows post-install runs before the owner's scripts (names usable with --only)
PROOF_CHECKS = frozenset({"prefs", "excised", "startup", "egress", "adblock", "leaks", "decisions", "visual", "claims", "ui",
                          "about", "stamp", "satellite"})
# checks that take the keyboard / foreground: never run unless asked with --drive, and announced with a countdown first
DRIVES_WINDOW = {"verify_address_bar"}
DRIVE_COUNTDOWN = 20


STARTUP_ERROR = re.compile(r"JavaScript error:.*(SyntaxError|No such JSWindowActor|ReferenceError|is not defined)")


def startup_errors(install_dir, seconds=25, say=print):
    """Start the installed browser HEADLESS on a throwaway profile and read its stderr: a module that fails to parse
    or an actor that never registered shows up here within seconds, with no window and no keyboard. 2026-10-02: the
    address bar check needed four keyboard-taking runs to find what one line of stderr said at startup:
    `DesktopActorRegistry.sys.mjs, line 242: SyntaxError`. -> {ok, lines, log}."""
    from . import throwaway
    exe = Path(install_dir) / "firefox.exe"
    prof = throwaway.profile("gstartup_", throwaway.USER_JS + 'user_pref("devtools.console.stdout.chrome", true);\n')
    log = prof / "stderr.txt"
    with open(log, "wb") as err, open(prof / "stdout.txt", "wb") as out:
        proc = subprocess.Popen([str(exe), "-headless", "-no-remote", "-profile", str(prof), "about:blank"], stdout=out, stderr=err)
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass
        # only the process this check started, with its own children
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    text = log.read_text(encoding="utf-8", errors="replace") + (prof / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    lines = sorted({l.strip()[:220] for l in text.splitlines() if STARTUP_ERROR.search(l)})
    log = throwaway.keep(prof, "stderr.txt", "stdout.txt") / "stderr.txt"
    throwaway.discard(prof)
    say(f"  [{'ok' if not lines else 'FAIL'}] headless startup: {len(lines)} module/actor error line(s)" + (f": {lines[0]}" if lines else ""))
    return {"ok": not lines, "lines": lines, "log": str(log)}


def scripts_dir():
    return documents() / "Gorilla.firefox" / "working scripts"


def _decisions_row(t, target):
    from . import buildrun, decisions
    return [decisions.proof_row(buildrun._owner_root(t), t["workdir"], target)]


def _claims_row(t, target):
    """What the public repository claims, against this tree and this build (fieldkit/buildh/claims.py; read-only:
    the register and the report are written only by `build-harness claims`)."""
    from . import claims
    return [claims.proof_row(t, target)]


def _visual_row(t, target, say=print):
    """Crisp icons and aligned pages: the static tree and a throwaway copy of `target` (fieldkit/visual)."""
    from .. import visual
    return [visual.proof_row(t, target, say=say)]


def _ui_rows(t, target, say=print):
    """Readable, working menus and Gorilla Settings controls (fieldkit buildh/uicheck.py; born 2026-10-04)."""
    from . import uicheck
    return uicheck.rows(t["workdir"], target, say=say)


def _stamp_rows(task_id, target, say=print):
    """The installed browser is the build build-verify recorded, and Help > About shows its stamp (D-157-38)."""
    import json as _json
    from . import buildstamp
    res = task.STATE / task_id / "build-result.json"
    recorded = None
    if res.is_file():
        recorded = _json.loads(res.read_text(encoding="utf-8")).get("build_id") or ""
    return buildstamp.about_rows(target, recorded=recorded, say=say)


def _satellite_rows(target, say=print):
    """Satellite mode on a copy of the install: identity and scripts per level and per kind of site (satellite.py)."""
    from . import satellite
    return satellite.rows(target, say=say)


def _about_rows(t, target, say=print):
    """Every about: page read and judged on a copy of the install (fieldkit buildh/aboutpages.py; born 2026-10-08),
    and held against the owner's reviewed register of pages (D-157-40)."""
    from . import aboutpages
    return aboutpages.rows(target, (installed(target) or {}).get("build_id"), say=say, register=aboutpages.register_for(t))


def post_install(task_id, install_dir=None, only=None, say=print, timeout=900, drive=False, sleep=time.sleep):
    """Run the owner's post-install checks against the installed build. -> {"ok", "results": [{name, rc, status, log}],
    "skipped"}. Fails closed: OK only when every requested check (all of them, or the --only names) RAN and passed;
    a check skipped (keyboard without --drive), a missing owner script or an --only name that is no check is
    SKIPPED, and SKIPPED is never OK."""
    t = task.load(task_id)
    target = Path(install_dir) if install_dir else find_install()
    if not target:
        return no_target()
    if running(target):
        raise task.Refused(f"Firefox is running from {target}: the checks need to start it themselves")
    logs = task.STATE / task_id / "post-install"
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    # production proof first: the shipped artefacts and a headless start, no window, no keyboard
    from . import proof, firefox
    want = PROOF_CHECKS & (only or PROOF_CHECKS)
    for name in sorted((only or set()) - PROOF_CHECKS - {n for n, _ in POST_INSTALL}):
        results.append({"name": name, "rc": None, "status": "SKIPPED", "log": None, "why": "no check has this name"})
        say(f"  [SKIPPED] {name}: no check has this name (proof: {sorted(PROOF_CHECKS)}; scripts: {[n for n, _ in POST_INSTALL]})")
    if want:
        deleted = firefox.manifest_deletions(t["meta"]["harness_root"]) if t.get("meta", {}).get("harness_root") else []
        from . import verify as vf
        truth = vf._truth_root(t["meta"].get("harness_root") or "", t["workdir"]) if t.get("meta", {}).get("harness_root") else None
        from . import leaks
        for row in ([caches_row()] if "startup" in want else []) + proof.rows(t["workdir"], target, deleted, which=want, truth_root=truth) + (leaks.rows(target) if "leaks" in want else []) + (_decisions_row(t, target) if "decisions" in want else []) + (_visual_row(t, target, say) if "visual" in want else []) + (_claims_row(t, target) if "claims" in want else []) + (_ui_rows(t, target, say) if "ui" in want else []) + (_about_rows(t, target, say) if "about" in want else []) + (_stamp_rows(task_id, target, say) if "stamp" in want else []) + (_satellite_rows(target, say) if "satellite" in want else []):
            say(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence'][:200]}")
            results.append({"name": row["check"].split(":")[0], "rc": 0 if row["ok"] else 1, "status": "ok" if row["ok"] else "FAIL",
                            "log": row.get("log"), "lines": row.get("bad", [])[:10]})
    for name, argv in POST_INSTALL:
        if only and name not in only:
            continue
        if name in DRIVES_WINDOW:
            if not drive:
                results.append({"name": name, "rc": None, "status": "SKIPPED", "log": None, "why": "takes the keyboard: run with --drive when nobody is at the laptop"})
                say(f"  [SKIPPED] {name}: takes the keyboard and the foreground window; re-run with --drive when you are away")
                continue
            say(f"  !! {name} will take the KEYBOARD and the FOREGROUND for about 70 s. Starting in {DRIVE_COUNTDOWN} s - Ctrl+C to stop.")
            for left in range(DRIVE_COUNTDOWN, 0, -5):
                say(f"     {left} s ...")
                sleep(5)
        script = scripts_dir() / f"{name}.py"
        if not script.is_file():
            results.append({"name": name, "rc": None, "status": "SKIPPED", "log": None, "why": f"missing: {script}"})
            say(f"  [SKIPPED] {name}: {script} not found: this check did NOT run")
            continue
        cmd = [sys.executable, str(script)] + [a.format(dir=target) for a in argv]
        say(f"  {name} ...")
        t0 = time.time()
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, cwd=str(script.parent))
            rc, out = r.returncode, (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired as e:
            rc, out = 124, f"timeout after {timeout} s\n" + ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""))
        log = logs / f"{name}.log"
        log.write_text(out, encoding="utf-8")
        results.append({"name": name, "rc": rc, "status": "ok" if rc == 0 else "FAIL", "log": str(log), "seconds": round(time.time() - t0)})
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:]
        say(f"  [{'ok' if rc == 0 else 'rc=' + str(rc)}] {name} ({time.time() - t0:.0f} s): {tail[0][:150] if tail else ''}")
    ok = bool(results) and all(r["status"] == "ok" for r in results)
    skipped = [r["name"] for r in results if r["status"] == "SKIPPED"]
    if skipped:
        say(f"  NOT OK while anything is SKIPPED: {skipped}")
    task.journal(t, "post_install", target=str(target), ok=ok, results=[(r["name"], r["rc"], r["status"]) for r in results])
    return {"ok": ok, "target": str(target), "results": results, "skipped": skipped}
