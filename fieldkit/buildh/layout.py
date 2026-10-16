# Version: 1.0.0 · updated 26-10-15-20-07
"""Where an installed Gorilla build keeps its program and its files, on Windows and on Linux, packed or unpacked.

Born 2026-10-15. The post-install checks were written on Windows and had never run on Linux: the first line asked
PowerShell for firefox.exe, the excision check read only omni.ja (the Linux working build is UNPACKED, D-158-11, so
it reported "unreadable" and checked nothing), and the startup, egress and ad-block rows started firefox.exe and
stopped it with taskkill. Every check that touches an install goes through these four, so no check can quietly
assume one platform again:

  exe(install_dir)                the program: firefox.exe on Windows, firefox elsewhere
  members(install_dir, jar)       the file names a jar ("omni.ja", "browser/omni.ja") holds, read from the archive
                                  when it exists, else from the loose files where the archive would be
  read(install_dir, jar, member)  one member's bytes, the same way; KeyError when neither has it
  start(argv, **kw) / stop(proc)  start the browser in its own process group, stop it and its children by PID
A jar that is neither an archive nor a folder of loose files raises FileNotFoundError: never "nothing found".
"""
import subprocess
import sys
import zipfile
from pathlib import Path


def exe(install_dir):
    return Path(install_dir) / ("firefox.exe" if sys.platform == "win32" else "firefox")


def _loose_root(install_dir, jar):
    return Path(install_dir) / Path(jar).parent


def packed(install_dir, jar):
    return (Path(install_dir) / jar).is_file()


def members(install_dir, jar):
    """Member names of `jar`, archive or loose. Loose: every file under the jar's folder, as the archive would name it
    (the unpacked browser/ folder is browser/omni.ja's root; the install root is omni.ja's, minus browser/)."""
    if packed(install_dir, jar):
        with zipfile.ZipFile(Path(install_dir) / jar) as z:
            return [n for n in z.namelist() if not n.endswith("/")]
    root = _loose_root(install_dir, jar)
    if not (root / "chrome.manifest").is_file():
        raise FileNotFoundError(f"{Path(install_dir) / jar} is neither an archive nor an unpacked folder")
    skip = () if jar.startswith("browser/") else ("browser/",)
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(skip):
            out.append(rel)
    return out


def read(install_dir, jar, member):
    if packed(install_dir, jar):
        with zipfile.ZipFile(Path(install_dir) / jar) as z:
            return z.read(member)
    p = _loose_root(install_dir, jar) / member
    if not p.is_file():
        raise KeyError(member)
    return p.read_bytes()


def start(argv, **kw):
    """Popen in a new process group (Linux), so stop() can end the browser's children too."""
    if sys.platform != "win32":
        kw.setdefault("start_new_session", True)
    return subprocess.Popen([str(a) for a in argv], **kw)


def stop(proc):
    from .probe import stop_tree
    stop_tree(proc.pid)
