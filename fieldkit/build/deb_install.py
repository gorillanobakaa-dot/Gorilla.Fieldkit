# Version: 1.1.0 · updated 26-10-15-16-38
"""Install the newest Gorilla .deb on this machine and prove what the person will see.

Pipeline stage `install` of firefox-linux (after `package`). The only Firefox stage that needs root, kept out of
firefox_linux.py (which promises that nothing there calls sudo). It never asks for a password: `sudo -n` either works
(a cached or passwordless sudo) or the stage stops and prints the exact command for a person to run.

Verified from the installed system, not from the package (owner 2026-10-15: "install the .deb here, set up the icons
and all that (teach the harness how to do it)"):
  version   dpkg-query says the package version the .deb carries
  binary    /usr/lib/gorilla-unleashed/firefox --version names the release
  menu      the installed .desktop Name= is the branding's name, and its Icon= resolves in hicolor at 48 and 256
  caches    the hicolor icon-theme.cache and the desktop mimeinfo.cache are at least as new as the install
  shadow    a personal ~/.local/share/applications/gorilla-unleashed.desktop wins over the system one; it must name the
            same release too (2026-10-15: one from August still said "Gorilla Unleashed 155" over the 158 install)
  icons     no personal copy of the icon (~/.local/share/icons, ~/.icons) wins over the package's crisp per-size files:
            2026-10-15 the app grid and the dock showed two different Gorilla icons because July files in
            ~/.local/share/icons/hicolor (64, 96, 128, a 2048 px picture in 256x256, an SVG wrapping a PNG) shadowed
            the package's 16..1024 set, at some sizes and not others. Each PNG must also be the size its folder says.
  shell     NOTE, not a failure: a GNOME Shell that started before the menu entry or the icons changed keeps showing
            the old name and picture in the app grid (2026-10-15: the grid said "Gorilla Unleashed 155" while the
            file and the dock said 158). On Wayland the shell cannot be restarted: the person logs out and back in.

Where to look by hand, in lookup order: ~/.local/share/applications then /usr/share/applications (the menu entry);
~/.local/share/icons, ~/.icons, then /usr/share/icons/hicolor/<size>/apps (the picture). The grid's app id is in
`gsettings get org.gnome.shell app-picker-layout`; the shell's start time is /proc/<gnome-shell pid>.
"""
import os
import re
import subprocess
from pathlib import Path

PKG = "gorilla-unleashed"
APP = Path("/usr/lib/gorilla-unleashed/firefox")
DESKTOP = Path("/usr/share/applications/gorilla-unleashed.desktop")
PERSONAL = Path.home() / ".local" / "share" / "applications" / DESKTOP.name
HICOLOR = Path("/usr/share/icons/hicolor")


TRASH = Path.home() / "Agents.Work.Trash"          # the house rule: quarantine, never delete
PERSONAL_ICON_DIRS = (Path.home() / ".local" / "share" / "icons", Path.home() / ".icons")


def _png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return int.from_bytes(head[16:20], "big") if head[:8] == b"\x89PNG\r\n\x1a\n" else None


def icon_shadows(icon, dirs=PERSONAL_ICON_DIRS):
    """Personal files named after the app's icon, which GTK finds BEFORE the package's -> [problem]."""
    out = []
    for base in dirs:
        if not base.is_dir():
            continue
        for f in sorted(list(base.glob(f"{icon}.*")) + list(base.glob(f"*/*/apps/{icon}.*"))):
            note = ""
            m = re.match(r"(\d+)x(\d+)$", f.parent.parent.name)
            if m and f.suffix == ".png" and _png_size(f) not in (None, int(m.group(1))):
                note = f" (a {_png_size(f)} px picture in {f.parent.parent.name})"
            out.append(f"icons: personal {f}{note} wins over the package's icon: move it to "
                       f"{TRASH}/ so the package's crisp files are used")
    return out


def shell_started(proc=Path("/proc")):
    """Start time (epoch seconds) of the running gnome-shell, or None."""
    try:
        boot = next(float(l.split()[1]) for l in (proc / "stat").read_text().splitlines() if l.startswith("btime"))
        tick = os.sysconf("SC_CLK_TCK")
        for d in proc.iterdir():
            if d.name.isdigit() and (d / "comm").is_file() and (d / "comm").read_text().strip() == "gnome-shell":
                start = int((d / "stat").read_text().rsplit(")", 1)[1].split()[19])
                return boot + start / tick
    except (OSError, StopIteration, ValueError, IndexError):
        return None
    return None


