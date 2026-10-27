# Version: 1.0.0 · updated 26-10-26-15-00
"""Keep the cable listener alive without a person: a service manager restarts it, not a session.

    fieldkit peers link keepalive [status]        this computer: is keep-alive installed and running
    fieldkit peers link keepalive install         Linux: systemd user units (listener + rescue, Restart=always) and
                                                  linger; Windows: the logon task (admin: the mandate prerequisite)
    fieldkit peers link keepalive units DIR       write the Linux units into DIR only (offline review, the handover)

2026-10-26 incident: the VAIO answered ping, ARP and nothing else (TCP 47077 and 22 REFUSED); its last traffic was
00:37 and the owner had to walk to it. What kept it alive before was weak by construction:
  - `setup` and `restart` started `serve` as a detached child; nothing restarts a child that dies.
  - The watchdog timer was a Type=oneshot unit whose restart started that child INSIDE the oneshot's cgroup; when the
    oneshot exits, systemd stops every process left in its cgroup (KillMode=control-group, the default), so the
    listener it had just started died with it. (Read in systemd.kill(5); not yet observed on the VAIO itself.)
  - Without linger, a user's systemd manager (and so every user timer) stops at logout and does not start at boot
    until someone logs in (loginctl(1) enable-linger).
  - After two failed restarts the watchdog stops on purpose and waits for a person (its rule since 2026-10-23).
Now: the listener IS a user service (Restart=always, RestartSec=5, no start limit), linger is on, the rescue
listener is a second service, and the watchdog restarts through systemctl, never as its own child.
Windows: a scheduled task "fieldkit-link" at logon, repeated every 5 minutes with MultipleInstances=IgnoreNew (a
running listener is left alone; a dead one is started again) and RestartCount on failure.
"""
import getpass
import re
import subprocess
import sys
from pathlib import Path

from . import link

SERVICE = "fieldkit-link.service"
RESCUE = "fieldkit-link-rescue.service"
WATCHDOG = "fieldkit-link-watchdog.timer"
TASK = "fieldkit-link"

UNIT = """[Unit]
Description=fieldkit cable link listener ({what})
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={root}
Environment=PYTHONPATH={root}
ExecStart={py} -P -m fieldkit peers link {verb}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def units(root=None, py=None):
    """-> {file name: text} of the Linux user units. Pure: the tests and the handover read it."""
    root, py = str(root or link.LINK_ROOT), py or sys.executable
    return {SERVICE: UNIT.format(what="answers signed requests", root=root, py=py, verb="serve"),
            RESCUE: UNIT.format(what="rescue door: status and restart of the listener only", root=root, py=py,
                                verb="rescue serve")}


def unit_dir(home=None):
    return Path(home or Path.home()) / ".config" / "systemd" / "user"


def write_units(d, root=None, py=None):
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for name, text in units(root, py).items():
        with open(d / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        out.append(str(d / name))
    return out


def _ok(run, argv):
    try:
        r = run(argv, capture_output=True, text=True)
    except OSError as e:
        return False, str(e)
    return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()


def linger(run=subprocess.run, user=None):
    ok, out = _ok(run, ["loginctl", "show-user", user or getpass.getuser(), "-p", "Linger"])
    return ok and bool(re.search(r"^Linger=yes", out, re.M))


def linux_status(run=subprocess.run, user=None):
    """-> rows {item, ok, detail}: units enabled + active, linger, the watchdog timer."""
    rows = []
    for u in (SERVICE, RESCUE, WATCHDOG):
        en = _ok(run, ["systemctl", "--user", "is-enabled", u])
        ac = _ok(run, ["systemctl", "--user", "is-active", u])
        rows.append({"item": u, "ok": en[0] and ac[0],
                     "detail": f"enabled={en[1].splitlines()[-1] if en[1] else '?'} "
                               f"active={ac[1].splitlines()[-1] if ac[1] else '?'}"})
    lg = linger(run, user)
    rows.append({"item": "linger", "ok": lg, "detail": "yes: the units run at boot without a login" if lg else
                 "NO: user units stop at logout and wait for a login after a reboot"})
    return rows


def listener_python(home=None, run=subprocess.run):
    """-> (ok, one line) about the Python the installed listener unit starts, or None when no unit is installed.
    2026-10-26: the units were written with the system python3, which had no pytest, so every test job the listener
    ran collected 0 tests and said only "exit 1"."""
    p = unit_dir(home) / SERVICE
    try:
        m = re.search(r"^ExecStart=(\S+)", p.read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    if not m:
        return False, f"{p} has no ExecStart"
    py = m.group(1)
    ok, out = _ok(run, [py, "-c", "import pytest, yaml, psutil"])
    return (True, f"{py}: pytest, yaml, psutil present") if ok else \
        (False, f"PROBLEM {py} cannot import pytest/yaml/psutil, so test jobs here collect nothing. FIX: run "
                f"`{sys.executable} -m fieldkit peers link keepalive install` with the Fieldkit venv Python")


def windows_status(run=subprocess.run):
    ok, out = _ok(run, ["schtasks", "/Query", "/TN", TASK, "/V", "/FO", "LIST"])
    if not ok:
        return [{"item": f"task {TASK}", "ok": False, "detail": "not installed (mandate prerequisite "
                 "link-keepalive-windows)"}]
    st = re.search(r"^Status:\s*(.+)$", out, re.M)
    return [{"item": f"task {TASK}", "ok": True, "detail": f"installed, status {st.group(1).strip() if st else '?'}"}]


def status(platform=sys.platform, run=subprocess.run):
    if platform.startswith("linux"):
        return linux_status(run)
    if platform == "win32":
        return windows_status(run)
    return [{"item": "keepalive", "ok": False, "detail": f"{platform}: no keep-alive is built for this OS yet"}]


def summary(rows):
    """One short dict a peer sends in its hello (doctor shows both ends)."""
    return {"ok": all(r["ok"] for r in rows), "missing": [r["item"] for r in rows if not r["ok"]]}


def stop_detached(pidfile=None):
    """Stop the listener `setup` / `restart` started as a detached child (serve.pid), only if it IS a link listener."""
    pidf = pidfile or link.home() / "serve.pid"
    try:
        pid = int(pidf.read_text().strip())
    except (OSError, ValueError):
        return "no detached listener recorded (serve.pid)"
    try:
        import psutil
        p = psutil.Process(pid)
        cmd = " ".join(p.cmdline())
        if "peers" in cmd and "serve" in cmd and "rescue" not in cmd:
            p.terminate()
            p.wait(5)
            return f"stopped the detached listener PID {pid}: the service runs it now"
        return f"left PID {pid} alone: not a link listener"
    except Exception as e:  # noqa: BLE001 - gone already, or no psutil
        return f"no detached listener at PID {pid} ({type(e).__name__})"


def install(platform=sys.platform, run=subprocess.run, home=None):
    """-> (code, lines). Linux only does it itself; Windows is an administrator prerequisite of the mandate."""
    if not platform.startswith("linux"):
        return 3, ["Windows: the logon task is the mandate prerequisite link-keepalive-windows "
                   "(fieldkit mandate prereqs apply); nothing is done here"]
    lines = [f"wrote {p}" for p in write_units(unit_dir(home))]
    lines.append(stop_detached())                 # else the service cannot bind and restarts every 5 s
    from . import link_watchdog
    _c, wl = link_watchdog.install(run=run, home=home)
    lines += [f"watchdog: {x}" for x in wl]
    for argv in (["systemctl", "--user", "daemon-reload"],
                 ["systemctl", "--user", "enable", "--now", SERVICE, RESCUE],
                 ["loginctl", "enable-linger", getpass.getuser()]):
        ok, out = _ok(run, argv)
        lines.append(f"{'ok  ' if ok else 'FAIL'} {' '.join(argv)}" + ("" if ok else f": {out[:200]}"))
    bad = [x for x in lines if x.startswith("FAIL")]
    return (3 if bad else 0), lines


FIREWALL_SH = Path(__file__).resolve().parent / "keepalive" / "link-rescue-firewall.sh"


def rescue_firewall_args(h=None):
    """[PEER, ME, PORT] for the rescue rule: the first cable device and this device's address on its subnet."""
    from . import link_rescue
    h = h or link.household()
    mine = link.device(h, h.get("me")) or {}
    for d in link.others(h):
        if (d.get("link") or "lan") == "cable" and d.get("address"):
            me = link._source_for(h, d["address"][0])
            if me:
                return [d["address"][0], me, str(link_rescue.port_for(mine, h))]
    return None


