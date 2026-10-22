#!/usr/bin/env python3
# Version: 1.1.0 · updated 26-10-22-16-10
"""leftovers.py - what removed packages, other desktops, virtual machines and old installs left behind on a Linux
desktop, shown to the person first, then cleaned without touching the running session.

    fieldkit leftovers scan [--json]                 read-only; exit 0 nothing to clean, 3 findings
    fieldkit leftovers clean [ID ...] [--apply]      the plan (default), or carry it out; every move is recorded
    fieldkit leftovers restore RUN_FOLDER [--apply]  put a cleaned run back
    fieldkit leftovers doctor                        the programs this tool needs

Born 2026-10-22. After a login the owner's Files and Settings came up light: two autostart launchers started the
same theme tool at once and scrambled its stylesheet. One of them was the leftover of a package removed but never
purged. The owner: "most of the people on linux would have 60 different launchers, remnants, leftovers and all that
crap left after they tried multiple DEs and VMs ... find all those orphans, leftovers, conflicting launchers, present
the user with the findings and afterwards remove them without crashing the current setup, so the user can log back
in and keep a pristine install."

WHAT COUNTS (each is a "kind"; the action is fixed per kind, never guessed per file)
  removed-package    dpkg state rc: the package is gone, its config files (autostart entries among them) stay
                     and still act. Action: copy every config file into the run folder, then dpkg --purge.
  autostart-duplicate  two login launchers start the same program with the same arguments at the same instant:
                     they race on whatever it writes. Action: hide the copy that does not belong to the program's
                     own package (a user-level Hidden=true entry; the system file is not touched).
  autostart-dead     a login launcher whose program is gone. A user file is moved out; a system file is hidden.
  vm-guest-agent     a virtual machine's guest helper (spice-vdagent, VBoxClient, vmware-user ...) starting at
                     login on a machine that is not a virtual machine. Action: hide it.
  menu-dead          an application menu entry in your own folder whose program (or Wine prefix) is gone.
                     Action: move it out.
  unit-dead          a systemd user service whose program is gone, or a broken link in a .wants folder.
                     Action: move it out (systemctl --user disable first when it is enabled).
  session-dead       a login-screen session whose program is gone and that no installed package owns.
                     Reported; moving it needs root and is done only when named.
  REPORTED ONLY, never acted on: launchers that run only on desktops not installed here (they never start);
  menu entries no package owns but whose program exists (often deliberate); other desktops installed (removing
  a desktop is an apt decision for the person, the command is printed).

WHAT IS NEVER A LEFTOVER (each one cost a false alarm on 2026-10-22)
  - a unit linked to /dev/null: that is a MASK, a deliberate switch-off (filter-chain.service);
  - a package-owned menu entry hidden by its own TryExec line (vim.desktop with vim not installed);
  - a launcher limited by OnlyShowIn/NotShowIn to other desktops: it never starts here;
  - an entry this tool wrote (X-Fieldkit-Leftovers=hidden).

WHY NOTHING CRASHES
  Every action changes what starts at the NEXT login or what the menu lists: no process is stopped, no file the
  running session has open is moved, no package that is installed is removed, the current desktop's own session
  file is never touched. A system file owned by an installed package is never edited (dpkg would put it back, or
  stop on it): it is switched off with a user-level override, the method the autostart spec itself provides.
  After --apply the scan runs again and a login-safety check must pass before it says "safe to log out".
"""
import argparse
import datetime
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
TRASH = HOME / "Agents.Work.Trash"          # the house rule: quarantine, never delete
USER_AUTOSTART = HOME / ".config" / "autostart"
SYS_AUTOSTART = Path("/etc/xdg/autostart")
USER_APPS = HOME / ".local" / "share" / "applications"
SYS_APPS = (Path("/usr/share/applications"), Path("/usr/local/share/applications"))
USER_UNITS = HOME / ".config" / "systemd" / "user"
SESSION_DIRS = (Path("/usr/share/xsessions"), Path("/usr/share/wayland-sessions"))
MARK = "X-Fieldkit-Leftovers=hidden"
# A guest helper does nothing on real hardware. Program basenames, as their launchers name them.
VM_AGENTS = {"spice-vdagent", "VBoxClient", "VBoxClient-all", "vboxclient", "vmware-user", "vmware-user-suid-wrapper",
             "vmtoolsd", "qemu-ga", "xen-guest-agent", "prlcc", "prltoolsd"}
