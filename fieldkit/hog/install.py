"""The hog's schedule: `fieldkit hog watch` every 5 minutes, as the owner, on Windows, Linux and macOS.

Windows  a scheduled task "Fieldkit hog watch" in the owner's own context (InteractiveToken: no stored password,
         not SYSTEM, no elevation), run by pythonw.exe so no console window flashes every 5 minutes.
Linux    systemd USER units fieldkit-hog.service + .timer (no root), OnCalendar every 5 min, Persistent.
macOS    a LaunchAgent ~/Library/LaunchAgents/com.fieldkit.hog.plist, StartInterval 300, in the owner's GUI domain.
Who can act through it: only code already in the Fieldkit folder it names. Undo: `fieldkit hog install --remove`.
Without --apply it prints what it would write and run, and changes nothing.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

TASK = "Fieldkit hog watch"
UNIT = "fieldkit-hog"
LABEL = "com.fieldkit.hog"
ROOT = Path(__file__).resolve().parents[2]


def _pythonw(python):
    p = Path(python)
    w = p.with_name("pythonw.exe")
    return str(w) if w.is_file() else str(p)


def task_xml(python, root, user=None):
    user = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>fieldkit hog watch: RAM, idle local models, leaks, orphans, CPU, heat, disk. Written by fieldkit hog install.</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger><Enabled>true</Enabled><StartBoundary>2026-01-01T00:00:00</StartBoundary><Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT1M</Delay></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT4M</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(_pythonw(python))}</Command><Arguments>-m fieldkit hog watch</Arguments><WorkingDirectory>{escape(str(root))}</WorkingDirectory></Exec></Actions>
</Task>
"""


def systemd_units(python, root):
    service = f"""[Unit]
Description=fieldkit hog watch: RAM, idle local models, leaks, orphans, CPU, heat, disk

[Service]
Type=oneshot
WorkingDirectory={root}
ExecStart={python} -m fieldkit hog watch
Nice=10
TimeoutStartSec=240
"""
    timer = f"""[Unit]
Description=Run fieldkit hog watch every 5 minutes

[Timer]
OnBootSec=2min
OnCalendar=*:0/5
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def launchd_plist(python, root):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{escape(python)}</string><string>-m</string><string>fieldkit</string><string>hog</string><string>watch</string></array>
  <key>WorkingDirectory</key><string>{escape(str(root))}</string>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>/dev/null</string>
  <key>StandardErrorPath</key><string>/dev/null</string>
</dict></plist>
"""


def plan(platform=None, python=None, root=None, home=None):
    """-> list of steps: ("write", path, text) or ("run", argv). Pure, so the tests read it without touching the OS."""
    platform = platform or sys.platform
    python = python or sys.executable
    root = root or ROOT
    home = Path(home or Path.home())
    if platform == "win32":
        from .registry import state_dir
        xml = state_dir() / "hog-task.xml"
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


def main(apply=False, remove=False):
    steps = remove_plan() if remove else plan()
    if not steps:
        print(f"hog install: no scheduler known on {sys.platform}")
        return 3
    rc = 0
    for kind, *rest in steps:
        if kind == "run":
            print(("RUN   " if apply else "WOULD RUN   ") + " ".join(rest[0]))
            if apply:
                r = subprocess.run(rest[0], capture_output=True, text=True)
                if r.returncode and "bootout" not in rest[0][1:2]:
                    print(f"  exit {r.returncode}: {(r.stdout + r.stderr).strip()[:300]}")
                    rc = 1
        elif kind == "rm":
            print(("REMOVE " if apply else "WOULD REMOVE ") + str(rest[0]))
            if apply and Path(rest[0]).exists():
                Path(rest[0]).unlink()
        else:
            path, text = rest
            print(("WRITE " if apply else "WOULD WRITE ") + str(path))
            if apply:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text(text, encoding="utf-16" if kind == "write-utf16" else "utf-8")
    if not apply:
        print("(nothing changed: add --apply)")
    return rc


def scheduled(platform=None, run=subprocess.run):
    platform = platform or sys.platform
    try:
        if platform == "win32":
            r = run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True, timeout=20)
        elif platform.startswith("linux"):
            r = run(["systemctl", "--user", "is-active", f"{UNIT}.timer"], capture_output=True, text=True, timeout=20)
        elif platform == "darwin":
            r = run(["launchctl", "list", LABEL], capture_output=True, text=True, timeout=20)
        else:
            return False, "no scheduler known"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]


def doctor():
    lines, ok = [], True
    try:
        import psutil
        lines.append(f"OK    psutil {psutil.__version__}")
    except ImportError:
        lines.append("FAIL  psutil missing: pip install psutil")
        return False, lines
    from . import probe, registry, config
    cfg = config()
    exe = probe.lms_path(cfg)
    lines.append(("OK    lms " + exe) if exe else "INFO  no lms CLI (LM Studio models are not checked)")
    d = registry.state_dir()
    lines.append(f"OK    state {d}" if os.access(d, os.W_OK) else f"FAIL  state {d} is not writable")
    on, why = scheduled()
    lines.append(("OK    schedule: " if on else "WARN  schedule not installed (fieldkit hog install --apply): ")
                 + str(why))
    ok = ok and on
    try:
        from ..autopilot import room
        r = room.doctor()
        lines.append("OK    room hook installed (registry + prompt line ride on it)" if r["ok"]
                     else f"WARN  room hooks: missing {r['missing']}, stale {r['stale']}: "
                          "python -m fieldkit.autopilot.room doctor --apply")
    except Exception as e:
        lines.append(f"WARN  room hook: {e}")
    last = d / "last.json"
    if last.is_file():
        import json
        import time
        j = json.loads(last.read_text(encoding="utf-8"))
        lines.append(f"INFO  last pass {int(time.time() - j['t'])} s ago: {j['summary'][:150]}")
    return ok, lines
