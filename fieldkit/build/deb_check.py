# Version: 1.2.0 · updated 26-10-15-11-31
"""What a Gorilla .deb gives the person who installs it: the menu entry, the launcher, crisp icons, the refresh.

    verify of the firefox-linux pipeline package stage (fieldkit.build.firefox_linux:verify_deb)

Born 2026-10-15 on the first Linux 158 package: the menu entry still read "Gorilla Unleashed 154" (the template was
never stamped; 155 shipped the same), and the post-install script refreshed the icon cache but not the menu database.
Owner: "denumirea de firefox, crearea iconitelor crisp, instalarea si impachetarea" must not be forgotten.

Each finding is deterministic, read from the package itself (dpkg-deb), never from the template it was made from:
  name      D-158-05 (owner 2026-10-15): "Gorilla Unleashed followed by the version" wherever a desktop shows or
            searches the browser. expected_name() = the branding's MOZ_APP_DISPLAYNAME + the package's major version;
            Name=, X-GNOME-FullName= equal it, Comment= (the hover tooltip on many docks) contains it, Keywords= has
            every word of it
  exec      every Exec= program path is a file inside the package
  icons     the Icon= name exists under hicolor/NxN/apps for the sizes desktops ask for, and each PNG's real pixel
            size (read from its IHDR header) is exactly NxN: a stretched or non-square icon renders blurred or small
  refresh   postinst runs gtk-update-icon-cache and update-desktop-database
  identity  RemotingName in application.ini equals the entry's StartupWMClass (else a window started without --name
            shows as "firefox-default" with a generic icon: 2026-10-15), and the program gorilla-unleashed is in the
            package and is what the entry starts (the task manager shows the file name)
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


def expected_name(branding, version):
    """D-158-05: "<MOZ_APP_DISPLAYNAME> <major version>", e.g. "Gorilla Unleashed 158". None when either is unknown."""
    base = display_name(branding)
    major = re.match(r"(\d+)", version or "")
    return f"{base} {major.group(1)}" if base and major else None


def name_problems(desktop_text, want, where):
    """Every name field of the [Desktop Entry] group checked against D-158-05. -> [problem, ...]"""
    main = desktop_text.split("\n[", 1)[0]
    field = lambda k: (re.search(rf"^{k}=(.*)$", main, re.M) or [None, None])[1]
    out = []
    for key in ("Name", "X-GNOME-FullName"):
        if (field(key) or "").strip() != want:
            out.append(f"name: {where} {key}={field(key)} , D-158-05 wants {want}")
    if want not in (field("Comment") or ""):
        out.append(f"name: {where} Comment={field('Comment')} does not name {want} (the hover tooltip)")
    kws = [k for k in (field("Keywords") or "").split(";") if k]
    missing = [w for w in want.split() if w not in kws]
    if missing:
        out.append(f"name: {where} Keywords= lacks {', '.join(missing)} (desktop search)")
    return out


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
    version = subprocess.run(["dpkg-deb", "-f", str(deb), "Version"], capture_output=True, text=True).stdout.strip()
    want = expected_name(branding, version)
    icon = None
    for d in desktops:
        text = read(d).decode("utf-8", "replace")
        main = text.split("\n[", 1)[0]                       # the [Desktop Entry] group, not the actions
        if want is None:
            problems.append("name: cannot form the D-158-05 name (branding MOZ_APP_DISPLAYNAME or package version)")
        else:
            problems += name_problems(text, want, d)
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
    ini = read("/usr/lib/gorilla-unleashed/application.ini")
    remoting = re.search(r"^RemotingName=(.*)$", ini.decode("utf-8", "replace"), re.M) if ini else None
    for d in desktops:
        main = read(d).decode("utf-8", "replace").split("\n[", 1)[0]
        wm = re.search(r"^StartupWMClass=(.*)$", main, re.M)
        if wm and (not remoting or remoting.group(1).strip() != wm.group(1).strip()):
            problems.append(f"identity: application.ini RemotingName={remoting.group(1) if remoting else '(none)'}, "
                            f"the entry's StartupWMClass={wm.group(1)}: a plain launch shows under the wrong name")
        ex = re.search(r"^Exec=(.*)$", main, re.M)
        if ex and "/usr/lib/gorilla-unleashed/gorilla-unleashed" not in ex.group(1):
            problems.append(f"identity: {d} does not start /usr/lib/gorilla-unleashed/gorilla-unleashed "
                            "(the task manager would show 'firefox')")
    if "/usr/lib/gorilla-unleashed/gorilla-unleashed" not in files:
        problems.append("identity: no /usr/lib/gorilla-unleashed/gorilla-unleashed program in the package")
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