# Desktop names (as OnlyShowIn writes them) -> session files that mean the desktop is installed.
DESKTOP_SESSIONS = {
    "gnome": ("gnome", "gnome-xorg", "gnome-wayland", "ubuntu", "gnome-classic"), "unity": ("unity",),
    "kde": ("plasma", "plasmawayland", "plasmax11", "kde-plasma"), "xfce": ("xfce",), "lxqt": ("lxqt",),
    "lxde": ("lxde", "lxsession"), "mate": ("mate",), "cinnamon": ("cinnamon", "cinnamon2d", "cinnamon-wayland"),
    "budgie": ("budgie-desktop", "budgie"), "pantheon": ("pantheon",), "deepin": ("deepin",), "enlightenment":
    ("enlightenment",), "lumina": ("lumina",), "sway": ("sway",), "hyprland": ("hyprland",), "x-ivi": (),
}


# ── reading .desktop files ──────────────────────────────────────────────────

def read_entry(path):
    """The [Desktop Entry] keys of a .desktop file (no localised Name[xx] keys), or None when unreadable."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return None
    keys, section = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
            continue
        if section == "[Desktop Entry]" and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if "[" not in k:
                keys.setdefault(k.strip(), v.strip())
    keys["_ours"] = MARK in text
    return keys


def _which(prog):
    if not prog:
        return None
    if os.path.isabs(prog):
        return prog if os.path.exists(prog) else None
    return shutil.which(prog)


def exec_program(exec_line):
    """(program as written, resolved path or None, extra fact) for an Exec= line. Field codes (%f %U ...) are
    dropped; `env VAR=x prog`, `sh -c "prog ..."` and `flatpak run ID` are looked through. A Wine entry
    (`env WINEPREFIX=/x wine C:\\...`) also needs its prefix folder: gone prefix = dead entry."""
    try:
        words = shlex.split(exec_line.replace("\\\\", "\\\\\\\\"))
    except ValueError:
        words = exec_line.split()
    words = [w for w in words if not re.fullmatch(r"%[a-zA-Z]", w)]
    env = {}
    while words and (os.path.basename(words[0]) == "env" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0])):
        w = words.pop(0)
        if "=" in w:
            k, v = w.split("=", 1)
            env[k] = v
    if len(words) >= 3 and os.path.basename(words[0]) in ("sh", "bash", "dash") and words[1] == "-c":
        inner = exec_program(words[2])
        return inner
    if not words:
        return "", None, None
    prog = words[0]
    path = _which(prog)
    if path and os.path.basename(prog) in ("wine", "wine64") and "WINEPREFIX" in env:
        pre = os.path.expanduser(env["WINEPREFIX"])
        if not os.path.isdir(pre):
            return prog, None, f"Wine prefix {pre} is gone"
    if path and os.path.basename(prog) == "flatpak" and len(words) >= 3 and words[1] == "run":
        app = next((w for w in words[2:] if not w.startswith("-")), None)
        if app and subprocess.run(["flatpak", "info", app], capture_output=True).returncode != 0:
            return prog, None, f"flatpak app {app} is not installed"
    return prog, path, None


def current_desktops():
    return {d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}


def session_names(path, entry=None):
    """The desktop names a login session declares (DesktopNames=, lower case), else its file name as a guess."""
    entry = entry if entry is not None else (read_entry(path) or {})
    names = _list(entry.get("DesktopNames"))
    if names:
        return names
    stem = Path(path).stem.lower()
    return {next((k for k, v in DESKTOP_SESSIONS.items() if stem in v), stem)}


def installed_desktops(session_dirs=SESSION_DIRS):
    """Desktop names (OnlyShowIn spelling, lower case) that some login session here declares."""
    have = set()
    for d in session_dirs:
        for p in glob.glob(os.path.join(d, "*.desktop")):
            have |= session_names(p)
    return have


def _list(v):
    return {x.strip().lower() for x in (v or "").split(";") if x.strip()}


def starts_here(entry, desktops):
    """Does an autostart entry start on this desktop (Hidden, OnlyShowIn, NotShowIn, TryExec, as the spec says)?"""
    if entry.get("Hidden", "").lower() == "true":
        return False
    only, never = _list(entry.get("OnlyShowIn")), _list(entry.get("NotShowIn"))
    if only and not desktops & only:
        return False
    if never and desktops & never:
        return False
    if entry.get("TryExec") and not _which(entry["TryExec"]):
        return False
    return True


# ── dpkg ────────────────────────────────────────────────────────────────────

def _run(argv, **kw):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30, **kw)
    except (OSError, subprocess.SubprocessError):
        return None


class Dpkg:
    """Who owns a path, and in what state. Cached; one dpkg-query per path."""

    def __init__(self):
        self.ok = shutil.which("dpkg-query") is not None
        self._own, self._st = {}, {}

    def owner(self, path):
        if not self.ok:
            return None
        if path not in self._own:
            r = _run(["dpkg-query", "-S", str(path)])
            pkg = None
            if r and r.returncode == 0:
                for line in r.stdout.splitlines():   # "pkg: /path" or "pkg1, pkg2: /path" or "diversion by ..."
                    if ": " in line and not line.startswith("diversion"):
                        pkg = line.split(": ", 1)[0].split(",")[0].strip().split(":")[0]
                        break
            self._own[str(path)] = pkg
        return self._own[str(path)]

    def state(self, pkg):
        if not pkg or not self.ok:
            return None
        if pkg not in self._st:
            r = _run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", pkg])
            self._st[pkg] = (r.stdout.strip() if r and r.returncode == 0 else "") or None
        return self._st[pkg]

    def removed(self):
        """Packages in state rc (removed, config files kept), with their config files."""
        if not self.ok:
            return []
        r = _run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Package}\t${Conffiles}\n"])
        out, cur = [], None
        for line in (r.stdout.splitlines() if r else []):
            if "\t" in line:
                st, pkg, rest = (line.split("\t") + ["", ""])[:3]
                cur = {"package": pkg, "conffiles": []} if st.strip().startswith("rc") else None
                if cur:
                    out.append(cur)
                    line = rest
                else:
                    continue
            if cur and line.strip():
                cur["conffiles"].append(line.strip().split()[0])
        return out


# ── the scan ────────────────────────────────────────────────────────────────

def _f(kind, ident, path, why, action, root=False, **extra):
    return {"id": f"{kind}:{ident}", "kind": kind, "path": str(path) if path else None, "why": why,
            "action": action, "root": root, **extra}


def effective_autostart(sys_dir=SYS_AUTOSTART, user_dir=USER_AUTOSTART):
    """name -> (path, entry, shadowed system path or None). A user file of the same name replaces the system one."""
    eff = {}
    for p in sorted(glob.glob(os.path.join(sys_dir, "*.desktop"))):
        eff[os.path.basename(p)] = (p, read_entry(p), None)
    for p in sorted(glob.glob(os.path.join(user_dir, "*.desktop"))):
        name = os.path.basename(p)
        eff[name] = (p, read_entry(p), eff.get(name, (None,))[0])
    return eff


def scan(dpkg=None, sys_autostart=SYS_AUTOSTART, user_autostart=USER_AUTOSTART, user_apps=USER_APPS,
         sys_apps=SYS_APPS, user_units=USER_UNITS, session_dirs=SESSION_DIRS, desktops=None, virt=None):
    """-> (findings, info). Read-only."""
    dpkg = dpkg or Dpkg()
    desktops = current_desktops() if desktops is None else desktops
    have = installed_desktops(session_dirs)
    if virt is None:
        r = _run(["systemd-detect-virt"])
        virt = (r.stdout.strip() if r else "") or "unknown"
    findings, info = [], []
    removed = dpkg.removed()
    rc_files = {c for p in removed for c in p["conffiles"]}
    for p in removed:
        live = [c for c in p["conffiles"] if os.path.exists(c)]
        starts = [c for c in live if c.startswith(str(sys_autostart) + "/")]
        why = (f"removed but not purged: {len(live)} config file(s) still on disk" if live else
               "removed but not purged: no files left, only its entry in the package database")
        if starts:
            why += f", {len(starts)} of them start at login ({', '.join(os.path.basename(s) for s in starts)})"
        findings.append(_f("removed-package", p["package"], None, why, "purge", root=True,
                           package=p["package"], files=live))

    # login launchers
    eff = effective_autostart(sys_autostart, user_autostart)
    running = {}
    for name, (path, e, shadow) in eff.items():
        if e is None or e.get("_ours"):
            continue
        prog, real, extra = exec_program(e.get("Exec", ""))
        user = str(path).startswith(str(user_autostart))
        if path in rc_files:
            continue                                   # reported, and fixed, with its package
        if not starts_here(e, desktops):
            only = _list(e.get("OnlyShowIn"))
            if only and not (only & have) and not user:
                info.append(f"{name}: runs only on {', '.join(sorted(only))}, none installed here, so it never "
                            f"starts (package {dpkg.owner(path) or 'none'}); harmless, not touched")
            continue
        if real is None:
            findings.append(_f("autostart-dead", name, path, f"starts {prog or '(no Exec)'} at login, which is "
                               f"gone{': ' + extra if extra else ''}", "move" if user else "hide"))
            continue
        if os.path.basename(real) in VM_AGENTS or os.path.basename(prog) in VM_AGENTS:
            if virt == "none":
                findings.append(_f("vm-guest-agent", name, path, f"{os.path.basename(real)} is a virtual machine's "
                                   f"guest helper and this machine is not a virtual machine (systemd-detect-virt: "
                                   f"none); package {dpkg.owner(path) or 'none'}", "hide",
                                   package=dpkg.owner(real)))
                continue
        args = " ".join(w for w in e.get("Exec", "").split()[1:] if not re.fullmatch(r"%[a-zA-Z]", w))
        running.setdefault((os.path.realpath(real), args), []).append((name, path))
    for (real, args), group in sorted(running.items()):
        if len(group) < 2:
            continue
        own = dpkg.owner(real)
        keep = next((g for g in group if dpkg.owner(g[1]) == own and own), None) or group[0]
        for name, path in group:
            if (name, path) == keep:
                continue
            pkg = dpkg.owner(path)
            findings.append(_f("autostart-duplicate", name, path, f"starts {real} {args}".rstrip() + f" at the same "
                               f"instant as {keep[0]}; the program belongs to {own or 'no package'}, this copy to "
                               f"{pkg or 'no package'}, so this copy goes", "move" if str(path).startswith(
                                   str(user_autostart)) else "hide", keeps=keep[0]))

    # application menu
    for p in sorted(glob.glob(os.path.join(user_apps, "**", "*.desktop"), recursive=True)):
        e = read_entry(p)
        if not e or e.get("Type", "Application") != "Application" or e.get("_ours"):
            continue
        prog, real, extra = exec_program(e.get("Exec", ""))
        tried = e.get("TryExec")
        if (tried and not _which(tried)) or (prog and real is None):
            gone = extra or f"{tried if tried and not _which(tried) else prog} is gone"
            findings.append(_f("menu-dead", os.path.relpath(p, user_apps), p, f"menu entry "
                               f"\"{e.get('Name', '?')}\": {gone}", "move"))
    unowned = []
    for d in sys_apps:
        for p in sorted(glob.glob(os.path.join(d, "*.desktop"))):
            if dpkg.owner(p):
                continue
            e = read_entry(p) or {}
            prog, real, extra = exec_program(e.get("Exec", ""))
            if real is None and prog:
                findings.append(_f("menu-dead", os.path.basename(p), p, f"system menu entry \"{e.get('Name', '?')}\" "
                                   f"from no package: {extra or prog + ' is gone'}", "move", root=True,
                                   named_only=True))
            else:
                unowned.append(os.path.basename(p))
    if unowned:
        info.append(f"{len(unowned)} system menu entries belong to no package but their programs exist (often "
                    f"installed by hand on purpose), not touched: {', '.join(unowned)}")

    # systemd user units
    if os.path.isdir(user_units):
        for p in sorted(glob.glob(os.path.join(user_units, "*.service"))):
            if os.path.islink(p) and os.readlink(p) == "/dev/null":
                continue                                # a mask: a deliberate switch-off
            if os.path.islink(p) and not os.path.exists(p):
                findings.append(_f("unit-dead", os.path.basename(p), p, f"link to {os.readlink(p)}, which is gone",
                                   "move"))
                continue
            try:
                text = Path(p).read_text(errors="replace")
            except OSError:
                continue
            m = re.search(r"^ExecStart\s*=\s*[-@+!:]*\s*(\S+)", text, re.M)
            if m and not _which(m.group(1)):
                findings.append(_f("unit-dead", os.path.basename(p), p, f"service starts {m.group(1)}, which is gone",
                                   "move", unit=os.path.basename(p)))
        for link in sorted(glob.glob(os.path.join(user_units, "*.wants", "*"))):
            if os.path.islink(link) and not os.path.exists(link) and os.readlink(link) != "/dev/null":
                findings.append(_f("unit-dead", os.path.relpath(link, user_units), link, f"enabled-link to "
                                   f"{os.readlink(link)}, which is gone", "move"))

    # login screen sessions
    cur_session = (os.environ.get("XDG_SESSION_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    others = {}
    for d in session_dirs:
        for p in sorted(glob.glob(os.path.join(d, "*.desktop"))):
            e = read_entry(p) or {}
            stem = Path(p).stem.lower()
            prog, real, extra = exec_program(e.get("Exec", ""))
            tried = e.get("TryExec")
            pkg = dpkg.owner(p)
            if real is None or (tried and not _which(tried)):
                if not pkg and stem != cur_session:
                    findings.append(_f("session-dead", os.path.basename(p), p, f"login-screen session "
                                       f"\"{e.get('Name', stem)}\" from no package, its program is gone", "move",
                                       root=True, named_only=True))
                continue
            names = session_names(p, e)
            if not (desktops & names) and pkg:
                # GNOME Classic declares GNOME-Classic;GNOME: the same desktop as GNOME, not another one
                others.setdefault(sorted(names)[0], set()).add(pkg)
    for dn, pkgs in sorted(others.items()):
        info.append(f"another desktop is installed: {dn} (session from {', '.join(sorted(pkgs))}). Removing a "
                    f"desktop is your decision, not this tool's: /usr/bin/sudo /usr/bin/apt remove "
                    f"{' '.join(sorted(pkgs))}, then /usr/bin/sudo /usr/bin/apt autoremove")
    return findings, info


# ── acting ──────────────────────────────────────────────────────────────────

def _sudo(argv):
    if os.geteuid() == 0:
        return subprocess.run(argv).returncode
    return subprocess.run(["sudo"] + argv).returncode


def _keep(src, run, manifest, how, **extra):
    """Copy (not move) a file into the run folder at its full path, and record it."""
    dst = run / "files" / str(src).lstrip("/")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    else:
        shutil.copy2(src, dst)
    manifest.append({"path": str(src), "kept": str(dst), "how": how, **extra})
    return dst


def hide(name, user_autostart=USER_AUTOSTART):
    """A user-level override that switches a system autostart entry off: the spec's own method."""
    user_autostart.mkdir(parents=True, exist_ok=True)
    p = user_autostart / name
    p.write_text("[Desktop Entry]\nType=Application\nName=" + name + "\nHidden=true\n"
                 f"# Written by fieldkit leftovers {datetime.date.today()}: switches /etc/xdg/autostart/{name} off.\n"
                 f"# Undo: fieldkit leftovers restore <run folder>, or move this file away.\n{MARK}\n")
    return p


