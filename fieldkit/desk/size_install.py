"""Install the two already-built Gorilla Size executables for the current user."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


RECYCLE_BIN_VERB = (
    r"Software\Classes\CLSID\{645FF040-5081-101B-9F08-00AA002F954E}"
    r"\shell\Fieldkit.GorillaTrash"
)


def path_has(directory, value):
    wanted = os.path.normcase(os.path.normpath(str(directory)))
    return any(os.path.normcase(os.path.normpath(os.path.expandvars(entry.strip('"')))) == wanted
               for entry in value.split(os.pathsep) if entry)


def install_windows(bindir, trash):
    import ctypes
    from ctypes import wintypes
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        if kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            raise OSError("the current user's Path is not a string")
        if not path_has(bindir, current):
            updated = f"{current};{bindir}" if current else str(bindir)
            winreg.SetValueEx(key, "Path", 0, kind, updated)
        saved, _ = winreg.QueryValueEx(key, "Path")
        if not path_has(bindir, saved):
            raise OSError("the current user's Path did not retain the new directory")

    command = f'"{trash}" empty'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RECYCLE_BIN_VERB) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Empty with Gorilla Trash...")
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(trash))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RECYCLE_BIN_VERB + r"\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        if winreg.QueryValueEx(key, "")[0] != command:
            raise OSError("the Recycle Bin menu command did not persist")

    notify = ctypes.windll.user32.SendMessageTimeoutW
    notify.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPCWSTR,
                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    notify.restype = wintypes.LPARAM
    notify(0xFFFF, 0x001A, 0, "Environment", 0x0002, 1000, ctypes.byref(ctypes.c_size_t()))


def main(argv, default_source):
    parser = argparse.ArgumentParser(prog="fieldkit size integrate")
    parser.add_argument("--from-dir", type=Path, default=default_source,
                        help="directory containing both already-built native programs")
    args = parser.parse_args(argv)
    if sys.platform not in ("win32", "linux"):
        print("Gorilla Size integration supports Windows and Linux", file=sys.stderr)
        return 2

    windows = sys.platform == "win32"
    suffix = ".exe" if windows else ""
    home = Path.home()
    if windows and not os.environ.get("LOCALAPPDATA"):
        print("LOCALAPPDATA is missing", file=sys.stderr)
        return 1
    bindir = Path(os.environ["LOCALAPPDATA"]) / "Fieldkit" / "bin" if windows else home / ".local" / "bin"
    payload_dir = bindir if windows else home / ".local" / "share" / "fieldkit" / "gorilla-size"
    sources = [args.from_dir.resolve() / f"{name}{suffix}" for name in ("gorilla-size", "gorilla-trash")]
    for binary, flag in zip(sources, ("--version", "--help")):
        try:
            result = subprocess.run([str(binary), flag], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Cannot open {binary}: {exc}", file=sys.stderr)
            return 1
        if result.returncode or not result.stdout.startswith(binary.stem + " "):
            print(f"Unexpected {flag} output from {binary}", file=sys.stderr)
            return 1

    try:
        if not windows:
            for binary in sources:
                link = bindir / binary.name
                if os.path.lexists(link) and not (link.is_symlink() and link.readlink() == payload_dir / binary.name):
                    raise OSError(f"{link} already exists. Move it aside before integrating.")
        payload_dir.mkdir(parents=True, exist_ok=True)
        for binary in sources:
            dest = payload_dir / binary.name
            if dest.is_symlink():
                raise OSError(f"Refusing to replace a symlink at {dest}")
            if binary != dest:
                staging = dest.with_name(dest.name + ".fieldkit-new")
                if staging.is_symlink():
                    raise OSError(f"Refusing to write through a symlink at {staging}")
                shutil.copy2(binary, staging)
                staging.replace(dest)
        if windows:
            install_windows(bindir, bindir / sources[1].name)
        else:
            bindir.mkdir(parents=True, exist_ok=True)
            for binary in sources:
                link = bindir / binary.name
                if not link.is_symlink():
                    link.symlink_to(payload_dir / binary.name)
    except OSError as exc:
        print(f"Gorilla Size integration: {exc}", file=sys.stderr)
        return 1

    print(f"Installed gorilla-size and gorilla-trash in {bindir}")
    if windows:
        print("Registered the current user's Recycle Bin menu. Open a fresh terminal from Explorer.")
    elif not path_has(bindir, os.environ.get("PATH", "")):
        print(f"Add {bindir} to your shell's PATH if it is still missing after logging in again.")
    return 0
