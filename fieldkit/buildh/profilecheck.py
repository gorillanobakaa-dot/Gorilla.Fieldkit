# Version: 1.0.0 · updated 26-10-15-22-29
"""Which profile does the person's browser really use, and does every way of starting it agree?

    fieldkit build-harness profile-check TASK [profile=<intended profile dir>] [paint=1]
    post-install row group "profile"

Born 2026-10-15. After the 158 install a clean profile (~/.mozilla/gorilla-158) was made and the menu launcher pointed
at it; the owner opened Gorilla and the new tab was black. The browser had started on the OLD 154-era profile anyway:
GNOME Shell kept the menu entry it loaded at login, and nothing checked which profile the new build actually ran on.
Measured with newtab-paint on copies: old profile 0 lit pixels in 7 of 7 new tabs, new profile drawn in 7 of 7.
The owner: "create all the deterministic checks and tools ... to make sure we don't end up in this position again".

Deterministic, no model, read-only:
  profiles(roots)        every Firefox profile folder under ~/.mozilla and ~/.config/mozilla: the version that last
                         ran it (compatibility.ini LastVersion and LastPlatformDir) and when (its lock or prefs.js)
  ran_by(install)        the profiles this install has run, newest first
  launchers()            every .desktop entry (personal, system, autostart) and ~/.local/bin script that starts the
                         install, and the profile each names (None = Firefox picks one from profiles.ini)
  rows(install, want)    the verdicts: the newest profile this install ran is the intended one; every launcher names
                         it; no OTHER profile was run by this build; GNOME Shell's cached menu is not older than the
                         launcher; and (paint=1) the new tab draws on a COPY of the intended profile (probe newtab-paint)
"""
import configparser
import os
import re
import time
from pathlib import Path

HOME = Path.home()
ROOTS = (HOME / ".mozilla", HOME / ".config" / "mozilla")
LAUNCHER_DIRS = (HOME / ".local" / "share" / "applications", Path("/usr/share/applications"),
                 HOME / ".config" / "autostart", HOME / "Desktop")
SCRIPT_DIRS = (HOME / ".local" / "bin",)
PROFILE_ARG = re.compile(r"(?:-profile|--profile)\s+(\"[^\"]+\"|'[^']+'|\S+)")
PROFILE_VAR = re.compile(r"^\s*PROFILE=(\"[^\"]+\"|'[^']+'|\S+)", re.M)


def _when(p):
    """When a profile was last used: its lock link or .parentlock, else prefs.js, else the folder."""
    for f in ("lock", ".parentlock", "prefs.js", "compatibility.ini"):
        q = p / f
        try:
            return q.lstat().st_mtime
        except OSError:
            continue
    return p.stat().st_mtime


def profiles(roots=ROOTS):
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("compatibility.ini")):
            d = p.parent
            cp = configparser.ConfigParser(interpolation=None)
            try:
                cp.read(p, encoding="utf-8")
                last = cp.get("Compatibility", "LastVersion", fallback="")
                plat = cp.get("Compatibility", "LastPlatformDir", fallback="")
            except (configparser.Error, OSError):
                last, plat = "", ""
            out.append({"path": str(d), "last_version": last.split("_")[0], "last_install": plat,
                        "when": _when(d), "has_prefs": (d / "prefs.js").is_file()})
        for d in sorted(x for x in root.iterdir() if x.is_dir()):
            if not (d / "compatibility.ini").exists() and (d / "places.sqlite").is_file() and \
                    not any(o["path"] == str(d) for o in out):
                out.append({"path": str(d), "last_version": "", "last_install": "", "when": _when(d),
                            "has_prefs": (d / "prefs.js").is_file()})
    return out


def ran_by(install, found):
    install = str(Path(install).resolve())
    return sorted((p for p in found if p["last_install"] and str(Path(p["last_install"]).resolve()) == install),
                  key=lambda p: -p["when"])


def _unquote(s):
    return s.strip("\"'")


