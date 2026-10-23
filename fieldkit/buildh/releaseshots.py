# Version: 1.0.0 · updated 26-10-23-19-55
"""The release page's pictures, taken from the real screen while the installed build shows each surface.

    fieldkit build-harness release-shots TASK out=DIR [prefix=158.0-2-] [--install-dir DIR]

Directive 13: screenshots are evidence, full size, unscaled, of the real screen. Until 2026-10-23 they were taken by
hand, by the maintainer, on an earlier build ("taken on the build before this one; what they show did not change").
This opens the installed build in a VISIBLE window on a fresh profile (nothing personal on screen), lets the
release-shots probe open each surface (Help > About, the app menu, the search-mode list, a right-click menu), and
captures the whole screen with the desktop's own tool at each SHOT line. Nothing is scaled, cropped or re-encoded.
Born 2026-10-23, Linux 158.0-2. Announce it first: the window takes the screen for about half a minute.
"""
import shutil
import subprocess
from pathlib import Path

TOOLS = (["gnome-screenshot", "-f"], ["scrot", "-o"], ["import", "-window", "root"])


def capture_cmd(which=shutil.which):
    """The first screen-capture tool installed, as a command prefix taking the output path last, or None."""
    for t in TOOLS:
        if which(t[0]):
            return list(t)
    return None


def parse(line):
    """'SHOT|name|what' -> ("shot", name, what); 'SHOT|skip|name|why' -> ("skip", name, why); 'SHOT|end' -> ("end",)."""
    f = line.split("|")
    if not f or f[0] != "SHOT":
        return None
    if len(f) >= 2 and f[1] == "end":
        return ("end",)
    if len(f) >= 4 and f[1] == "skip":
        return ("skip", f[2], f[3])
    if len(f) >= 3:
        return ("shot", f[1], "|".join(f[2:]))
    return None


def run(install_dir, out_dir, prefix="", say=print, capture=None, runner=subprocess.run, probe_run=None):
    """-> {"ok", "shots": [{"name", "file", "proves"}], "skipped": [(name, why)]}"""
    from . import probe
    cmd = capture or capture_cmd()
    if not cmd:
        return {"ok": False, "shots": [], "skipped": [("all", "no screen-capture tool (gnome-screenshot, scrot, import)")]}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shots, skipped = [], []

    def on_line(line):
        p = parse(line)
        if not p or p[0] == "end":
            return
        if p[0] == "skip":
            skipped.append((p[1], p[2]))
            say(f"  skip {p[1]}: {p[2]}")
            return
        f = out / f"{prefix}{p[1]}.png"
        r = runner(cmd + [str(f)], capture_output=True, text=True, timeout=20)
        if r.returncode == 0 and f.is_file() and f.stat().st_size > 0:
            shots.append({"name": p[1], "file": str(f), "proves": p[2]})
            say(f"  shot {f.name}: {p[2]}")
        else:
            skipped.append((p[1], f"capture failed: {(r.stderr or '').strip()[:120]}"))

    (probe_run or probe.run)(install_dir, "release-shots", wait=8, timeout=120, headless=False, on_line=on_line,
                             say=lambda m: None)
    return {"ok": bool(shots) and not skipped, "shots": shots, "skipped": skipped}
