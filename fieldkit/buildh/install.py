"""Install a verified build on this machine, with a backup first and a way back.

Order of events, every one measured and journaled:
  1. backup   - the installed browser (install dir) and the profiles, zipped into
                ~/Documents/Gorilla.Firefox.Backups/<version>-<stamp>/ with a manifest (sha256, sizes, application.ini
                version, profiles.ini). Documents exists on Windows and Linux alike, so the backup lives in the same
                place on both.
  2. install  - the packaged installer, silently, into the SAME directory the current build uses (a per-user
                directory needs no elevation: C:\\Users\\<you>\\Gorilla Unleashed). Firefox must not be running.
  3. verify   - application.ini Version and `firefox.exe --version` must say the pinned version; the uninstall
                registry entry too.
  4. restore  - the backup unpacked over the install dir, when asked (`install --restore <backup dir>`).
Profiles are never touched by install; they are backed up because an upgrade migrates them in place.
"""
import configparser
import hashlib
import json
import os
import re
import shutil
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


def find_install():
    """The per-user install the uninstall registry names (Windows), or None."""
    if sys.platform != "win32":
        return None
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            root = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                name = winreg.QueryValueEx(sub, "DisplayName")[0]
                if "Gorilla" in name or "Firefox" in name:
                    loc = winreg.QueryValueEx(sub, "InstallLocation")[0]
                    if loc and (Path(loc) / "firefox.exe").is_file():
                        return Path(loc)
            except OSError:
                continue
    return None


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


def clear_startup_caches(say=print):
    """Delete every profile's startupCache. Firefox keys that cache on the BuildID; two different builds with one
    BuildID (02 Oct, builds 2-6) left the owner's profile running the broken build's compiled scripts while a
    fresh profile ran the new one. -> [cleared dirs]. Refuses while Firefox runs."""
    if subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process firefox -ErrorAction SilentlyContinue | Select-Object -First 1"],
                      capture_output=True, text=True).stdout.strip():
        raise task.Refused("Firefox is running: close it before the startup caches are cleared")
    cleared = []
    for prof in local_profiles():
        sc = prof / "startupCache"
        if sc.is_dir():
            n = sum(1 for _ in sc.rglob("*") if _.is_file())
            shutil.rmtree(sc, ignore_errors=True)
            cleared.append(f"{sc} ({n} files)")
            say(f"  startup cache cleared: {sc.parent.name} ({n} files)")
    return cleared


def caches_row():
    left = [str(p / "startupCache") for p in local_profiles() if (p / "startupCache").is_dir() and any((p / "startupCache").iterdir())]
    return {"check": "profiles: no stale startup cache from an earlier build", "ok": not left,
            "evidence": "every profile's startupCache is empty" if not left else f"{len(left)} profile(s) still cached: {left[:2]}"}


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


def verify(install_dir, version):
    """-> rows [{check, ok, evidence}] for the installed build."""
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
    reg = find_install()
    rows.append({"check": "the uninstall entry points at this install", "ok": reg is not None and Path(reg).resolve() == Path(install_dir).resolve(),
                 "evidence": str(reg)})
    return rows


def restore(backup_dir, install_dir, say=print):
    """Unpack install-<ver>.zip over `install_dir` (the dir is emptied first). Profiles are NOT restored unless asked."""
    if running(install_dir):
        raise task.Refused(f"Firefox is running from {install_dir}: close it first")
    man = json.loads((Path(backup_dir) / "manifest.json").read_text(encoding="utf-8"))
    z = Path(backup_dir) / man["files"]["install"]["zip"]
    if _sha(z) != man["files"]["install"]["sha256"]:
        raise task.Refused(f"{z.name} does not match its manifest hash: not restoring from a damaged backup")
    inst = Path(install_dir)
    if inst.exists():
        shutil.rmtree(inst)
    inst.mkdir(parents=True)
    with zipfile.ZipFile(z) as zf:
        zf.extractall(inst)
    say(f"  restored {z.name} into {inst}")
    return installed(inst)


def run(task_id, do_backup=True, say=print, install_dir=None):
    """The whole step: backup -> install -> verify, journaled. -> {"ok", "backup", "rows"}."""
    t = task.load(task_id)
    res = json.loads((task.STATE / task_id / "build-result.json").read_text(encoding="utf-8"))
    installer = Path(res["artifacts"]["installer"]["file"])
    if _sha(installer) != res["artifacts"]["installer"]["sha256"]:
        return {"ok": False, "why": "the installer on disk is not the one build-verify hashed"}
    version = t["meta"]["upstream"]["version"]
    target = Path(install_dir) if install_dir else find_install()
    if not target:
        return {"ok": False, "why": "no installed Gorilla/Firefox found to replace; pass --install-dir"}
    before = installed(target)
    say(f"  installed now: {before} at {target}")
    bdir = None
    if do_backup:
        bdir = backup(target, say=say)
        task.journal(t, "backup", dest=str(bdir), installed=before)
    rc, secs = install(installer, target, say=say)
    cleared = clear_startup_caches(say=say)
    rows = verify(target, version) + [caches_row()]
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
# checks that take the keyboard / foreground: never run unless asked with --drive, and announced with a countdown first
DRIVES_WINDOW = {"verify_address_bar"}
DRIVE_COUNTDOWN = 20