def stale_shell_note(paths, started):
    """A note when anything the menu shows changed after the shell started (it keeps the old name and picture)."""
    if started is None:
        return []
    newer = [str(p) for p in paths if p.exists() and p.stat().st_mtime > started]
    return [f"shell: GNOME Shell started before {', '.join(newer[:3])} changed, so the app grid can still show the "
            "old name and icon; log out and back in (on Wayland the shell cannot be restarted)"] if newer else []


def _run(cmd, timeout=900):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def newest_deb(release):
    debs = sorted(Path(release).glob(f"{PKG}_*.deb"), key=lambda p: p.stat().st_mtime)
    return debs[-1] if debs else None


def stage_install(ctx, run=_run):
    deb = newest_deb(ctx.vars["release"])
    if deb is None:
        return {"ok": False, "detail": f"no {PKG} .deb in {ctx.vars['release']}: run the package stage"}
    # --reinstall: a repackaged .deb keeps its version (158.0-1); without it apt reports "0 upgraded, 0 newly
    # installed" and leaves the old files (2026-10-15: the D-158-05 name stayed "Gorilla Unleashed" until this).
    cmd = ["/usr/bin/apt-get", "install", "-y", "--reinstall", "--allow-downgrades", str(deb)]
    if run(["sudo", "-n", "true"], timeout=10)[0] != 0:
        return {"ok": False, "detail": "sudo needs a password here; a person runs: sudo " + " ".join(cmd)}
    rc, out = run(["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", *cmd])
    tail = [l for l in out.splitlines() if l.strip()][-4:]
    return {"ok": rc == 0, "detail": f"apt-get install exited {rc}: " + " | ".join(tail), "deb": str(deb)}


def verify_install(ctx, run=_run):
    deb = newest_deb(ctx.vars["release"])
    problems = []
    rc, want = run(["dpkg-deb", "-f", str(deb), "Version"]) if deb else (1, "")
    rc2, have = run(["dpkg-query", "-W", "-f=${Version}", PKG])
    if rc != 0 or rc2 != 0 or want.strip() != have.strip():
        problems.append(f"version: installed {have.strip() or 'nothing'}, the .deb is {want.strip() or '?'}")
    release = ctx.vars.get("version", "")
    rc, out = run([str(APP), "--version"], timeout=60)
    if rc != 0 or release not in out:
        problems.append(f"binary: {APP} --version said {out.strip()[:80]!r}, expected {release}")
    from .deb_check import expected_name, name_problems
    name = expected_name(Path(ctx.vars["src"]) / "browser" / "branding" / "gorilla", have.strip())
    text = DESKTOP.read_text(encoding="utf-8") if DESKTOP.is_file() else ""
    main = text.split("\n[", 1)[0]
    if not text:
        problems.append(f"menu: {DESKTOP} is missing")
    elif name:
        problems += name_problems(text, name, "installed")
    for f in sorted(PERSONAL.parent.glob("*.desktop")) if PERSONAL.parent.is_dir() else []:
        text = f.read_text(encoding="utf-8", errors="replace")
        if "/usr/lib/gorilla-unleashed/firefox" in text:
            problems.append(f"shadow: personal launcher {f} starts /usr/lib/gorilla-unleashed/firefox, so the task "
                            "manager shows 'firefox': point it at /usr/lib/gorilla-unleashed/gorilla-unleashed")
    if name and PERSONAL.is_file():
        problems += [p.replace("installed", f"personal launcher {PERSONAL}", 1)
                     for p in name_problems(PERSONAL.read_text(encoding="utf-8", errors="replace"), name, "installed")]
    icon = re.search(r"^Icon=(.*)$", main, re.M)
    for sz in (48, 256):
        if icon and not (HICOLOR / f"{sz}x{sz}" / "apps" / f"{icon.group(1).strip()}.png").is_file():
            problems.append(f"menu: icon {icon.group(1).strip()} has no {sz}x{sz} file in {HICOLOR}")
    notes = []
    if icon:
        problems += icon_shadows(icon.group(1).strip())
        notes += stale_shell_note([DESKTOP, PERSONAL, HICOLOR / "256x256" / "apps" / f"{icon.group(1).strip()}.png"],
                                  shell_started())
    if DESKTOP.is_file():
        for cache in (HICOLOR / "icon-theme.cache", DESKTOP.parent / "mimeinfo.cache"):
            if not cache.is_file() or cache.stat().st_mtime + 1 < DESKTOP.stat().st_mtime:
                problems.append(f"caches: {cache} is older than the installed menu entry (postinst did not refresh it)")
    detail = "; ".join(problems) if problems else f"{PKG} {have.strip()} installed, menu {name!r}, icons and caches fresh"
    return {"ok": not problems, "notes": notes, "detail": detail + "".join(f"; NOTE {n}" for n in notes)}
