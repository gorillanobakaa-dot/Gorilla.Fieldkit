"""Start the watchdog at login.

Linux: a systemd USER unit. No root, no sudo, and `systemctl --user` is enough.
Windows: deliberately not handled here. The existing scheduled task set up on
2026-10-09 already does it, it works, and the owner's instruction on 2026-10-13
was to leave Windows alone. This reports that rather than changing anything.
"""
import shutil
import subprocess
from fieldkit import proc as fkproc
import sys
from pathlib import Path

UNIT_DIR = Path.home() / ".config/systemd/user"
UNIT = UNIT_DIR / "gorilla-watchdog.service"
UNIT_NAME = "gorilla-watchdog"

UNIT_BODY = """[Unit]
Description=Gorilla online watchdog: rejoin the wifi, or say so out loud
After=network.target

[Service]
Type=simple
ExecStart={python} -m fieldkit watchdog run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
"""


PLIST_DIR = Path.home() / "Library/LaunchAgents"
PLIST = PLIST_DIR / "com.gorilla.watchdog.plist"
PLIST_LABEL = "com.gorilla.watchdog"

PLIST_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.gorilla.watchdog</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>fieldkit</string>
    <string>watchdog</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
"""


def _run(args, timeout=60):
    try:
        r = fkproc.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def plan(platform=None, python=None):
    plat = (platform or sys.platform).lower()
    if plat in ("win32", "windows"):
        return {"platform": "windows", "status": "not-this-platform",
                "why": "the existing scheduled task from 2026-10-09 already starts it at sign-in, "
                       "it works, and Windows is deliberately left alone",
                "steps": []}
    py = python or sys.executable
    if plat in ("darwin", "macos"):
        return {"platform": "darwin", "status": "plan",
                "steps": [
                    {"step": "launchagent plist", "detail": str(PLIST), "done": PLIST.exists()},
                    {"step": "load", "detail": f"launchctl load -w {PLIST}", "done": PLIST.exists()},
                ],
                "python": py,
                "needs_root": False,
                "next": "fieldkit watchdog install --apply"}
    enabled = _run(["systemctl", "--user", "is-enabled", UNIT_NAME])[0] == 0
    return {"platform": "linux", "status": "plan",
            "steps": [
                {"step": "unit file", "detail": str(UNIT), "done": UNIT.exists()},
                {"step": "enable", "detail": f"systemctl --user enable --now {UNIT_NAME}", "done": enabled},
            ],
            "python": py,
            "needs_root": False,
            "next": "fieldkit watchdog install --apply"}


def apply(platform=None, python=None, run=None):
    plat = (platform or sys.platform).lower()
    if plat in ("win32", "windows"):
        return plan(platform)
    r = run or _run
    py = python or sys.executable
    if plat in ("darwin", "macos"):
        PLIST_DIR.mkdir(parents=True, exist_ok=True)
        PLIST.write_text(PLIST_BODY.format(python=py), encoding="utf-8")
        rc, out = r(["launchctl", "load", "-w", str(PLIST)])
        return {"platform": "darwin",
                "status": "running" if rc == 0 else "failed",
                "done": [f"wrote {PLIST}"] + (["loaded LaunchAgent"] if rc == 0 else []),
                "why": "" if rc == 0 else out[-300:],
                "stop_with": f"launchctl unload -w {PLIST}"}
    if not shutil.which("systemctl"):
        return {"platform": "linux", "status": "blocked",
                "why": "no systemctl here; start `fieldkit watchdog run` however this system starts "
                       "user programs at login"}
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(UNIT_BODY.format(python=py))
    done = [f"wrote {UNIT}"]
    rc, out = r(["systemctl", "--user", "daemon-reload"])
    if rc != 0:
        return {"platform": "linux", "status": "failed", "done": done, "why": out[-300:]}
    rc, out = r(["systemctl", "--user", "enable", "--now", UNIT_NAME])
    if rc != 0:
        return {"platform": "linux", "status": "failed", "done": done, "why": out[-300:]}
    done.append("enabled and started")
    # Verify the ARTEFACT: ask systemd whether it is actually running rather than
    # trusting the exit code of the command that started it.
    _rc, active = r(["systemctl", "--user", "is-active", UNIT_NAME])
    return {"platform": "linux",
            "status": "running" if active.strip() == "active" else "enabled-not-running",
            "done": done, "is_active": active.strip(),
            "stop_with": f"systemctl --user disable --now {UNIT_NAME}"}


def uninstall(platform=None, run=None):
    """Leave no trace. A tool that cannot be removed is a tool nobody will try."""
    plat = (platform or sys.platform).lower()
    if plat in ("win32", "windows"):
        return {"platform": "windows", "status": "not-this-platform"}
    r = run or _run
    if plat in ("darwin", "macos"):
        r(["launchctl", "unload", "-w", str(PLIST)])
        removed = []
        if PLIST.exists():
            PLIST.unlink()
            removed.append(str(PLIST))
        return {"platform": "darwin", "status": "removed", "removed": removed}
    r(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    removed = []
    if UNIT.exists():
        UNIT.unlink()
        removed.append(str(UNIT))
    r(["systemctl", "--user", "daemon-reload"])
    return {"platform": "linux", "status": "removed", "removed": removed,
            "note": "the log and pause file in the state folder are left alone; they are a record"}


def windows_event_hook_scripts(out_dir=None, python=None):
    """Generate deterministic Windows Scheduled Task script for logon watchdog + WLAN Event 8002/4003 offline triage.

    Never touches existing Windows scheduled tasks automatically; writes a script only when `out_dir` is supplied.
    """
    py = Path(python or sys.executable)
    pyw = py.with_name("pythonw.exe") if py.name.lower() == "python.exe" and py.with_name("pythonw.exe").exists() else py
    ps_body = f"""# Offline Wi-Fi Watchdog + WLAN Event Hook registration (zero internet, zero AI model)