def launchers(install, dirs=LAUNCHER_DIRS, scripts=SCRIPT_DIRS):
    """-> [{"file", "profile" (None: no -profile), "kind"}] for everything that starts `install`."""
    install = str(install).rstrip("/")
    out = []
    for d in dirs:
        for f in sorted(d.glob("*.desktop")) if d.is_dir() else []:
            text = f.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("Exec=") and install in line:
                    m = PROFILE_ARG.search(line)
                    entry = {"file": str(f), "profile": _unquote(m.group(1)) if m else None, "kind": "menu",
                             "line": line[5:][:160]}
                    if not any(e["file"] == entry["file"] and e["profile"] == entry["profile"] for e in out):
                        out.append(entry)                    # one row per file and profile (actions repeat the Exec)
    for d in scripts:
        for f in sorted(d.iterdir()) if d.is_dir() else []:
            if not f.is_file() or f.stat().st_size > 200_000:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if install in text or "gorilla-unleashed" in text:
                # a script names its profile in PROFILE=...; its -profile then reads "$PROFILE"
                m = PROFILE_VAR.search(text) or PROFILE_ARG.search(text)
                out.append({"file": str(f), "profile": _unquote(m.group(1)) if m else None, "kind": "script",
                            "line": ""})
    return out


def shell_started():
    from ..build.deb_install import shell_started as s
    return s()


def rows(install, want, found=None, starters=None, shell=None, paint=None):
    """want: the intended profile folder. paint: callable(profile) -> (ok, evidence) or None to skip the pixels."""
    want = str(Path(want).expanduser()) if want else None
    found = profiles() if found is None else found
    starters = launchers(install) if starters is None else starters
    out = []
    ran = ran_by(install, found)
    newest = ran[0]["path"] if ran else None
    if not want:
        out.append({"check": "profile: an intended profile is named", "ok": False,
                    "evidence": "no profile= given and none recorded: say which profile this build is for"})
        return out
    out.append({"check": "profile: the newest profile this build ran is the intended one", "ok": newest == want,
                "evidence": (f"{newest} last ran {install} at {time.strftime('%Y-%m-%d %H:%M', time.localtime(ran[0]['when']))}"
                             if ran else f"this build has run no profile yet (open it once on {want})")
                            + ("" if newest == want else f"; intended {want}")})
    others = [p for p in ran if p["path"] != want]
    out.append({"check": "profile: no other profile was run by this build", "ok": not others,
                "evidence": "none" if not others else "; ".join(
                    f"{p['path']} (last {time.strftime('%Y-%m-%d %H:%M', time.localtime(p['when']))})" for p in others)})
    wrong = [s for s in starters if s["profile"] not in (want, None)]
    unnamed = [s for s in starters if s["profile"] is None and s["kind"] == "menu" and "/usr/share/" not in s["file"]]
    out.append({"check": "profile: every launcher of this build names the intended profile", "ok": not wrong,
                "evidence": f"{len(starters)} launcher(s)" + ("" if not wrong else "; wrong: " + "; ".join(
                    f"{s['file']} -> {s['profile']}" for s in wrong))
                            + ("" if not unnamed else "; no profile named (Firefox picks one): " +
                               ", ".join(s["file"] for s in unnamed))})
    started = shell_started() if shell is None else shell
    personal = [Path(s["file"]) for s in starters if s["kind"] == "menu" and str(HOME) in s["file"]]
    newer = [str(p) for p in personal if started and p.exists() and p.stat().st_mtime > started]
    out.append({"check": "profile: the desktop's menu has read the current launcher", "ok": not newer,
                "evidence": "the shell started after every launcher changed" if not newer else
                ("GNOME Shell started before " + ", ".join(newer) + " changed: its menu still starts the OLD command. "
                 "Log out and back in (Wayland cannot restart the shell)")})
    if paint:
        ok, ev = paint(want)
        out.append({"check": "profile: the new tab draws on a copy of the intended profile (pixels)", "ok": ok,
                    "evidence": ev})
    return out


def paint_probe(install, timeout=200, say=print):
    """newtab-paint on a COPY of a profile -> (ok, evidence): every NTPAINT line must say DRAWN."""
    from . import probe

    def run(profile):
        r = probe.run(install, "newtab-paint", url="about:newtab", wait=12, timeout=timeout, say=say,
                      profile=profile)
        got = [l.split("|") for l in r.get("lines", []) if l.startswith("NTPAINT|")]
        black = [g[1] for g in got if g[-1] != "DRAWN"]
        if not got:
            return False, "the probe reported nothing (it did not run): unchecked is not passed"
        return not black, (f"{len(got) - len(black)} of {len(got)} new tab(s) drawn" +
                           ("" if not black else "; BLACK: " + ", ".join(black)))
    return run


RECORD = HOME / ".local" / "state" / "fieldkit" / "intended-profile"


def intended():
    """The profile profile-fresh made for the current build (written there by it), else None."""
    try:
        return RECORD.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def record(profile):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(str(Path(profile).expanduser()) + "\n", encoding="utf-8")