def apply(findings, run, user_autostart=USER_AUTOSTART, sudo=_sudo):
    """Carry out each finding's action. -> (manifest, failures). The run folder holds a copy of every file before
    anything changes, so a failure half-way leaves everything recoverable."""
    manifest, failures = [], []
    if any(f["action"] == "purge" for f in findings):
        # A purge script can delete more than the package's config files (google-chrome-stable's removes Google's
        # apt source and signing key, 2026-10-22). The whole of /etc is small: keep it, readable by root only.
        snap = run / "etc-before-purge.tar.gz"
        # /etc holds the password hashes (shadow): the copy is root's alone, made so before it is written
        if sudo(["sh", "-c", 'umask 077 && exec tar -C / -czf "$1" etc', "sh", str(snap)]) != 0:
            return manifest, ["could not save /etc before purging: nothing purged"]
        manifest.append({"path": "/etc", "kept": str(snap), "how": "etc-snapshot"})
    for f in findings:
        try:
            if f["action"] == "purge":
                for c in f.get("files", []):
                    if os.path.exists(c):
                        _keep(c, run, manifest, "purged-conffile", package=f["package"])
                if sudo(["dpkg", "--purge", f["package"]]) != 0:
                    failures.append(f"{f['id']}: dpkg --purge failed")
                else:
                    manifest.append({"path": None, "how": "purged-package", "package": f["package"]})
            elif f["action"] == "hide":
                name = os.path.basename(f["path"])
                target = user_autostart / name
                if target.exists():
                    _keep(target, run, manifest, "replaced-user-entry")
                p = hide(name, user_autostart)
                manifest.append({"path": str(p), "how": "hid", "system": f["path"]})
            elif f["action"] == "move":
                src = f["path"]
                if f.get("unit"):
                    r = _run(["systemctl", "--user", "is-enabled", f["unit"]])
                    if r and r.stdout.strip() == "enabled":
                        _run(["systemctl", "--user", "disable", f["unit"]])
                        manifest.append({"path": None, "how": "disabled-unit", "unit": f["unit"]})
                _keep(src, run, manifest, "moved")
                if f.get("root"):
                    if sudo(["rm", "-f", "--", src]) != 0:          # the copy is already in the run folder
                        failures.append(f"{f['id']}: could not move {src} (root)")
                else:
                    os.unlink(src)
        except OSError as e:
            failures.append(f"{f['id']}: {e}")
    return manifest, failures