$ErrorActionPreference = 'Stop'
$py  = '{py}'
$pyw = '{pyw}'
$logDir = Join-Path $env:LOCALAPPDATA 'PortableNetworkHub'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 1. Logon daemon task (background windowless watchdog loop)
$actLoop = New-ScheduledTaskAction -Execute $pyw -Argument '-m fieldkit watchdog run'
$trgLoop = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName 'GorillaOnlineWatchdog' -Action $actLoop -Trigger $trgLoop -Force | Out-Null

# 2. Instant OS Event-Log hook on WLAN-AutoConfig failure (8002) or disconnect (8003/4003)
$xml = @"
<QueryList>
  <Query Id="0" Path="Microsoft-Windows-WLAN-AutoConfig/Operational">
    <Select Path="Microsoft-Windows-WLAN-AutoConfig/Operational">*[System[(EventID=8002 or EventID=8003)]]</Select>
  </Query>
</QueryList>
"@
$diagOut = Join-Path $logDir 'last-wlan-diagnose.json'
$cmd = "`"$py`" -m fieldkit diagnose --hours 1 --json > `"$diagOut`""
$cimClass = Get-CimClass -ClassName MSFT_TaskEventTrigger -Namespace Root/Microsoft/Windows/TaskScheduler
$trgEv = New-CimInstance -CimClass $cimClass -ClientOnly
$trgEv.Enabled = $true
$trgEv.Subscription = $xml
$actEv = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $cmd"
Register-ScheduledTask -TaskName 'GorillaWlanOfflineDiagnose' -Action $actEv -Trigger $trgEv -Force | Out-Null
Write-Output "Registered GorillaOnlineWatchdog and GorillaWlanOfflineDiagnose -> $logDir"
"""
    script_path = None
    if out_dir:
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        script_path = d / "register-wlan-offline-hooks.ps1"
        script_path.write_text(ps_body, encoding="utf-8")
    return {
        "ok": True,
        "script": str(script_path) if script_path else None,
        "tasks": ["GorillaOnlineWatchdog", "GorillaWlanOfflineDiagnose"],
        "events": ["Microsoft-Windows-WLAN-AutoConfig/Operational:8002", "Microsoft-Windows-WLAN-AutoConfig/Operational:8003"],
        "preview": ps_body if not script_path else f"wrote {script_path}",
    }
