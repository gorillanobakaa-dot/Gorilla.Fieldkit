# Version: 1.0.0 · updated 26-10-23-16-17
"""The relay heartbeat: the listener is checked and the open jobs are looked after; it does NOT restart the listener.

    fieldkit peers link watchdog [--once] [--every S]    one check (a timer runs it), or a loop every S seconds
    fieldkit peers link watchdog --install               Linux: a systemd user timer that runs --once every 2 minutes

2026-10-23: the listener went deaf silently, and notes waited until a person noticed. 2026-10-28 (C4,
docs/findings/c4-link-drops.yaml): the restart here duplicated the service manager (systemd Restart=, the Windows
logon task), the one keep-alive that brought the link back on the real log. So a deaf listener is now reported once to
the owner's inbox and logged (watchdog.log); the restart is the service manager's. On the boss, each check also looks
after the open jobs (fieldkit/mandate/jobs.py supervise), which is why this timer still exists.
"""
import json
import sys
import time
from pathlib import Path

from . import link


def _state_path():
    return link.home() / "watchdog.json"


def _log(text):
    link.home().mkdir(parents=True, exist_ok=True)
    with open(link.home() / "watchdog.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")


def _listening():
    from . import link_setup
    me = (link.config() or {}).get("me")
    return bool(me) and link_setup.listening(me)


def _report(text):
    try:
        from ..autopilot.inbox import add
        add("the cable link listener does not come back", text, kind="link")
    except Exception:  # noqa: BLE001 - the log still holds it
        pass


def tick(listening=_listening, restart=None, report=_report, supervise=None):   # restart: kept so old callers work, unused
    """One heartbeat -> what happened, in words."""
    sp = _state_path()
    try:
        st = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
    except ValueError:
        st = {}
    if not (link.config() or {}) and listening is _listening:
        return "not paired: nothing to watch"
    if listening():
        if st.get("reported"):
            _log("listening again")
        st = {"last_ok": time.strftime("%Y-%m-%d %H:%M:%S")}
        what = "listening"
    else:
        # C4 (docs/findings/c4-link-drops.yaml): the service manager (systemd Restart=, the Windows logon task) is the one
        # keep-alive. Restarting here as well made two mechanisms fight over one port. A deaf listener is reported once.
        if not st.get("reported"):
            report("the listener is not answering and the service manager has not brought it back. "
                   f"Log: {link.home() / 'watchdog.log'}")
            st["reported"] = True
            _log("not listening; reported once, not restarting (the service manager does that)")
        what = "not listening; reported, no restart (the service manager is the keep-alive)"
    if supervise is not None:
        try:
            for line in supervise():
                _log(f"jobs: {line}")
        except Exception as e:  # noqa: BLE001 - jobs never stop the heartbeat
            _log(f"jobs: supervise failed: {e}")
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return what


UNIT = """[Unit]
Description=fieldkit cable link watchdog (one check)

[Service]
Type=oneshot
KillMode=process
WorkingDirectory={root}
ExecStart={py} -m fieldkit peers link watchdog --once
"""
TIMER = """[Unit]
Description=fieldkit cable link watchdog, every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
"""


def install(run=None, home=None):
    """Linux: write and enable a systemd user timer. -> (code, lines). Windows: the Windows side's scheduled task."""
    if not sys.platform.startswith("linux"):
        return 3, ["the watchdog timer is installed this way on Linux only; Windows uses a scheduled task (its side)"]
    import subprocess
    run = run or subprocess.run
    d = Path(home or Path.home()) / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    (d / "fieldkit-link-watchdog.service").write_text(UNIT.format(root=link.LINK_ROOT, py=sys.executable),
                                                     encoding="utf-8")
    (d / "fieldkit-link-watchdog.timer").write_text(TIMER, encoding="utf-8")
    r1 = run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    r2 = run(["systemctl", "--user", "enable", "--now", "fieldkit-link-watchdog.timer"], capture_output=True, text=True)
    ok = r1.returncode == 0 and r2.returncode == 0
    return (0 if ok else 3), [f"wrote {d / 'fieldkit-link-watchdog.timer'}",
                              "enabled: every 2 minutes" if ok else f"not enabled: {(r2.stderr or '').strip()[:200]}"]


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit peers link watchdog")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=int, default=120)
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args(argv)
    if a.install:
        code, lines = install()
        print("\n".join(lines))
        return code
    from ..mandate import jobs
    sup = jobs.supervise_lines
    while True:
        print(tick(supervise=sup))
        if a.once:
            return 0
        time.sleep(a.every)
