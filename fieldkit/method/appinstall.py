"""fieldkit install app / purge: deterministic, 0-LLM cross-OS application purge and installer for Windows & Linux.

Symmetric across [L15-WIN] (Windows) and [VAIO-LNX] (Linux), locally or across the cable (--peer) / SSH (--host):
  - `fieldkit install purge <app> [--dir PATH] [--apply] [--peer|--host HOST]`
      Stops running processes, removes/recycles corrupted or old binaries, shortcuts, icons, desktop launchers,
      and (on Windows) exports + deletes stale registry keys (Uninstall, Software\\<GUID>, MuiCache,
      Compatibility Assistant\\Store) into the Recycle Bin / ~/Agents.Work.Trash with an undo manifest.
  - `fieldkit install app <source> [--name <app>] [--replace] [--apply] [--peer|--host HOST]`
      Optionally purges any older/corrupted install first (--replace), copies the unpacked tree or binary into the
      OS-canonical install path (`~/.local/opt/<app>-<ver>` on Linux, `%LOCALAPPDATA%\\Programs\\<App>` on Windows)
      with SHA-256 verification, creates symlinks/shortcuts, generates/installs all required icon sizes (RGBA PNG
      48..512 + scalable SVG on Linux; 16/32/48/256 .ico + .lnk shortcuts on Windows), updates OS icon & launcher
      caches, and runs deterministic verification.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

from . import installverify as iv
from . import installverify_os as ivos

ROOT = Path(__file__).resolve().parents[2]
HICOLOR_SIZES = (48, 64, 96, 128, 256, 512)
WIN_ICO_SIZES = (16, 32, 48, 256)

APP_META = {
    "antigravity": {
        "display_name": "Antigravity",
        "comment": "Google Antigravity agentic IDE",
        "categories": "Development;IDE;",
        "wm_class": "Antigravity",
        "binary": "antigravity",
        "win_exe": "Antigravity.exe",
        "win_dir": "Antigravity",
        "preserve_bins": ("agy",),
    },
    "gorilla-opencode": {
        "display_name": "Gorilla OpenCode",
        "comment": "Gorilla OpenCode coding agent",
        "categories": "Development;IDE;Utility;",
        "wm_class": "gorilla-opencode",
        "binary": "gorilla-opencode",
        "win_exe": "gorilla-opencode.exe",
        "win_dir": "gorilla-opencode",
        "preserve_bins": (),
    },
    "codex": {
        "display_name": "Codex",
        "comment": "OpenAI Codex coding agent",
        "categories": "Development;Utility;",
        "wm_class": "codex",
        "binary": "codex",
        "win_exe": "codex.exe",
        "win_dir": "codex",
        "preserve_bins": (),
    },
    "agy": {
        "display_name": "Antigravity CLI",
        "comment": "Antigravity terminal agent CLI",
        "categories": "Development;Utility;",
        "wm_class": "agy",
        "binary": "agy",
        "win_exe": "agy.cmd",
        "win_dir": "agy",
        "preserve_bins": ("antigravity",),
    },
}


def app_meta(name: str) -> dict:
    key = name.strip().lower()
    if key in APP_META:
        return dict(APP_META[key], id=key)
    disp = name.strip().replace("-", " ").title()
    return {
        "id": key,
        "display_name": disp,
        "comment": f"{disp} application",
        "categories": "Development;Utility;",
        "wm_class": name.strip(),
        "binary": key,
        "win_exe": f"{key}.exe",
        "win_dir": disp.replace(" ", ""),
        "preserve_bins": (),
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_source_version(source: Path, app_id: str) -> str:
    """Read version from source tree (`resources/app/package.json`, `package.json`, or `version` file)."""
    source = Path(source)
    if source.is_dir():
        for rel in ("resources/app/package.json", "resources/app/product.json", "package.json"):
            cand = source / rel
            if cand.is_file():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8", errors="replace"))
                    v = data.get("version") or data.get("productVersion")
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                except ValueError:
                    pass
        ver_file = source / "version"
        if ver_file.is_file():
            txt = ver_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if txt and txt[0].strip():
                return txt[0].strip()
        m = re.search(rf"{re.escape(app_id)}-([0-9][0-9A-Za-z._-]*)$", source.name, re.I)
        if m:
            return m.group(1)
    return "local"


def make_rgba_png(size: int, rgb: tuple[int, int, int] = (24, 90, 219)) -> bytes:
    """Create a deterministic, valid RGBA PNG (`color_type == 6`, bit_depth == 8) of `size x size` in pure Python."""
    r0, g0, b0 = rgb
    rows = []
    cx = cy = (size - 1) / 2.0
    radius = size * 0.45
    inner = size * 0.22
    for y in range(size):
        row = bytearray([0])  # filter type 0 (None)
        gy = (y * 19) // max(1, size)
        for x in range(size):
            gx = (x * 19) // max(1, size)
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist > radius:
                row.extend((x & 1, y & 1, 0, 0))
            elif dist < inner:
                row.extend((240 - gx, 246 - gy, 255, 255))
            else:
                row.extend((min(255, r0 + gx), min(255, g0 + gy), b0, 255))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, level=9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def make_svg(display_name: str, rgb: tuple[int, int, int] = (24, 90, 219)) -> str:
    letter = (display_name[:1] or "A").upper()
    hex_col = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">\n'
        f'  <rect x="16" y="16" width="224" height="224" rx="48" fill="{hex_col}"/>\n'
        f'  <text x="128" y="168" font-family="sans-serif" font-size="128" font-weight="bold" '
        f'text-anchor="middle" fill="#ffffff">{letter}</text>\n'
        "</svg>\n"
    )


def make_ico(sizes: tuple[int, ...] = WIN_ICO_SIZES, rgb: tuple[int, int, int] = (24, 90, 219)) -> bytes:
    """Build a valid Windows `.ico` file embedding RGBA PNG frames for each size in `sizes`."""
    frames = [(s, make_rgba_png(s, rgb=rgb)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = []
    blobs = []
    for s, png in frames:
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries.append(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset))
        blobs.append(png)
        offset += len(png)
    return header + b"".join(entries) + b"".join(blobs)


def _find_source_png(source: Path) -> Path | None:
    """Locate a candidate PNG icon inside an unpacked app tree if present."""
    if not source.is_dir():
        return None
    for rel in (
        "resources/app/resources/linux/code.png",
        "resources/app/resources/linux/antigravity.png",
        "antigravity.png",
        "code.png",
    ):
        cand = source / rel
        if cand.is_file():
            info = iv.png_info(cand.read_bytes()[:64])
            if info and info.get("ctype") == 6:
                return cand
    for cand in sorted(source.rglob("*.png"))[:40]:
        if any(k in cand.name.lower() for k in ("icon", "logo", "code", "antigravity")):
            info = iv.png_info(cand.read_bytes()[:64])
            if info and info.get("ctype") == 6:
                return cand
    return None


def _linux_processes_under(prefixes: list[str]) -> list[dict]:
    """Find running processes on Linux whose executable or cmdline starts with any prefix in `prefixes`."""
    out = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return out
    my_pid = os.getpid()
    norm_prefixes = [p.rstrip("/") for p in prefixes if p]
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == my_pid:
            continue
        exe = ""
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            pass
        cmdline = ""
        try:
            raw = (entry / "cmdline").read_bytes()
            cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            pass
        matched = False
        for pref in norm_prefixes:
            if (exe and (exe == pref or exe.startswith(pref + "/"))) or (
                cmdline and (cmdline.startswith(pref + " ") or cmdline.startswith(pref + "/") or cmdline == pref)
            ):
                matched = True
                break
        if matched:
            out.append({"pid": pid, "exe": exe or cmdline.split()[0]})
    return sorted(out, key=lambda x: x["pid"])


def _trash_move_linux(path: Path, trash_dir: Path) -> str:
    """Move `path` into `trash_dir` (never permanent delete), preserving relative structure."""
    trash_dir.mkdir(parents=True, exist_ok=True)
    dest = trash_dir / path.name
    idx = 1
    while dest.exists() or dest.is_symlink():
        dest = trash_dir / f"{path.name}.{idx}"
        idx += 1
    shutil.move(str(path), str(dest))
    return str(dest)


def purge_linux(name: str, apply: bool = False, install_dir: str | None = None, home: Path | None = None) -> dict:
    """Purge an application on Linux: stop processes, disable user systemd unit, move files to ~/Agents.Work.Trash,
    and update hicolor icon cache + desktop database."""
    meta = app_meta(name)
    app_id = meta["id"]
    home = Path(home or Path.home()).resolve()
    opt_root = home / ".local" / "opt"
    bin_root = home / ".local" / "bin"
    apps_root = home / ".local" / "share" / "applications"
    autostart_root = home / ".config" / "autostart"
    hicolor_root = home / ".local" / "share" / "icons" / "hicolor"
    systemd_root = home / ".config" / "systemd" / "user"

    opt_targets: list[Path] = []
    if install_dir:
        p = Path(install_dir)
        if p.exists() or p.is_symlink():
            opt_targets.append(p)
    if opt_root.is_dir():
        for child in sorted(opt_root.iterdir()):
            cname = child.name.lower()
            if cname == app_id or cname.startswith(app_id + "-"):
                if child not in opt_targets:
                    opt_targets.append(child)

    bin_target = bin_root / meta["binary"]
    file_targets: list[Path] = list(opt_targets)
    if (bin_target.exists() or bin_target.is_symlink()) and meta["binary"] not in meta["preserve_bins"]:
        file_targets.append(bin_target)

    for d_file in (apps_root / f"{app_id}.desktop", autostart_root / f"{app_id}.desktop"):
        if d_file.exists() or d_file.is_symlink():
            file_targets.append(d_file)

    for sz in HICOLOR_SIZES:
        ic = hicolor_root / f"{sz}x{sz}" / "apps" / f"{app_id}.png"
        if ic.exists() or ic.is_symlink():
            file_targets.append(ic)
    svg_ic = hicolor_root / "scalable" / "apps" / f"{app_id}.svg"
    if svg_ic.exists() or svg_ic.is_symlink():
        file_targets.append(svg_ic)

    systemd_units: list[Path] = []
    if systemd_root.is_dir():
        for u in sorted(systemd_root.glob(f"{app_id}*.service")):
            systemd_units.append(u)
            file_targets.append(u)

    for cache_dir in (
        home / ".config" / f"{app_id}-updater",
        home / ".cache" / f"{app_id}-updater",
        home / ".cache" / app_id,
    ):
        if cache_dir.exists():
            file_targets.append(cache_dir)

    prefixes = [str(p) for p in opt_targets]
    if bin_target.exists():
        prefixes.append(str(bin_target))
    procs = _linux_processes_under(prefixes)

    user_data_kept = [str(p) for p in (home / f".{app_id}", home / ".config" / meta["display_name"]) if p.exists()]

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    trash_dir = home / "Agents.Work.Trash" / f"purge-{app_id}-{stamp}"
    ops_done = []
    errors = []

    if apply:
        for proc in procs:
            pid = proc["pid"]
            try:
                os.kill(pid, signal.SIGTERM)
                ops_done.append({"op": "stop", "pid": pid, "exe": proc["exe"]})
            except OSError as e:
                errors.append(f"stop pid {pid}: {e}")
        if procs:
            time.sleep(0.3)
            for proc in procs:
                pid = proc["pid"]
                try:
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

        for u in systemd_units:
            subprocess.run(["systemctl", "--user", "disable", "--now", u.name], capture_output=True)

        for target in file_targets:
            if target.exists() or target.is_symlink():
                try:
                    dest = _trash_move_linux(target, trash_dir)
                    ops_done.append({"op": "recycle", "path": str(target), "trash": dest})
                except OSError as e:
                    errors.append(f"recycle {target}: {e}")

        if ops_done:
            trash_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "tool": "fieldkit install purge",
                "os": "linux",
                "app": app_id,
                "timestamp": stamp,
                "done": ops_done,
                "kept_user_data": user_data_kept,
            }
            (trash_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if hicolor_root.is_dir() and shutil.which("gtk-update-icon-cache"):
            subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(hicolor_root)], capture_output=True)
        if apps_root.is_dir() and shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(apps_root)], capture_output=True)

    still_present = [str(p) for p in file_targets if p.exists() or p.is_symlink()] if apply else []
    return {
        "ok": len(errors) == 0 and len(still_present) == 0,
        "os": "linux",
        "app": app_id,
        "apply": apply,
        "processes": procs,
        "paths": [str(p) for p in file_targets],
        "systemd_units": [str(u) for u in systemd_units],
        "kept_user_data": user_data_kept,
        "trash_dir": str(trash_dir) if (apply and ops_done) else None,
        "still_present": still_present,
        "errors": errors,
    }


def purge_windows(name: str, apply: bool = False, install_dir: str | None = None, backup_dir: str | None = None) -> dict:
    """Purge an application on Windows by delegating to kits/profilefix/profilefix.py (`purge-app`) and refreshing
    the Explorer icon cache."""
    meta = app_meta(name)
    pf_path = ROOT / "kits" / "profilefix" / "profilefix.py"
    if not install_dir:
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            cand = Path(local_app) / "Programs" / meta["win_dir"]
            if cand.is_dir():
                install_dir = str(cand)
    cmd = [sys.executable, str(pf_path), "purge-app", name]
    if install_dir:
        cmd += ["--dir", str(install_dir)]
    if backup_dir:
        cmd += ["--backup-dir", str(backup_dir)]
    if apply:
        cmd.append("--apply")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out_text = (r.stdout or "") + (r.stderr or "")
    if apply and shutil.which("ie4uinit.exe"):
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)
    # Exit 1 with "no installed program" and no install_dir means nothing was installed to purge (clean state)
    nothing_installed = r.returncode == 1 and "no installed program" in out_text and not install_dir
    ok = (r.returncode == 0) or nothing_installed
    return {
        "ok": ok,
        "os": "windows",
        "app": meta["id"],
        "apply": apply,
        "install_dir": install_dir,
        "returncode": 0 if nothing_installed else r.returncode,
        "output": out_text.strip(),
    }


def install_linux(
    source: Path,
    name: str = "antigravity",
    replace: bool = False,
    apply: bool = False,
    home: Path | None = None,
) -> dict:
    """Install an unpacked app tree or binary on Linux into `~/.local/opt/<app>-<ver>` with full desktop integration."""
    source = Path(source).expanduser().resolve()
    meta = app_meta(name)
    app_id = meta["id"]
    home = Path(home or Path.home()).resolve()

    if not source.exists():
        return {"ok": False, "os": "linux", "app": app_id, "error": f"source path does not exist: {source}"}

    ver = detect_source_version(source, app_id)
    opt_dir = home / ".local" / "opt" / f"{app_id}-{ver}"
    bin_link = home / ".local" / "bin" / meta["binary"]
    desktop_file = home / ".local" / "share" / "applications" / f"{app_id}.desktop"
    hicolor_root = home / ".local" / "share" / "icons" / "hicolor"

    src_bin = source / meta["binary"] if source.is_dir() else source
    if not src_bin.is_file():
        return {"ok": False, "os": "linux", "app": app_id, "error": f"binary not found at {src_bin}"}

    plan_steps = []
    if replace:
        plan_steps.append(f"purge existing {app_id} installations under {home / '.local'}")
    plan_steps.extend(
        [
            f"copy {source} -> {opt_dir} and verify binary SHA-256",
            f"symlink {bin_link} -> {opt_dir / meta['binary']} (preserving {', '.join(meta['preserve_bins']) or 'other bins'})",
            f"write RGBA PNG icons ({', '.join(map(str, HICOLOR_SIZES))}) and scalable SVG under {hicolor_root}",
            f"write and validate {desktop_file}",
            "refresh gtk-update-icon-cache and update-desktop-database",
        ]
    )

    if not apply:
        return {
            "ok": True,
            "os": "linux",
            "app": app_id,
            "version": ver,
            "apply": False,
            "source": str(source),
            "opt_dir": str(opt_dir),
            "bin_link": str(bin_link),
            "desktop_file": str(desktop_file),
            "plan": plan_steps,
        }

    purge_res = None
    if replace:
        purge_res = purge_linux(app_id, apply=True, home=home)
        if not purge_res["ok"]:
            return {"ok": False, "os": "linux", "app": app_id, "error": "purge before install failed", "purge": purge_res}

    if opt_dir.exists():
        trash_dir = home / "Agents.Work.Trash" / f"replace-{app_id}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        _trash_move_linux(opt_dir, trash_dir)

    opt_dir.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, opt_dir, symlinks=True)
    else:
        opt_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, opt_dir / meta["binary"])

    installed_bin = opt_dir / meta["binary"]
    installed_bin.chmod(0o755)
    sandbox = opt_dir / "chrome-sandbox"
    if sandbox.is_file():
        try:
            sandbox.chmod(0o755)
        except OSError:
            pass

    src_sha = sha256_file(src_bin)
    dst_sha = sha256_file(installed_bin)
    if src_sha != dst_sha:
        return {
            "ok": False,
            "os": "linux",
            "app": app_id,
            "error": f"SHA-256 mismatch after copy: src={src_sha} dst={dst_sha}",
        }

    bin_link.parent.mkdir(parents=True, exist_ok=True)
    if bin_link.exists() or bin_link.is_symlink():
        trash_dir = home / "Agents.Work.Trash" / f"bin-{app_id}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        _trash_move_linux(bin_link, trash_dir)
    try:
        bin_link.symlink_to(installed_bin)
    except OSError:
        # Fallback when running Linux install tests on a Windows host without SeCreateSymbolicLinkPrivilege
        bin_link.write_text(f'#!/bin/sh\nexec "{installed_bin}" "$@"\n', encoding="utf-8")
        bin_link.chmod(0o755)

    for sz in HICOLOR_SIZES:
        ic_dir = hicolor_root / f"{sz}x{sz}" / "apps"
        ic_dir.mkdir(parents=True, exist_ok=True)
        ic_path = ic_dir / f"{app_id}.png"
        ic_path.write_bytes(make_rgba_png(sz))

    svg_dir = hicolor_root / "scalable" / "apps"
    svg_dir.mkdir(parents=True, exist_ok=True)
    (svg_dir / f"{app_id}.svg").write_text(make_svg(meta["display_name"]), encoding="utf-8", newline="\n")

    index_theme = hicolor_root / "index.theme"
    if not index_theme.is_file():
        sys_theme = Path("/usr/share/icons/hicolor/index.theme")
        if sys_theme.is_file():
            shutil.copy2(sys_theme, index_theme)
        else:
            dirs_list = ",".join([f"{s}x{s}/apps" for s in HICOLOR_SIZES] + ["scalable/apps"])
            sections = "\n".join(
                [f"[{s}x{s}/apps]\nSize={s}\nContext=Applications\nType=Fixed\n" for s in HICOLOR_SIZES]
                + ["[scalable/apps]\nSize=48\nContext=Applications\nType=Scalable\nMinSize=16\nMaxSize=512\n"]
            )
            index_theme.write_text(
                f"[Icon Theme]\nName=Hicolor\nComment=Fallback icon theme\nDirectories={dirs_list}\n\n{sections}",
                encoding="utf-8",
                newline="\n",
            )

    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    desktop_content = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        f"Name={meta['display_name']}\n"
        f"Comment={meta['comment']}\n"
        f"Exec={installed_bin} %U\n"
        f"TryExec={installed_bin}\n"
        f"Icon={app_id}\n"
        "Terminal=false\n"
        f"Categories={meta['categories']}\n"
        f"StartupWMClass={meta['wm_class']}\n"
        f"MimeType=x-scheme-handler/{app_id};\n"
    )
    desktop_file.write_text(desktop_content, encoding="utf-8", newline="\n")
    desktop_file.chmod(0o755)

    cache_runs = {}
    if shutil.which("desktop-file-validate"):
        r = subprocess.run(["desktop-file-validate", str(desktop_file)], capture_output=True, text=True)
        cache_runs["desktop-file-validate"] = {"returncode": r.returncode, "output": (r.stdout + r.stderr).strip()}
    if shutil.which("gtk-update-icon-cache"):
        r = subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(hicolor_root)], capture_output=True, text=True)
        cache_runs["gtk-update-icon-cache"] = {"returncode": r.returncode, "output": (r.stdout + r.stderr).strip()}
    if shutil.which("update-desktop-database"):
        r = subprocess.run(["update-desktop-database", str(desktop_file.parent)], capture_output=True, text=True)
        cache_runs["update-desktop-database"] = {"returncode": r.returncode, "output": (r.stdout + r.stderr).strip()}

    ref = iv.reference_from_local(app_id, opt_dir, home=home, version=ver)
    verify_res = iv.verify_linux(ref, home=home)
    return {
        "ok": verify_res["ok"],
        "os": "linux",
        "app": app_id,
        "version": ver,
        "apply": True,
        "source": str(source),
        "opt_dir": str(opt_dir),
        "bin_link": str(bin_link),
        "binary_sha256": dst_sha,
        "desktop_file": str(desktop_file),
        "caches": cache_runs,
        "purge": purge_res,
        "verify": verify_res,
    }


def _create_windows_shortcut(lnk_path: Path, target_exe: Path, icon_path: Path, description: str) -> tuple[bool, str]:
    """Create a Windows `.lnk` shortcut using PowerShell WScript.Shell."""
    lnk_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{str(lnk_path).replace(chr(39), chr(39)*2)}'); "
        f"$s.TargetPath = '{str(target_exe).replace(chr(39), chr(39)*2)}'; "
        f"$s.WorkingDirectory = '{str(target_exe.parent).replace(chr(39), chr(39)*2)}'; "
        f"$s.IconLocation = '{str(icon_path).replace(chr(39), chr(39)*2)},0'; "
        f"$s.Description = '{description.replace(chr(39), chr(39)*2)}'; "
        "$s.Save()"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    return r.returncode == 0 and lnk_path.is_file(), (r.stdout + r.stderr).strip()


def install_windows(
    source: Path,
    name: str = "antigravity",
    replace: bool = False,
    apply: bool = False,
    install_root: Path | None = None,
) -> dict:
    """Install an unpacked app tree or binary on Windows into `%LOCALAPPDATA%\\Programs\\<App>` with `.ico` & `.lnk`."""
    source = Path(source).expanduser().resolve()
    meta = app_meta(name)
    app_id = meta["id"]
    if not source.exists():
        return {"ok": False, "os": "windows", "app": app_id, "error": f"source path does not exist: {source}"}

    ver = detect_source_version(source, app_id)
    base_programs = Path(install_root) if install_root else Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    target_dir = base_programs / meta["win_dir"]
    exe_name = meta["win_exe"]
    src_exe = (source / exe_name) if source.is_dir() else source
    if source.is_dir() and not src_exe.is_file():
        cand = source / f"{app_id}.exe"
        if cand.is_file():
            src_exe = cand
            exe_name = cand.name

    if not src_exe.is_file():
        return {"ok": False, "os": "windows", "app": app_id, "error": f"executable not found at {src_exe}"}

    if not apply:
        return {
            "ok": True,
            "os": "windows",
            "app": app_id,
            "version": ver,
            "apply": False,
            "source": str(source),
            "target_dir": str(target_dir),
            "exe": str(target_dir / exe_name),
        }

    purge_res = None
    if replace:
        purge_res = purge_windows(app_id, apply=True, install_dir=str(target_dir))

    if target_dir.exists():
        from .. import size as fk_size
        fk_size.main(["recycle", str(target_dir)])

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target_dir)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / exe_name)

    dst_exe = target_dir / exe_name
    src_sha = sha256_file(src_exe)
    dst_sha = sha256_file(dst_exe)
    if src_sha != dst_sha:
        return {"ok": False, "os": "windows", "app": app_id, "error": f"SHA-256 mismatch: {src_sha} != {dst_sha}"}

    ico_path = target_dir / f"{app_id}.ico"
    if not ico_path.is_file() or ivos.ico_sizes(ico_path) != list(WIN_ICO_SIZES):
        ico_path.write_bytes(make_ico(WIN_ICO_SIZES))

    shortcuts = []
    if not install_root:
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / f"{meta['display_name']}.lnk"
        start_menu = (
            Path(os.environ.get("APPDATA", ""))
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / f"{meta['display_name']}.lnk"
        )
        for lnk in (desktop, start_menu):
            ok_lnk, msg = _create_windows_shortcut(lnk, dst_exe, ico_path, meta["comment"])
            shortcuts.append({"path": str(lnk), "ok": ok_lnk, "msg": msg})
        if shutil.which("ie4uinit.exe"):
            subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)

    return {
        "ok": True,
        "os": "windows",
        "app": app_id,
        "version": ver,
        "apply": True,
        "target_dir": str(target_dir),
        "exe": str(dst_exe),
        "exe_sha256": dst_sha,
        "ico": str(ico_path),
        "shortcuts": shortcuts,
        "purge": purge_res,
    }


def run_purge(
    name: str,
    apply: bool = False,
    install_dir: str | None = None,
    peer: bool = False,
    host: str | None = None,
    home: Path | None = None,
) -> dict:
    if peer:
        from ..peers import link
        r = link.call("purge-app", name=name, apply=apply, dir=install_dir)
        return r.get("result") or r
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes", host, "python3", "-m", "fieldkit", "install", "purge", name, "--json"]
        if install_dir:
            cmd += ["--dir", install_dir]
        if apply:
            cmd.append("--apply")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout) if r.returncode in (0, 3) and r.stdout.strip().startswith("{") else {
            "ok": False, "error": (r.stderr or r.stdout).strip()
        }
    if sys.platform == "win32":
        return purge_windows(name, apply=apply, install_dir=install_dir)
    return purge_linux(name, apply=apply, install_dir=install_dir, home=home)


def run_install(
    source: str,
    name: str = "antigravity",
    replace: bool = False,
    apply: bool = False,
    peer: bool = False,
    host: str | None = None,
    home: Path | None = None,
) -> dict:
    if peer:
        from ..peers import link
        r = link.call("install-app", source=source, name=name, replace=replace, apply=apply)
        return r.get("result") or r
    if host:
        cmd = [
            "ssh", "-o", "BatchMode=yes", host,
            "python3", "-m", "fieldkit", "install", "app", source, "--name", name, "--json",
        ]
        if replace:
            cmd.append("--replace")
        if apply:
            cmd.append("--apply")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return json.loads(r.stdout) if r.returncode in (0, 3) and r.stdout.strip().startswith("{") else {
            "ok": False, "error": (r.stderr or r.stdout).strip()
        }
    if sys.platform == "win32":
        return install_windows(Path(source), name=name, replace=replace, apply=apply)
    return install_linux(Path(source), name=name, replace=replace, apply=apply, home=home)
