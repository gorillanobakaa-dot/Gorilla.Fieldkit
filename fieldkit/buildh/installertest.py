# Version: 1.0.0 · updated 26-10-19
"""The shipped installer, installed and uninstalled for real, leaving the machine as it was.

    fieldkit build-harness installer-test TASK

Born 2026-10-19 (release 158.0 build 2): every install tonight came from the zip; the installer that ships beside it
had never run. 2026-10-02 had shown why that matters: the NSIS installer, silent, exited 0 having installed nothing.

  1. before   the per-user uninstall entries (HKCU ...\\Uninstall) and the shortcuts on the Desktop, in the Start menu
              and on the taskbar, recorded
  2. install  the installer build-verify hashed, silent, into a fresh folder under the session temp, with an INI that
              asks for NO shortcuts, no maintenance service, no default-browser agent (the owner's machine gains nothing)
  3. verify   every file is byte-identical (sha256) to the zip build-verify proved; application.ini says the build;
              firefox.exe --version says the version
  4. remove   the installer's own uninstaller (uninstall\\helper.exe /S), then the folder to the Recycle Bin
  5. after    the uninstall entries and the shortcuts are exactly as before (fails otherwise, naming what changed)
-> {"ok", "rows": [{check, ok, evidence}]}.
"""
import hashlib
import json
import os
import sys
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from . import task

