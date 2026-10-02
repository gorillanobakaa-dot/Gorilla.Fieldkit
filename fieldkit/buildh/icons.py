"""The icon checks every model forgets: is the gorilla really in firefox.exe, is the logo crisp, does the installer
show it. The owner wrote three tools for this (working scripts/check_embedded_icon.py, check_logo_provenance.py,
brand_installer_stub.py) and nothing called them; build-verify does now, and the build loop brands the installer's
7-Zip stub BEFORE the package stage, where it is consumed.
"""
import re
import struct
import subprocess
import sys
from pathlib import Path

ICONS = ("firefox.ico", "document.ico", "pbmode.ico", "newtab.ico")


def branding_dir(mozconfig_text):
    """`ac_add_options --with-branding=browser/branding/gorilla` -> that relative path, or None."""
    m = re.search(r"--with-branding=([\w./-]+)", mozconfig_text)
    return m.group(1) if m else None


def ico_images(path):
    """[((w, h), bpp, payload)] of every image in an .ico."""
    d = Path(path).read_bytes()
    _, _, n = struct.unpack("<HHH", d[:6])
    out = []
    for i in range(n):
        w, h, c, r, planes, bc, size, off = struct.unpack("<BBBBHHII", d[6 + i * 16:22 + i * 16])
        out.append(((w or 256, h or 256), bc, d[off:off + size]))
    return out


def embedded(exe_path, branding):
    """-> [(ico name, images embedded, images total, detail)] byte-matching each image's pixel data (a distinctive
    interior slice; the DIB header is generic) against the PE. The owner's check_embedded_icon.py, as a function."""
    exe = Path(exe_path).read_bytes()
    rows = []
    for name in ICONS:
        p = Path(branding) / name
        if not p.is_file():
            continue
        hits, detail = 0, []
        for dims, bc, payload in ico_images(p):
            probe = payload[64:64 + 512] if len(payload) > 600 else payload
            found = probe in exe
            hits += found
            detail.append(f"{dims[0]}x{dims[1]}:{'YES' if found else 'no'}")
        rows.append((name, hits, len(detail), " ".join(detail)))
    return rows


def _tool(owner_root, name, *args, timeout=600):
    script = Path(owner_root) / "working scripts" / name
    if not script.is_file():
        return None, f"no {script}"
    r = subprocess.run([sys.executable, str(script), *args], cwd=str(owner_root), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def logo_provenance(owner_root):
    """The owner's Crisp Icon Doctrine gates (ICON-002, ASSET-002). -> (ok or None when the tool is absent, text)."""
    rc, text = _tool(owner_root, "check_logo_provenance.py", "--root", str(owner_root))
    return (None if rc is None else rc == 0), text


def installer_stub_check(owner_root):
    rc, text = _tool(owner_root, "brand_installer_stub.py", "--root", str(owner_root), "--check")
    return (None if rc is None else rc == 0), text


def brand_installer_stub(owner_root, say=print):
    """Before packaging: put this build's icon on the 7-Zip SFX stub (keeps a .orig). -> (ok, text)."""
    ok, text = installer_stub_check(owner_root)
    if ok is None:
        return None, text
    if ok:
        return True, "SFX stub already carries this build's icon"
    rc, text = _tool(owner_root, "brand_installer_stub.py", "--root", str(owner_root))
    say("  " + text.splitlines()[-1][:160] if text else "  stub tool said nothing")
    ok2, _ = installer_stub_check(owner_root)
    return bool(ok2), text
