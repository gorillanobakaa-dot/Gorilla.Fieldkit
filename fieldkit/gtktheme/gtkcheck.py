#!/usr/bin/env python3
# Version: 1.1.0 · updated 26-10-22-14-20
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

Added 2026-10-22, after the owner's Files and Settings came up light after a login ("I never had a theme being
scrambled on me before"):

  4. SCRAMBLED  Two login launchers ran the installer at the same instant and wrote the same gtk.css through one
                shared temp name: each file became two interleaved copies (318 and 158 lines instead of 159 and 79),
                comments broken mid-way, and GTK dropped the lot. Seen here as the theme's BEGIN marker more than
                once, or BEGIN/END markers that do not pair up.
  5. LAUNCHERS  The second launcher was a leftover: the theme's old package (gtk-high-contrast-theme) had been
                removed but not purged, so its autostart .desktop stayed (dpkg state rc) and still ran, through a
                compatibility symlink, the SAME program as the new one. Seen here as two autostart entries whose
                Exec resolves to one program; the owning package and its dpkg state are named.

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


def scrambled(text, marker=MARKER):
    """Why a stylesheet's theme block is broken, or None. Counts the markers in order: a clean file has BEGIN, END,
    BEGIN, END ... (normally once). Two runs writing at once leave BEGIN twice before an END."""
    marks = re.findall(r"/\* (BEGIN|END) " + re.escape(marker) + r" \*/", text)
    if not marks:
        return None
    depth, worst = 0, 0
    for m in marks:
        depth += 1 if m == "BEGIN" else -1
        worst = max(worst, depth)
        if depth < 0:
            return f"an END marker before its BEGIN ({marks.count('BEGIN')} BEGIN, {marks.count('END')} END)"
    if worst > 1 or depth != 0:
        return f"BEGIN/END markers do not pair up ({marks.count('BEGIN')} BEGIN, {marks.count('END')} END, in order: {' '.join(marks)})"
    if marks.count("BEGIN") > 1:
        return f"the theme block is there {marks.count('BEGIN')} times"
    return None


AUTOSTART = ("/etc/xdg/autostart", os.path.expanduser("~/.config/autostart"))


def _desktops():
    """This session's desktop names (XDG_CURRENT_DESKTOP, e.g. "ubuntu:GNOME"); empty when not in a desktop session."""
    return {d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}


def _exec_program(desktop, desktops=None):
    """(resolved program, arguments) of a .desktop file's Exec line, or None when it does not start on this desktop.
    OnlyShowIn/NotShowIn count: xdg-user-dirs ships one entry for KDE and one for the rest, same program, never both
    run. Outside a desktop session (over SSH) an OnlyShowIn entry cannot be confirmed and is left out."""
    desktops = _desktops() if desktops is None else desktops
    try:
        with open(desktop, errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    if re.search(r"^Hidden\s*=\s*true", text, re.M | re.I):
        return None                         # a user override that switches the system entry off
    only = re.search(r"^OnlyShowIn\s*=\s*(.+)$", text, re.M)
    if only and not desktops & {d.strip().lower() for d in only.group(1).split(";") if d.strip()}:
        return None
    never = re.search(r"^NotShowIn\s*=\s*(.+)$", text, re.M)
    if never and desktops & {d.strip().lower() for d in never.group(1).split(";") if d.strip()}:
        return None
    m = re.search(r"^Exec\s*=\s*(.+)$", text, re.M)
    if not m:
        return None
    parts = m.group(1).split()
    prog = parts[0]
    if not os.path.isabs(prog):
        for d in os.environ.get("PATH", "/usr/bin:/bin").split(":"):
            if os.path.exists(os.path.join(d, prog)):
                prog = os.path.join(d, prog)
                break
    return os.path.realpath(prog), " ".join(p for p in parts[1:] if not p.startswith("%"))


def _owner(path):
    """(package, dpkg state) owning a file, or (None, None) when dpkg does not know it."""
    try:
        r = subprocess.run(["dpkg-query", "-S", path], capture_output=True, text=True, timeout=10)
        pkg = r.stdout.split(":", 1)[0].strip() if r.returncode == 0 else None
        if not pkg:
            return None, None
        st = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", pkg], capture_output=True, text=True,
                            timeout=10).stdout.strip()
        return pkg, st or None
    except (OSError, subprocess.SubprocessError):
        return None, None


def duplicate_launchers(dirs=AUTOSTART, owner=_owner, desktops=None):
    """Autostart entries that start the same program with the same arguments at login: they run at the same
    instant and race on whatever that program writes. A user entry in ~/.config/autostart with the same file name
    replaces the system one, so only the effective entry of each name counts. -> [[{file, program, package,
    state}, ...], ...]"""
    effective = {}
    for d in dirs:                          # later dirs (the user's) replace earlier ones by file name
        for p in sorted(glob.glob(os.path.join(d, "*.desktop"))):
            effective[os.path.basename(p)] = p
    groups = {}
    for p in effective.values():
        e = _exec_program(p, desktops)
        if e:
            groups.setdefault(e, []).append(p)
    out = []
    for (prog, args), files in sorted(groups.items()):
        if len(files) > 1:
            out.append([{"file": f, "program": prog, "args": args, **dict(zip(("package", "state"), owner(f)))}
                        for f in files])
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
    for css in (CSS4, CSS3):
        try:
            with open(css, errors="replace") as f:
                why = scrambled(f.read())
        except OSError:
            why = None
        if why:
            findings.append({"check": "scrambled", "toolkit": "", "what": f"{css}: {why}",
                             "fix": f"first find who wrote it twice (the launchers finding below; journalctl --user -b "
                                    f"| grep -i theme); then move {css} to {os.path.expanduser('~/Agents.Work.Trash')}/ and run "
                                    f"{PACKAGE} apply once"})
    theme_prog = os.path.realpath(PACKAGE)
    for group in duplicate_launchers():
        leftovers = [g["package"] for g in group if g["state"] and g["state"].startswith("rc")]
        if group[0]["program"] != theme_prog and not leftovers:
            # same program twice, both packages installed (Debian's desktop-base ships a KDE copy of
            # xdg-user-dirs.desktop): real, but not this check's business. Listed, not a finding.
            info.append(f"also started twice at login, both packages installed: {group[0]['program']} "
                        f"({', '.join(os.path.basename(g['file']) for g in group)})")
            continue
        names = ", ".join(f"{g['file']} (package {g['package'] or 'none'}"
                          f"{', dpkg state ' + g['state'] if g['state'] else ''})" for g in group)
        fix = (f"purge the removed package's leftover: /usr/bin/sudo /usr/bin/dpkg --purge {' '.join(leftovers)}"
               if leftovers else "keep one: the other belongs to an old install (dpkg-query -S <file> names it); "
                                 "move it to " + os.path.expanduser("~/Agents.Work.Trash") + "/")
        findings.append({"check": "launchers", "toolkit": "", "what": f"{len(group)} login launchers start "
                         f"{group[0]['program']} {group[0]['args']} at the same instant: {names}", "fix": fix})
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
    c = sp.add_parser("check", help="unfocused-window colours, fades and greys the user stylesheet misses; a "
                                     "scrambled stylesheet and the duplicate login launchers that scramble it; apps and "
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