STARTUP_ERROR = re.compile(r"JavaScript error:.*(SyntaxError|No such JSWindowActor|ReferenceError|is not defined)")


def startup_errors(install_dir, seconds=25, say=print):
    """Start the installed browser HEADLESS on a throwaway profile and read its stderr: a module that fails to parse
    or an actor that never registered shows up here within seconds, with no window and no keyboard. 2026-10-02: the
    address bar check needed four keyboard-taking runs to find what one line of stderr said at startup:
    `DesktopActorRegistry.sys.mjs, line 242: SyntaxError`. -> {ok, lines, log}."""
    import tempfile
    exe = Path(install_dir) / "firefox.exe"
    prof = Path(tempfile.mkdtemp(prefix="gstartup_"))
    (prof / "user.js").write_text("\n".join([
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("browser.aboutwelcome.enabled", false);',
        'user_pref("devtools.console.stdout.chrome", true);', ""]), encoding="utf-8")
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
    say(f"  [{'ok' if not lines else 'FAIL'}] headless startup: {len(lines)} module/actor error line(s)" + (f": {lines[0]}" if lines else ""))
    return {"ok": not lines, "lines": lines, "log": str(log)}


def scripts_dir():
    return documents() / "Gorilla.firefox" / "working scripts"


def post_install(task_id, install_dir=None, only=None, say=print, timeout=900, drive=False, sleep=time.sleep):
    """Run the owner's post-install checks against the installed build. -> {"ok", "results": [{name, rc, log}]}."""
    t = task.load(task_id)
    target = Path(install_dir) if install_dir else find_install()
    if not target:
        return {"ok": False, "why": "no installed Gorilla/Firefox found; pass --install-dir"}
    if running(target):
        raise task.Refused(f"Firefox is running from {target}: the checks need to start it themselves")
    logs = task.STATE / task_id / "post-install"
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    # production proof first: the shipped artefacts and a headless start, no window, no keyboard
    from . import proof, firefox
    want = {"prefs", "excised", "startup", "egress"} & (only or {"prefs", "excised", "startup", "egress"})
    if want:
        deleted = firefox.manifest_deletions(t["meta"]["harness_root"]) if t.get("meta", {}).get("harness_root") else []
        for row in ([caches_row()] if "startup" in want else []) + proof.rows(t["workdir"], target, deleted, which=want):
            say(f"  [{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['evidence'][:200]}")
            results.append({"name": row["check"].split(":")[0], "rc": 0 if row["ok"] else 1, "log": row.get("log"), "lines": row.get("bad", [])[:10]})
    for name, argv in POST_INSTALL:
        if only and name not in only:
            continue
        if name in DRIVES_WINDOW:
            if not drive:
                results.append({"name": name, "rc": None, "log": None, "why": "takes the keyboard: run with --drive when nobody is at the laptop"})
                say(f"  [skipped] {name}: takes the keyboard and the foreground window; re-run with --drive when you are away")
                continue
            say(f"  !! {name} will take the KEYBOARD and the FOREGROUND for about 70 s. Starting in {DRIVE_COUNTDOWN} s - Ctrl+C to stop.")
            for left in range(DRIVE_COUNTDOWN, 0, -5):
                say(f"     {left} s ...")
                sleep(5)
        script = scripts_dir() / f"{name}.py"
        if not script.is_file():
            results.append({"name": name, "rc": None, "log": None, "why": f"missing: {script}"})
            say(f"  [skip] {name}: {script} not found")
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
        results.append({"name": name, "rc": rc, "log": str(log), "seconds": round(time.time() - t0)})
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:]
        say(f"  [{'ok' if rc == 0 else 'rc=' + str(rc)}] {name} ({time.time() - t0:.0f} s): {tail[0][:150] if tail else ''}")
    ok = bool(results) and all(r["rc"] == 0 for r in results if r["rc"] is not None) and any(r["rc"] is not None for r in results)
    task.journal(t, "post_install", target=str(target), ok=ok, results=[(r["name"], r["rc"]) for r in results])
    return {"ok": ok, "target": str(target), "results": results}
