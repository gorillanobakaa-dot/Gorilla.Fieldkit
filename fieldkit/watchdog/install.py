"""Start the watchdog at login.

Linux: a systemd USER unit. No root, no sudo, and `systemctl --user` is enough.
Windows: deliberately not handled here. The existing scheduled task set up on
2026-10-09 already does it, it works, and the owner's instruction on 2026-10-13
was to leave Windows alone. This reports that rather than changing anything.
"""
import shutil
import subprocess
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


def _run(args, timeout=60):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def plan(platform=None, python=None):
    if (platform or sys.platform) == "win32":
        return {"platform": "windows", "status": "not-this-platform",
                "why": "the existing scheduled task from 2026-10-09 already starts it at sign-in, "
                       "it works, and Windows is deliberately left alone",
                "steps": []}
    py = python or sys.executable
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
    if (platform or sys.platform) == "win32":
        return plan(platform)
    if not shutil.which("systemctl"):
        return {"platform": "linux", "status": "blocked",
                "why": "no systemctl here; start `fieldkit watchdog run` however this system starts "
                       "user programs at login"}
    r = run or _run
    py = python or sys.executable
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
    if (platform or sys.platform) == "win32":
        return {"platform": "windows", "status": "not-this-platform"}
    r = run or _run
    r(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    removed = []
    if UNIT.exists():
        UNIT.unlink()
        removed.append(str(UNIT))
    r(["systemctl", "--user", "daemon-reload"])
    return {"platform": "linux", "status": "removed", "removed": removed,
            "note": "the log and pause file in the state folder are left alone; they are a record"}
