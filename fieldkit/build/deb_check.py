# Version: 1.0.0 · updated 26-10-15-04-20
"""What a Gorilla .deb gives the person who installs it: the menu entry, the launcher, crisp icons, the refresh.

    verify of the firefox-linux pipeline package stage (fieldkit.build.firefox_linux:verify_deb)

Born 2026-10-15 on the first Linux 158 package: the menu entry still read "Gorilla Unleashed 154" (the template was
never stamped; 155 shipped the same), and the post-install script refreshed the icon cache but not the menu database.
Owner: "denumirea de firefox, crearea iconitelor crisp, instalarea si impachetarea" must not be forgotten.

Each finding is deterministic, read from the package itself (dpkg-deb), never from the template it was made from:
  name      the .desktop Name= equals the branding's MOZ_APP_DISPLAYNAME, so no version number can go stale
  exec      every Exec= program path is a file inside the package
  icons     the Icon= name exists under hicolor/NxN/apps for the sizes desktops ask for, and each PNG's real pixel
            size (read from its IHDR header) is exactly NxN: a stretched or non-square icon renders blurred or small
  refresh   postinst runs gtk-update-icon-cache and update-desktop-database
"""
import re
import struct
import subprocess
import tarfile
import io
from pathlib import Path

SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024)


def display_name(branding):
    """MOZ_APP_DISPLAYNAME from the branding's configure.sh, or None."""
    f = Path(branding) / "configure.sh"
    if not f.is_file():
        return None
    m = re.search(r'^MOZ_APP_DISPLAYNAME="?([^"\n]+)"?', f.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else None


def png_size(data):
    """(width, height) from a PNG's IHDR, or None when it is not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


def _members(deb):
    raw = subprocess.run(["dpkg-deb", "--fsys-tarfile", str(deb)], capture_output=True, check=True).stdout
    tf = tarfile.open(fileobj=io.BytesIO(raw))
    return {m.name.lstrip("."): (m, tf) for m in tf.getmembers()}


def _control(deb, name):
    r = subprocess.run(["dpkg-deb", "--ctrl-tarfile", str(deb)], capture_output=True, check=True).stdout
    tf = tarfile.open(fileobj=io.BytesIO(r))
    for m in tf.getmembers():
        if m.name.lstrip("./") == name:
            return tf.extractfile(m).read().decode("utf-8", "replace")
    return ""


def check(deb, branding):
    files = _members(deb)
    read = lambda p: files[p][1].extractfile(files[p][0]).read() if p in files and files[p][0].isfile() else None
    problems, notes = [], []
    desktops = [p for p in files if p.startswith("/usr/share/applications/") and p.endswith(".desktop")]
    if not desktops:
        problems.append("name: no .desktop file under /usr/share/applications: the browser has no menu entry")
    want = display_name(branding)
    icon = None
    for d in desktops:
        text = read(d).decode("utf-8", "replace")
        main = text.split("\n[", 1)[0]                       # the [Desktop Entry] group, not the actions
        name = re.search(r"^Name=(.*)$", main, re.M)
        if want is None:
            notes.append("name: branding has no MOZ_APP_DISPLAYNAME; Name= not compared")
        elif not name or name.group(1).strip() != want:
            problems.append(f"name: {d} says Name={name.group(1) if name else '(none)'}, the branding says {want}")
        for ex in re.findall(r"^Exec=(.*)$", text, re.M):
            prog = next((w for w in ex.split() if w.startswith("/")), None)
            if prog and prog not in files:
                problems.append(f"exec: {d} runs {prog}, which is not in the package")
        m = re.search(r"^Icon=(.*)$", main, re.M)
        icon = m.group(1).strip() if m else None
    if icon:
        for sz in SIZES:
            p = f"/usr/share/icons/hicolor/{sz}x{sz}/apps/{icon}.png"
            data = read(p)
            if data is None:
                problems.append(f"icons: no {sz}x{sz} icon ({p})")
                continue
            real = png_size(data)
            if real != (sz, sz):
                problems.append(f"icons: {p} is {real[0]}x{real[1] if real else '?'} pixels, not {sz}x{sz}"
                                if real else f"icons: {p} is not a PNG")
    post = _control(deb, "postinst")
    for tool in ("gtk-update-icon-cache", "update-desktop-database"):
        if tool not in post:
            problems.append(f"refresh: postinst does not run {tool}")
    return {"ok": not problems, "deb": str(deb), "problems": problems, "notes": notes, "icon": icon,
            "name": want}


def lines(r):
    out = [f"  {p}" for p in r["problems"]] + [f"  note: {n}" for n in r["notes"]]
    out.append(f"DEB-CHECK {'OK' if r['ok'] else 'NOT OK'}: {Path(r['deb']).name}, menu name {r['name']!r}, "
               f"icon {r['icon']!r}, {len(r['problems'])} problem(s)")
    return out