def login_safe(before, desktops=None, sys_autostart=SYS_AUTOSTART, user_autostart=USER_AUTOSTART,
               session_dirs=SESSION_DIRS, removed_ok=()):
    """What a login needs and the cleaning must not have taken: every launcher that started here before still starts
    unless it was one of ours to remove, the current desktop still has its session file, dpkg is consistent."""
    desktops = current_desktops() if desktops is None else desktops
    problems = []
    now = {n for n, (p, e, s) in effective_autostart(sys_autostart, user_autostart).items()
           if e and starts_here(e, desktops)}
    for n in sorted(before - now - set(removed_ok)):
        problems.append(f"{n} started at login before and does not now")
    cur = (os.environ.get("XDG_SESSION_DESKTOP") or os.environ.get("DESKTOP_SESSION") or "").lower()
    if cur and not any((Path(d) / f"{cur}.desktop").exists() for d in session_dirs):
        problems.append(f"the current session file {cur}.desktop is missing")
    r = _run(["dpkg", "--audit"])
    if r and r.stdout.strip():
        problems.append("dpkg --audit reports: " + r.stdout.strip().splitlines()[0])
    return problems


def restore(run, apply_it=False, sudo=_sudo):
    """Put a cleaned run back from its manifest. Purged packages are not reinstalled: their config files are
    written back, which is what made them act; reinstall with apt if the program itself is wanted."""
    m = json.loads((Path(run) / "manifest.json").read_text())
    steps = []
    for r in reversed(m["actions"]):
        if r["how"] in ("moved", "purged-conffile", "replaced-user-entry"):
            steps.append(("copy back", r["kept"], r["path"]))
        elif r["how"] == "hid":
            steps.append(("remove override", r["path"], None))
        elif r["how"] == "disabled-unit":
            steps.append(("enable", r["unit"], None))
        elif r["how"] == "etc-snapshot":
            steps.append(("note", f"/etc as it was before the purge is in {r['kept']}: take single files from it with "
                          f"/usr/bin/sudo /usr/bin/tar -xzf {r['kept']} -C / etc/<file>", None))
    if not apply_it:
        return steps, []
    fails = []
    for what, a, b in steps:
        try:
            if what == "copy back":
                if os.path.lexists(b):
                    fails.append(f"{b} exists again; not overwritten (the kept copy is {a})")
                elif os.access(os.path.dirname(b) or "/", os.W_OK):
                    os.makedirs(os.path.dirname(b), exist_ok=True)
                    shutil.copy2(a, b, follow_symlinks=False)
                elif sudo(["cp", "-a", "--", a, b]) != 0:
                    fails.append(f"could not write {b}")
            elif what == "remove override":
                if os.path.exists(a) and MARK in Path(a).read_text(errors="replace"):
                    keep = Path(run) / "restored-overrides"          # never deleted: moved into the run folder
                    keep.mkdir(exist_ok=True)
                    shutil.move(a, keep / os.path.basename(a))
            elif what == "enable":
                _run(["systemctl", "--user", "enable", a])
        except OSError as e:
            fails.append(str(e))
    return steps, fails