INI = """[Install]
InstallDirectoryPath={dir}
TaskbarShortcut=false
DesktopShortcut=false
StartMenuShortcuts=false
MaintenanceService=false
PrivateBrowsingShortcut=false
OptionalExtensions=false
RegisterDefaultAgent=false
"""


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot():
    """-> {"uninstall": [key names under HKCU Uninstall], "shortcuts": [paths]}: what an installer could leave."""
    keys = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall") as k:
            i = 0
            while True:
                try:
                    keys.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except (ImportError, OSError):
        pass
    # an installer also registers itself as a browser for this account: StartMenuInternet clients and the Firefox
    # URL/HTML/PDF file types; the uninstaller must take them away again
    try:
        import winreg

        def subkeys(path, prefix=""):
            out = []
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                    i = 0
                    while True:
                        try:
                            n = winreg.EnumKey(k, i)
                        except OSError:
                            break
                        if n.lower().startswith(prefix.lower()):
                            out.append(path + "\\" + n)
                        i += 1
            except OSError:
                pass
            return out
        keys += subkeys(r"Software\Clients\StartMenuInternet")
        for pre in ("FirefoxURL", "FirefoxHTML", "FirefoxPDF"):
            keys += subkeys(r"Software\Classes", pre)
    except ImportError:
        pass
    places = [Path.home() / "Desktop", Path(os.environ.get("PUBLIC") or os.environ.get("ALLUSERSPROFILE", "")) / "Desktop",
              Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
              Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"]
    links = sorted(str(p) for d in places if d.is_dir() for p in d.glob("*.lnk"))
    return {"uninstall": sorted(keys), "shortcuts": links}


def zip_hashes(zip_path):
    """-> {relative path: sha256} for the zip's files under its one top-level folder."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        names = [x for x in z.namelist() if not x.endswith("/")]
        top = names[0].split("/")[0] + "/"
        for x in names:
            if x.startswith(top):
                out[x[len(top):]] = hashlib.sha256(z.read(x)).hexdigest()
    return out


def compare(install_dir, expected):
    """-> (missing, different, extra) relative paths; extra excludes what the installer adds on purpose."""
    d = Path(install_dir)
    have = {p.relative_to(d).as_posix(): p for p in d.rglob("*") if p.is_file()}
    missing = sorted(k for k in expected if k not in have)
    different = sorted(k for k in expected if k in have and _sha(have[k]) != expected[k])
    extra = sorted(k for k in have if k not in expected and not k.startswith("uninstall/")
                   and k not in ("install.log",               # the NSIS installer's own local record of what it wrote
                                    "installation_telemetry.json", "postSigningData", "distribution/setup.ini"))
    return missing, different, extra


def run(task_id, say=print, runner=subprocess.run, work=None):
    t = task.load(task_id)
    res = json.loads((task.STATE / task_id / "build-result.json").read_text(encoding="utf-8"))
    installer = Path(res["artifacts"]["installer"]["file"])
    zip_path = Path(res["artifacts"]["zip"]["file"])
    rows = []

    def row(check, ok, evidence):
        rows.append({"check": check, "ok": bool(ok), "evidence": evidence})
        say(f"  [{'ok' if ok else 'FAIL'}] {check}: {evidence}")

    ok_hash = _sha(installer) == res["artifacts"]["installer"]["sha256"]
    row("the installer is the one build-verify hashed", ok_hash, installer.name)
    if not ok_hash:
        return {"ok": False, "rows": rows}
    expected = zip_hashes(zip_path)
    before = snapshot()
    base = Path(work or tempfile.mkdtemp(prefix="gorilla-installer-test-"))
    base.mkdir(parents=True, exist_ok=True)
    target = base / "Gorilla Unleashed"
    ini = base / "install.ini"
    ini.write_text(INI.format(dir=target), encoding="utf-8")
    t0 = time.time()
    r = runner([str(installer), "/S", f"/INI={ini}"], capture_output=True, text=True, errors="replace", timeout=1800)
    row("the installer ran silently", r.returncode == 0, f"exit {r.returncode} in {time.time() - t0:.0f} s")
    missing, different, extra = compare(target, expected)
    row("every file of the package is installed, byte-identical", not missing and not different and target.is_dir(),
        f"{len(expected)} expected; missing {len(missing)} {missing[:3]}; different {len(different)} {different[:3]}")
    row("nothing else is installed (beyond the uninstaller)", not extra, f"extra {len(extra)} {extra[:5]}")
    ini_text = (target / "application.ini").read_text(encoding="utf-8", errors="replace") if (target / "application.ini").is_file() else ""
    bid = next((l.split("=", 1)[1].strip() for l in ini_text.splitlines() if l.startswith("BuildID=")), None)
    row("application.ini says the build", bool(bid) and bid == str(res.get("build_id")), f"BuildID {bid} (build-verify: {res.get('build_id')})")
    ver = ""
    if (target / "firefox.exe").is_file():
        ver = runner([str(target / "firefox.exe"), "--version"], capture_output=True, text=True, errors="replace",
                     timeout=120).stdout.strip()
    version = t["meta"]["upstream"]["version"]
    row("firefox.exe --version says the version", version in ver, ver or "no firefox.exe")
    helper = target / "uninstall" / "helper.exe"
    if helper.is_file():
        u = runner([str(helper), "/S"], capture_output=True, text=True, errors="replace", timeout=600)
        deadline = time.time() + 120                       # helper.exe hands off to a copy of itself and returns at once
        while (target / "firefox.exe").exists() and time.time() < deadline:
            time.sleep(2)
        row("its own uninstaller removes it", not (target / "firefox.exe").exists(), f"helper.exe /S exit {u.returncode}")
    else:
        row("its own uninstaller removes it", False, "no uninstall/helper.exe was installed")
    after = snapshot()
    new_keys = sorted(set(after["uninstall"]) - set(before["uninstall"]))
    gone_keys = sorted(set(before["uninstall"]) - set(after["uninstall"]))
    new_links = sorted(set(after["shortcuts"]) - set(before["shortcuts"]))
    gone_links = sorted(set(before["shortcuts"]) - set(after["shortcuts"]))
    row("the uninstall entries are as before", not new_keys and not gone_keys, f"added {new_keys} removed {gone_keys}")
    row("the shortcuts are as before", not new_links and not gone_links, f"added {new_links[:3]} removed {gone_links[:3]}")
    if base.exists():                                      # leftovers to the Recycle Bin, never a permanent delete
        rr = runner([sys.executable, "-m", "fieldkit", "size", "recycle", str(base), "--json"], capture_output=True,
                    text=True, errors="replace", timeout=600)
        row("the test folder is in the Recycle Bin", rr.returncode == 0, str(base))
    ok = all(x["ok"] for x in rows)
    task.journal(t, "installer-test", ok=ok, failed=[x["check"] for x in rows if not x["ok"]])
    return {"ok": ok, "rows": rows}
