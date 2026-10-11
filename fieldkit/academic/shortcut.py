"""A "Fieldkit Academic" shortcut that opens the numbered menu: on the desktop and in the Start menu (Windows) or the
applications list (Linux).

    fieldkit academic shortcut            make them (again: they are rewritten the same way)
    fieldkit academic shortcut --check    which exist and where they point; changes nothing
    fieldkit academic shortcut --remove   remove them: only shortcuts named Fieldkit Academic that open this menu

From the academic harness this generalises (make_shortcuts.py, 2026-10-10), without its one student's names and
icons. A shortcut is only ever removed when it is this tool's own: its name and the command it runs both match.
"""
import os
import subprocess
import sys
from pathlib import Path

from ..core import settings

NAME = "Fieldkit Academic"
ARGS = "-m fieldkit academic menu"


def _python():
    """pythonw has no console; the menu needs one, so python.exe."""
    return sys.executable


def places(home=None, platform=None):
    home = Path(home or Path.home())
    if (platform or sys.platform) == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return [home / "Desktop" / f"{NAME}.lnk",
                appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NAME}.lnk"]
    return [home / "Desktop" / "fieldkit-academic.desktop",
            home / ".local" / "share" / "applications" / "fieldkit-academic.desktop"]


def desktop_entry(python=None, root=None):
    return ("[Desktop Entry]\nType=Application\nName=" + NAME + "\nComment=The academic tools, as a numbered menu\n"
            f"Exec={python or _python()} {ARGS}\nPath={root or settings.ROOT}\nTerminal=true\nCategories=Education;\n")


def _ps_link(path, python, root):
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    return (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(path)}); "
            f"$s.TargetPath = {q(python)}; $s.Arguments = {q(ARGS)}; $s.WorkingDirectory = {q(root)}; "
            f"$s.Description = 'The academic tools, as a numbered menu'; $s.Save()")


def _target_of_lnk(path, run=subprocess.run):
    r = run(["powershell", "-NoProfile", "-Command",
             f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{str(path)}'); $s.TargetPath + ' ' + $s.Arguments"],
            capture_output=True, text=True)
    return (r.stdout or "").strip()


def ours(path, run=subprocess.run):
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix == ".desktop":
        return ARGS in p.read_text(encoding="utf-8", errors="replace")
    return ARGS in _target_of_lnk(p, run)


def make(home=None, platform=None, run=subprocess.run):
    made = []
    for p in places(home, platform):
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".desktop":
            p.write_text(desktop_entry(), encoding="utf-8")
            p.chmod(0o755)
        else:
            r = run(["powershell", "-NoProfile", "-Command", _ps_link(p, _python(), settings.ROOT)],
                    capture_output=True, text=True)
            if r.returncode != 0:
                return {"ok": False, "made": [str(x) for x in made], "error": (r.stderr or "").strip()[:200],
                        "next": "run it again from a normal (not administrator) window"}
        made.append(p)
    return {"ok": True, "made": [str(x) for x in made], "next": f"double-click {NAME} on the desktop"}


def check(home=None, platform=None, run=subprocess.run):
    rows = [{"path": str(p), "exists": p.is_file(), "ours": ours(p, run)} for p in places(home, platform)]
    return {"ok": all(r["exists"] and r["ours"] for r in rows), "shortcuts": rows,
            "next": "nothing to do" if all(r["exists"] for r in rows) else "fieldkit academic shortcut"}


def remove(home=None, platform=None, run=subprocess.run):
    removed, kept = [], []
    for p in places(home, platform):
        if p.is_file() and ours(p, run):
            p.unlink()
            removed.append(str(p))
        elif p.is_file():
            kept.append(str(p))
    return {"ok": True, "removed": removed, "kept_not_ours": kept, "next": "done"}
