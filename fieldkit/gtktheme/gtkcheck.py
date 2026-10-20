#!/usr/bin/env python3
# Version: 1.0.0 · updated 26-10-20-18-50
"""gtkcheck.py - does a GTK user stylesheet hold when a window loses focus, and is the desktop showing it.

    fieldkit gtk-theme check [--json]          read-only; exit 0 clean, 3 findings

Born 2026-10-20 from the Black Gorilla Theme (Documents/gtk-high-contrast-theme). The owner saw the Files sidebar
turn light grey and the header bar fade every time another window was on top. Three separate mistakes, each now a
check here, so the next session runs this instead of guessing:

  1. NAMES  A theme built on GTK's named colours (@define-color window_bg_color ...) set the FOCUSED names only.
            Toolkits keep a second set for an unfocused ("backdrop") window: libadwaita reads
            --sidebar-backdrop-color from @sidebar_backdrop_color, GTK3 reads @theme_unfocused_* and friends.
            Unset, they fall back to the toolkit's own (light #f2f2f4 with color-scheme=default).
  2. FADES  Toolkits also dim unfocused windows with filter: opacity(0.5) and fixed greys (#919190) that no colour
            name reaches. Each must be cancelled by selector in the user stylesheet, which outranks the theme
            whatever the specificity.
  3. STALE  A fixed stylesheet changes nothing on screen until the app starts again, and closing the window is not
            starting again: Files runs as `nautilus --gapplication-service` and survives it (the fix "did not
            work" for 15 minutes because Files had been running since 15:23). A package upgrade that only applies
            "once" never rewrites the CSS either.

The rules are read from the toolkit libraries THIS machine has (their embedded CSS), never from a list typed here,
so a new libadwaita with a new fade is caught the day it is installed.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time

STATE = ":backdrop"
CSS4 = os.path.expanduser("~/.config/gtk-4.0/gtk.css")
CSS3 = os.path.expanduser("~/.config/gtk-3.0/gtk.css")
LIBS = {                                   # library -> the user stylesheet that styles it
    "libadwaita": ("libadwaita-1.so.0", CSS4),
    "gtk4": ("libgtk-4.so.1", CSS4),
    "gtk3": ("libgtk-3.so.0", CSS3),
}
PACKAGE = "/usr/bin/black-gorilla-theme"   # the packaged installer, if this machine has it
MARKER = "black-gorilla-theme"


def find_lib(soname):
    for d in ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib"):
        hits = sorted(glob.glob(os.path.join(d, soname + "*")))
        hits = [h for h in hits if not os.path.islink(h)] or hits
        if hits:
            return hits[-1]
    return None


def embedded_rules(path):
    """Every CSS rule ('selector { body }') stored as plain text inside a toolkit library, plus its colour
    definitions as ('@define-color', 'name value') pairs: GTK3 declares its unfocused colours that way, outside any
    rule, so a reader of rules alone never sees them (the first version of this check had exactly that blind spot)."""
    with open(path, "rb") as f:
        data = f.read()
    rules = set()
    for m in re.finditer(rb"[\x20-\x7e\t]{8,}", data):
        raw = m.group(0)
        if b"@define-color" in raw:
            for d in re.finditer(r"@define-color\s+([a-z_]+)\s+([^;]+)", raw.decode("ascii", "replace")):
                rules.add(("@define-color", f"{d.group(1)} {d.group(2).strip()}"))
        if b"{" not in raw or b"}" not in raw:
            continue                        # most of a library is code and symbol names: no rule to read
        for r in re.finditer(r"([^{}]+)\{([^{}]*)\}", raw.decode("ascii", "replace")):
            rules.add((r.group(1).strip(), r.group(2).strip()))
    return rules


def css_rules(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"@[a-z-]+[^{};]*;", "", text)      # @define-color / @import statements: not part of a selector
    return [(s.strip(), b.strip()) for s, b in re.findall(r"([^{}]+)\{([^{}]*)\}", text)]


def _parts(selector):
    out, depth, cur = [], 0, ""
    for ch in selector:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [" ".join(p.split()) for p in out + [cur] if p.strip()]


def base(sel):
    """'button:backdrop:not(:disabled).flat' -> 'button.flat': the element without state and exclusions."""
    s = re.sub(r":not\([^()]*(\([^()]*\))?[^()]*\)", "", sel).replace(STATE, "")
    return " ".join(s.split())


def defined_names(text):
    return set(re.findall(r"@define-color\s+([a-z_]+)", re.sub(r"/\*.*?\*/", "", text, flags=re.S)))


def check_names(lib_rules, user_text, kind):
    """Backdrop colour NAMES the toolkit reads that the user stylesheet never defines."""
    blob = "\n".join(f"{s}{{{b}}}" for s, b in lib_rules if s != "@define-color")
    if kind == "gtk3":
        wanted = {b.split()[0] for s, b in lib_rules if s == "@define-color"
                  and re.match(r"(theme_)?unfocused_|wm_unfocused_", b)}
    else:
        wanted = set(re.findall(r"--[a-z-]+-backdrop-color:\s*@([a-z_]+)", blob))
    return sorted(wanted - defined_names(user_text))


def check_fades(lib_rules, user_text):
    """Backdrop rules that fade (filter: opacity / opacity < 1) with no same-selector cancel in the user sheet."""
    cancels = set()
    for s, b in css_rules(user_text):
        if re.search(r"filter:\s*none|opacity:\s*1(\.0)?\s*(;|$)", b):
            cancels.update(_parts(s))
    missing = set()
    for s, b in lib_rules:
        if STATE not in s or not re.search(r"filter:\s*opacity\(0?\.\d|(^|[^-])opacity:\s*0?\.\d", b):
            continue
        for p in _parts(s):
            if STATE in p and p not in cancels:
                missing.add(p)
    return sorted(missing)


def check_greys(lib_rules, user_text):
    """Backdrop rules giving text a fixed colour, whose element the user sheet never colours. -> (root, others)"""
    coloured = set()
    for s, b in css_rules(user_text):
        if re.search(r"(^|;|\s)color:", b):
            coloured.update(base(p) for p in _parts(s))
    out = set()
    for s, b in lib_rules:
        m = re.search(r"(^|;|\s)color:\s*([^;]+)", b)
        if STATE not in s or not m or m.group(2).strip() in ("transparent", "inherit", "currentColor"):
            continue
        for p in _parts(s):
            if STATE in p and base(p) not in coloured:
                out.add(p)
    root = sorted(p for p in out if base(p) in (".background", "window"))
    return root, sorted(out - set(root))


def _boot_time():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("btime"):
                return int(line.split()[1])
    return 0


# Programs that load GTK but never draw a themed window: login daemons and the shell (St, not GTK CSS).
# The portals stay: they draw the file chooser every app opens.
NO_WINDOW = re.compile(r"^/usr/libexec/(?!xdg-desktop-portal)|^/usr/bin/gnome-shell$")


def stale_apps():
    """GTK programs of this user that started BEFORE the stylesheet they use was last written: they still draw the
    old one. One entry per program (its oldest process), never one per child process."""
    tick, boot, uid, me = os.sysconf("SC_CLK_TCK"), _boot_time(), os.getuid(), os.getpid()
    mtimes = {p: os.path.getmtime(p) for p in (CSS3, CSS4) if os.path.exists(p)}
    best = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if int(pid) == me or os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            exe = os.readlink(f"/proc/{pid}/exe")
            if NO_WINDOW.search(exe):
                continue
            with open(f"/proc/{pid}/maps") as f:
                maps = f.read()
            css = CSS4 if "libgtk-4.so" in maps else CSS3 if "libgtk-3.so" in maps else None
            if css not in mtimes:
                continue
            with open(f"/proc/{pid}/stat") as f:
                started = boot + int(f.read().rsplit(")", 1)[1].split()[19]) / tick
            if started >= mtimes[css] or (exe in best and best[exe]["t"] <= started):
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
            best[exe] = {"pid": int(pid), "exe": exe, "t": started, "css": css,
                         "started": time.strftime("%H:%M:%S", time.localtime(started)),
                         "css_written": time.strftime("%H:%M:%S", time.localtime(mtimes[css])),
                         "service": "--gapplication-service" in cmd}
        except (OSError, ValueError, IndexError):
            continue
    return [{k: v for k, v in d.items() if k != "t"} for d in sorted(best.values(), key=lambda d: d["t"])]


def _block(text):
    m = re.search(r"/\* BEGIN " + MARKER + r" \*/.*?/\* END " + MARKER + r" \*/", text, re.S)
    return m.group(0) if m else None


def stale_package(installer=PACKAGE):
    """The theme block in the live stylesheets differs from what the installed package writes (an upgrade that never
    reached this user). Runs the installer into a throwaway HOME only. -> list of stylesheets that differ."""
    if not os.path.exists(installer):
        return []
    live = {}
    for p in (CSS4, CSS3):
        try:
            with open(p) as f:
                live[p] = _block(f.read())
        except OSError:
            live[p] = None
    if not any(live.values()):
        return []                           # the theme is not applied (never installed, or reverted): nothing to refresh
    with tempfile.TemporaryDirectory() as home:
        subprocess.run([installer, "--apply-once"], env={**os.environ, "HOME": home}, capture_output=True, timeout=60)
        out = []
        for p in (CSS4, CSS3):
            fresh = os.path.join(home, os.path.relpath(p, os.path.expanduser("~")))
            try:
                with open(fresh) as f:
                    want = _block(f.read())
            except OSError:
                continue
            if want and live[p] != want:
                out.append(p)
    return out


def check(self_cmd="fieldkit"):
    findings, info = [], []
    for kind, (soname, css) in LIBS.items():
        lib = find_lib(soname)
        if not lib:
            info.append(f"{kind}: {soname} not installed, not checked")
            continue
        try:
            with open(css) as f:
                user = f.read()
        except OSError:
            user = ""
        rules = embedded_rules(lib)
        n = sum(1 for s, _ in rules if STATE in s)
        info.append(f"{kind}: {n} unfocused-window rules read from {lib}")
        for name in check_names(rules, user, kind):
            findings.append({"check": "names", "toolkit": kind, "what": name,
                             "fix": f"@define-color {name} <the focused colour>;  in {css}"})
        if kind != "gtk3":
            for sel in check_fades(rules, user):
                findings.append({"check": "fades", "toolkit": kind, "what": sel,
                                 "fix": f"{sel} {{ filter: none; }}  in {css}"})
        if kind != "libadwaita":
            root, others = check_greys(rules, user)
            for sel in root:
                findings.append({"check": "greys", "toolkit": kind, "what": sel,
                                 "fix": f"{sel} {{ color: <the focused text colour>; }}  in {css}"})
            if others:
                info.append(f"{kind}: {len(others)} unfocused rules give a fixed text colour to an element the user "
                            f"sheet never colours (they matter only if that element shows text): --json lists them")
    for app in stale_apps():
        name = os.path.basename(app["exe"])
        if name.startswith("xdg-desktop-portal"):
            how = f"/usr/bin/systemctl --user restart {name}   (it draws the file chooser for every app)"
        elif app["service"]:
            how = (f"closing its window does not end it: end the service ({app['exe']} --quit where it has one; "
                   f"Files: /usr/bin/nautilus -q), then open it")
        else:
            how = "close it and open it again"
        findings.append({"check": "stale-app", "toolkit": "", "what": f"{app['exe']} (pid {app['pid']}) started "
                         f"{app['started']}, {app['css']} written {app['css_written']}", "fix": how})
    for css in stale_package():
        findings.append({"check": "stale-package", "toolkit": "", "what": f"{css} differs from what {PACKAGE} writes",
                         "fix": f"if the installed package is the newer one: {PACKAGE}  (no sudo, rewrites only the theme "
                                f"block); if the theme source is newer: rebuild and install the package"})
    return findings, info


def main(argv=None, prog="gtkcheck.py"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("check", help="unfocused-window colours, fades and greys the user stylesheet misses; apps and "
                                     "packages still showing an old stylesheet (read-only; exit 3 = findings)")
    c.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("not-this-platform: GTK user stylesheets are checked on Linux only")
        return 0
    findings, info = check()
    if a.json:
        out = {"ok": not findings, "findings": findings, "info": info}
        for kind, (soname, css) in LIBS.items():
            lib = find_lib(soname)
            if lib and kind != "libadwaita":
                try:
                    with open(css) as f:
                        user = f.read()
                except OSError:
                    user = ""
                out[f"{kind}_uncoloured"] = check_greys(embedded_rules(lib), user)[1]
        print(json.dumps(out, indent=1))
    else:
        for line in info:
            print("  " + line)
        for f in findings:
            print(f"FINDING {f['check']}{' ' + f['toolkit'] if f['toolkit'] else ''}: {f['what']}")
            print(f"  fix: {f['fix']}")
        print("CLEAN: every unfocused-window rule is covered and the desktop is showing the current stylesheet"
              if not findings else f"{len(findings)} finding(s)")
    return 3 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
