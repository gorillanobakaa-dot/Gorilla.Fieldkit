"""Run the HDMI Harness (gorillanobakaa-dot/Gorilla.HDMI.Harness) so an agent gets its answer.

    fieldkit hdmi status | doctor | diag [--area laptop|tv] | profile-list [NAME] | tv-info | tv-wifi-report [SSID]
    fieldkit hdmi fix | test-quick [--only H01,H07]

What the harness is for: a Windows laptop on a TV by HDMI, connected and kept connected. Its known failure is a
stuck GPU driver - the TV is detected but the driver offers 0 modes and SetDisplayConfig fails (error 1610) - and
the repair is `pnputil /restart-device` on the adapter the TV hangs off (fix.py: restart_tv_gpu). That needs
administrator rights; without them the harness logs "cannot restart the GPU driver: not elevated" and the TV stays
dark. The repair runs only when activation has failed: an active TV is never restarted.

Why a launcher: the harness elevates itself by relaunching in a NEW window that waits for ENTER (--pause), so a
caller gets nothing back; and with --no-elevate, `fix` cannot do the one repair it exists for. Here, `fix` and
`test-quick` run elevated, waited for (Start-Process -Verb RunAs -Wait), with --no-elevate passed to the elevated
copy so it does not relaunch again; the output is captured and the exit code returned. Windows shows its consent
prompt: that is the owner's approval, every time. The read-only commands run unelevated.

test-quick reads (H01-H08), then puts the TV back in the configured topology (fix.ensure, no mode change): on a
working TV that changes nothing; on a stuck one it is the same repair the background helper would make.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..core import settings
from ..win11 import run as w11

READ_ONLY = {"status": ["status"], "doctor": ["doctor"], "diag": ["diag"], "profile-list": ["profile", "list"],
             "tv-info": ["tv", "info"], "tv-wifi-report": ["tv", "wifi-report"]}
ELEVATED = {"fix": ["fix"], "test-quick": ["test", "quick"]}


def harness_dir(path=None):
    return Path(path or os.environ.get("FIELDKIT_HDMI") or settings.ROOT / "toolbox" / "hdmi-harness")


def python_exe():
    """The harness's own venv (imports.yaml), else this Python."""
    v = settings.ROOT / "_sources" / "venv-hdmi" / "Scripts" / "python.exe"
    return str(v) if v.is_file() else sys.executable


def argv_for(command, extra=(), path=None):
    if command not in READ_ONLY and command not in ELEVATED:
        raise ValueError(f"unknown command {command!r}: {', '.join([*READ_ONLY, *ELEVATED])}")
    words = (READ_ONLY.get(command) or ELEVATED[command]) + list(extra)
    pre = [] if command == "doctor" else ["--no-elevate"]       # doctor never elevates (hdmi.py: _bare)
    return [python_exe(), str(harness_dir(path) / "hdmi.py")] + pre + words, command in ELEVATED


def run(command, extra=(), path=None, runner=subprocess.run, admin=None):
    """-> (exit code, output)."""
    argv, elevate = argv_for(command, extra, path)
    if not Path(argv[1]).is_file():
        raise FileNotFoundError(f"{argv[1]} is not here: fieldkit gather --only hdmi-harness")
    if elevate and not (w11.is_admin() if admin is None else admin):
        fd, out = tempfile.mkstemp(prefix="fieldkit-hdmi-", suffix=".txt")
        os.close(fd)
        try:
            r = runner(w11.elevated_wrapper(argv, out), capture_output=True, text=True)
            text = Path(out).read_text(encoding="utf-8", errors="replace") if Path(out).is_file() else ""
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        return r.returncode, (text + (r.stderr or "")).strip()
    r = runner(argv, capture_output=True, text=True)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