def rescue_firewall_present(platform=sys.platform, run=subprocess.run, args=None):
    """True / False from the pinned script's verify; None when it cannot tell (then the prerequisite is applied)."""
    if not platform.startswith("linux"):
        return True                                  # the rescue door listens on Linux peers only
    args = args or rescue_firewall_args()
    if not args:
        return None
    try:
        r = run(["sh", str(FIREWALL_SH), "verify", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"present = (yes|no)", r.stdout or "")
    return None if r.returncode != 0 or not m else m.group(1) == "yes"


def restart_via_manager(platform=sys.platform, run=subprocess.run):
    """The watchdog's restart when keep-alive is installed: the manager starts it, so it outlives the caller.
    -> (handled, code, lines); handled False means keep-alive is not installed (the old way is used)."""
    if platform.startswith("linux"):
        if not _ok(run, ["systemctl", "--user", "is-enabled", SERVICE])[0]:
            return False, 0, []
        ok, out = _ok(run, ["systemctl", "--user", "restart", SERVICE])
        return True, (0 if ok else 3), [f"systemctl --user restart {SERVICE}: " + ("ok" if ok else out[:200])]
    if platform == "win32":
        if not _ok(run, ["schtasks", "/Query", "/TN", TASK])[0]:
            return False, 0, []
        _ok(run, ["schtasks", "/End", "/TN", TASK])      # a deaf listener the task holds; IgnoreNew skips /Run
        ok, out = _ok(run, ["schtasks", "/Run", "/TN", TASK])
        return True, (0 if ok else 3), [f"schtasks /End + /Run /TN {TASK}: " + ("ok" if ok else out[:200])]
    return False, 0, []


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link keepalive", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="status", choices=["status", "install", "units"])
    ap.add_argument("dir", nargs="?", help="units: where to write the Linux units")
    a = ap.parse_args(argv)
    if a.action == "units":
        if not a.dir:
            ap.print_usage()
            return 2
        for p in write_units(a.dir):
            print(f"wrote {p}")
        return 0
    if a.action == "install":
        code, lines = install()
        print("\n".join(lines))
        return code
    rows = status()
    for r in rows:
        print(f"  [{'ok' if r['ok'] else 'NO'}] {r['item']:<32} {r['detail']}")
    ok = all(r["ok"] for r in rows)
    print("KEEPALIVE  " + ("OK" if ok else "NOT INSTALLED: fieldkit peers link keepalive install (Linux), "
                           "or the mandate prerequisite (Windows)"))
    return 0 if ok else 3