# ── command line ────────────────────────────────────────────────────────────

def _print_findings(findings, info):
    for line in info:
        print("  " + line)
    for f in findings:
        root = " (needs your password)" if f["root"] else ""
        named = " (only when named)" if f.get("named_only") else ""
        print(f"FINDING {f['id']}\n  {f['why']}\n  action: {f['action']}{root}{named}")


def doctor():
    need = {"dpkg-query": "Debian-family package database (without it removed-package and ownership are not checked)",
            "systemd-detect-virt": "tells a virtual machine from real hardware (systemd)",
            "systemctl": "user services (systemd)", "sudo": "for the actions that need your password"}
    missing = 0
    for prog, why in need.items():
        ok = shutil.which(prog)
        print(f"{'ok     ' if ok else 'MISSING'} {prog}: {why}")
        missing += not ok
    print(f"ok      Python {sys.version.split()[0]}")
    print("the person supplies: their password, for purge and for system files only")
    return 1 if missing else 0


def main(argv=None, prog="leftovers.py"):
    usage = "\n".join(l.replace("fieldkit leftovers", prog) for l in __doc__.split("\n\n")[1].splitlines())
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0], epilog=usage,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("scan", help="list what is left behind (read-only; exit 3 = findings)")
    s.add_argument("--json", action="store_true")
    c = sp.add_parser("clean", help="the plan for the findings (default), --apply carries it out")
    c.add_argument("ids", nargs="*", help="finding ids from scan; none = every finding not marked 'only when named'")
    c.add_argument("--apply", action="store_true")
    r = sp.add_parser("restore", help="put a cleaned run back")
    r.add_argument("run")
    r.add_argument("--apply", action="store_true")
    sp.add_parser("doctor", help="the programs this tool needs")
    a = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("not-this-platform: desktop leftovers are checked on Linux only")
        return 0
    if a.cmd == "doctor":
        return doctor()
    if a.cmd == "restore":
        steps, fails = restore(a.run, a.apply)
        for what, x, y in steps:
            print(f"  {what}: {x}" + (f" -> {y}" if y else ""))
        print("\n".join("FAILED " + f for f in fails) or ("restored" if a.apply else
              f"plan only: {self_cmd()} leftovers restore {a.run} --apply"))
        return 1 if fails else 0
    findings, info = scan()
    if a.cmd == "scan":
        if a.json:
            print(json.dumps({"findings": findings, "info": info}, indent=1))
        else:
            _print_findings(findings, info)
            print(f"{len(findings)} finding(s)" if findings else "CLEAN: nothing left behind that this tool knows")
            if findings:
                print(f"next: {self_cmd()} leftovers clean   (shows the plan; nothing changes without --apply)")
        return 3 if findings else 0
    known = {f["id"]: f for f in findings}
    bad = [i for i in a.ids if i not in known]
    if bad:
        print("REFUSED: not a current finding: " + ", ".join(bad) + f" (run {self_cmd()} leftovers scan)")
        return 2
    chosen = [known[i] for i in a.ids] if a.ids else [f for f in findings if not f.get("named_only")]
    if not chosen:
        print("nothing to clean")
        return 0
    for f in chosen:
        print(f"  {f['action']:6} {f['id']}{'  (password)' if f['root'] else ''}")
    if not a.apply:
        ids = " ".join(shlex.quote(f["id"]) for f in chosen) + " " if a.ids else ""
        print(f"plan only. Nothing is stopped and nothing changes until the next login. To carry it out:\n"
              f"  {self_cmd()} leftovers clean {ids}--apply")
        return 0
    desktops = current_desktops()
    before = {n for n, (p, e, s) in effective_autostart().items() if e and starts_here(e, desktops)}
    run = TRASH / f"leftovers-{datetime.datetime.now():%y-%m-%d-%H-%M-%S}"
    run.mkdir(parents=True)
    manifest, fails = apply(chosen, run)
    (run / "manifest.json").write_text(json.dumps({"made": str(datetime.datetime.now()), "findings": chosen,
                                                   "actions": manifest}, indent=1))
    gone = {os.path.basename(f["path"]) for f in chosen if f["path"] and f["kind"].startswith("autostart")
            or f["kind"] == "vm-guest-agent"}
    gone |= {os.path.basename(c) for f in chosen if f["kind"] == "removed-package" for c in f.get("files", [])}
    problems = login_safe(before, desktops, removed_ok=gone)
    after, _ = scan()
    left = {f["id"] for f in after} & {f["id"] for f in chosen}
    for f in fails:
        print("FAILED " + f)
    for p in problems:
        print("LOGIN-UNSAFE " + p)
    for i in sorted(left):
        print("STILL-THERE " + i)
    print(f"run folder (everything kept, undo with {self_cmd()} leftovers restore {run} --apply): {run}")
    if fails or problems or left:
        return 1
    print(f"done: {len(chosen)} cleaned, login check passed, safe to log out and back in")
    return 0


def self_cmd():
    try:
        from fieldkit.core.settings import self_cmd as sc
        return sc()
    except ImportError:
        return f"{sys.executable} {os.path.abspath(__file__)}"


if __name__ == "__main__":
    sys.exit(main())
