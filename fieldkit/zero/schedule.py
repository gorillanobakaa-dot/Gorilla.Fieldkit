# Version: 1.0.0 · updated 26-10-26
"""The scheduled comms check: `fieldkit net check` every 15 minutes, as the owner, on Windows, Linux and macOS.

The same pattern as `fieldkit hog install` (fieldkit/hog/install.py): a Windows task in the owner's own context
(InteractiveToken, no stored password, not SYSTEM, LeastPrivilege) run by pythonw.exe; a systemd USER timer; a
LaunchAgent. 2026-10-25 night: the cable net went quiet with both listeners down and nobody noticed until morning;
this runs the check, revives the cable through `fieldkit peers link heal --revive`, retries unacked messages, and
flags silent stations on the PLAN.md Board, with nobody awake. Undo: `fieldkit net schedule --remove`.
Without --apply it prints what it would write and run, and changes nothing.
"""
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "Fieldkit net comms check"
UNIT = "fieldkit-net"
LABEL = "com.fieldkit.net"
ROOT = Path(__file__).resolve().parents[2]
EVERY_MIN = 15
ARGS = ["-m", "fieldkit", "net", "check", "--scheduled"]


def _pythonw(python):
    p = Path(python)
    w = p.with_name("pythonw.exe")
    return str(w) if w.is_file() else str(p)


def task_xml(python, root, user=None, every=EVERY_MIN):
    user = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>fieldkit net check: the comms check of the agent net (leases, cable revive, unacked retries). Written by fieldkit net schedule.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger><Enabled>true</Enabled><StartBoundary>2026-01-01T00:00:00</StartBoundary><Repetition><Interval>PT{every}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT2M</Delay></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(_pythonw(python))}</Command><Arguments>{escape(" ".join(ARGS))}</Arguments><WorkingDirectory>{escape(str(root))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def systemd_units(python, root, every=EVERY_MIN):
    service = f"""[Unit]
Description=fieldkit net check: the comms check of the agent net

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={python} {" ".join(ARGS)}
Nice=10
TimeoutStartSec=300
"""
    timer = f"""[Unit]
Description=Run fieldkit net check every {every} minutes

[Timer]
OnBootSec=3min
OnCalendar=*:0/{every}
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def launchd_plist(python, root, every=EVERY_MIN):
    args = "".join(f"<string>{escape(a)}</string>" for a in [python] + ARGS)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>WorkingDirectory</key><string>{escape(str(root))}</string>
  <key>StartInterval</key><integer>{every * 60}</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict></plist>
"""


def plan(platform=None, python=None, root=None, home=None, state=None):
    """-> steps ("write"|"write-utf16", path, text) / ("run", argv). Pure: the tests read it without touching the OS."""
    platform = platform or sys.platform
    python = python or sys.executable
    root = root or ROOT
    home = Path(home or Path.home())
    if platform == "win32":
        xml = Path(state or (root / "state" / "coordination")) / "net-task.xml"
        return [("write-utf16", xml, task_xml(python, root)),
                ("run", ["schtasks", "/Create", "/TN", TASK, "/XML", str(xml), "/F"])]
    if platform.startswith("linux"):
        d = home / ".config" / "systemd" / "user"
        svc, tmr = systemd_units(python, root)
        return [("write", d / f"{UNIT}.service", svc), ("write", d / f"{UNIT}.timer", tmr),
                ("run", ["systemctl", "--user", "daemon-reload"]),
                ("run", ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])]
    if platform == "darwin":
        p = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        return [("write", p, launchd_plist(python, root)),
                ("run", ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]),
                ("run", ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(p)])]
    return []


def remove_plan(platform=None, home=None):
    platform = platform or sys.platform
    home = Path(home or Path.home())
    if platform == "win32":
        return [("run", ["schtasks", "/Delete", "/TN", TASK, "/F"])]
    if platform.startswith("linux"):
        d = home / ".config" / "systemd" / "user"
        return [("run", ["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"]),
                ("rm", d / f"{UNIT}.timer"), ("rm", d / f"{UNIT}.service")]
    if platform == "darwin":
        return [("run", ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"]),
                ("rm", home / "Library" / "LaunchAgents" / f"{LABEL}.plist")]
    return []


def apply(steps, run=subprocess.run, say=print):
    """Carry the steps out. -> 0, or 1 when a command failed (launchctl bootout of a missing agent is not a failure)."""
    rc = 0
    for s in steps:
        if s[0] in ("write", "write-utf16"):
            s[1].parent.mkdir(parents=True, exist_ok=True)
            s[1].write_text(s[2], encoding="utf-16" if s[0] == "write-utf16" else "utf-8")
            say(f"wrote {s[1]}")
        elif s[0] == "rm":
            if s[1].exists():
                s[1].unlink()
                say(f"removed {s[1]}")
        else:
            r = run(s[1], capture_output=True, text=True)
            ok = r.returncode == 0 or "bootout" in s[1]
            say(f"{'ran' if ok else 'FAILED'}: {' '.join(s[1])}" + ("" if ok else f"  ({(r.stderr or r.stdout).strip()[:200]})"))
            rc = rc or (0 if ok else 1)
    return rc


def scheduled(platform=None, run=subprocess.run, home=None):
    """-> (installed?, detail)."""
    platform = platform or sys.platform
    try:
        if platform == "win32":
            r = run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True)
            return r.returncode == 0, f"task {TASK!r} " + ("installed" if r.returncode == 0 else "not installed")
        if platform.startswith("linux"):
            r = run(["systemctl", "--user", "is-enabled", f"{UNIT}.timer"], capture_output=True, text=True)
            return r.returncode == 0, f"{UNIT}.timer {(r.stdout or '').strip() or 'not installed'}"
        if platform == "darwin":
            p = Path(home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"
            return p.is_file(), f"{p} " + ("present" if p.is_file() else "missing")
    except OSError as e:
        return False, str(e)
    return False, f"{platform}: no schedule built for this OS"
