"""Run a build-harness command in its own visible window that outlives this session.

    fieldkit build-harness window <command> [args ...]      e.g.  window build-run firefox-157.0-truth

Born 2026-10-08 (owner: "make sure is running in screen"). A long run started from an assistant's shell is that
shell's child: it is invisible, and it dies when the session ends (2026-10-0x: Explorer restarted from the session
vanished twice overnight). This starts `python -m fieldkit build-harness <command> ...` in a new PowerShell window
created by Windows itself (WMI Win32_Process.Create), so the owner can watch it and it keeps running whatever
happens to the session, the way `screen` keeps a job on Linux. Everything it prints also goes to a log file
(state/windows/<stamp>-<command>.log) that ends with "exit <code>"; the window stays open afterwards (-NoExit).
-> {"pid", "log"}. Windows only.

The machine is kept awake while the command runs (2026-10-09: a build started at 00:13 sat in its gate all night while
the laptop went to standby and sleep - 570 CPU-seconds in seven and a half hours). The window asks Windows not to
idle-sleep and not to turn the screen off (SetThreadExecutionState ES_CONTINUOUS|ES_SYSTEM_REQUIRED|
ES_DISPLAY_REQUIRED, the request a video player makes; no setting is changed): on a Modern Standby laptop the screen
going off is standby itself. The request is cleared when the command ends. A closed lid or a chosen Sleep still sleeps.

    fieldkit build-harness awake <pid>     the same request for a run already going, until that process exits
    fieldkit build-harness follow [<log>|latest] [cmd=build-run] [every=1] [timeout=S]
                                           the log's milestones as they are written; exits with the run's exit code
"""
import base64
import re
import subprocess
import sys
import time
from pathlib import Path

from . import task

FK = Path(__file__).resolve().parents[2]


def log_path(args):
    d = task.STATE.parent / "windows"
    d.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w.-]+", "_", "-".join(args[:2]))[:60] or "run"
    return d / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.log"


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


AWAKE_TYPE = ("Add-Type -Namespace GorillaHarness -Name Power -MemberDefinition "
              "'[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint esFlags);'")
# ES_CONTINUOUS|SYSTEM_REQUIRED|DISPLAY_REQUIRED: on this Modern Standby laptop the screen turning off IS standby
# (2026-10-09: with SYSTEM_REQUIRED alone it entered Modern Standby eleven times during one build, and desktop programs
# are held there), so the screen stays on while a command runs
AWAKE_ON = "[void][GorillaHarness.Power]::SetThreadExecutionState([uint32]'0x80000003')"
AWAKE_OFF = "[void][GorillaHarness.Power]::SetThreadExecutionState([uint32]'0x80000000')"    # ES_CONTINUOUS: release


def script(args, log, python=None):
    """The PowerShell the window runs (UTF-8 throughout; every line to the log too, as it comes; the exit code at the
    end). Not Tee-Object: in Windows PowerShell 5.1 it writes UTF-16 and has no -Encoding (2026-10-08: a build log
    nothing could grep, with the UTF-8 "exit" line appended to UTF-16 text)."""
    python = python or sys.executable
    argv = " ".join(_ps_quote(a) for a in args)
    title = "Gorilla build-harness: " + " ".join(args)[:80]
    return "\n".join([
        f"$Host.UI.RawUI.WindowTitle = {_ps_quote(title)}",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        "$env:PYTHONUNBUFFERED = '1'",
        "$env:PYTHONUTF8 = '1'",
        f"Set-Location {_ps_quote(FK)}",
        f"$log = New-Object System.IO.StreamWriter({_ps_quote(log)}, $true, (New-Object System.Text.UTF8Encoding($false)))",
        "$log.AutoFlush = $true",
        AWAKE_TYPE,
        AWAKE_ON,
        f"& {_ps_quote(python)} -m fieldkit build-harness {argv} 2>&1 | ForEach-Object {{ $s = \"$_\"; $log.WriteLine($s); $s }}",
        "$code = $LASTEXITCODE",
        AWAKE_OFF,
        "$log.WriteLine('exit ' + $code)",
        "$log.Close()",
        "Write-Host ''",
        f"Write-Host ('Finished with exit ' + $code + '. Log: ' + {_ps_quote(log)})",
    ])


def awake_script(pid):
    """PowerShell that keeps the machine from idle-sleeping until process `pid` exits, then releases the request."""
    return "\n".join([AWAKE_TYPE, AWAKE_ON, f"Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue", AWAKE_OFF])


def hold_awake(pid, run=subprocess.run):
    """Start (through WMI, so it outlives the session) a hidden PowerShell holding the stay-awake request until `pid`
    exits -> {"pid": the holder's pid}; refuses a pid that is not running."""
    chk = run(["powershell", "-NoProfile", "-Command", f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ 'yes' }}"],
              capture_output=True, text=True, timeout=60).stdout.strip()
    if chk != "yes":
        raise task.Refused(f"process {pid} is not running")
    enc = base64.b64encode(awake_script(pid).encode("utf-16-le")).decode("ascii")
    cl = f"powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand {enc}"
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=" + _ps_quote(cl)
          + "}; Write-Output ($r.ReturnValue.ToString() + ' ' + $r.ProcessId)")
    parts = run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.split()
    if len(parts) != 2 or parts[0] != "0":
        raise task.Refused(f"Windows did not start the stay-awake holder ({' '.join(parts) or 'nothing'})")
    return {"pid": int(parts[1])}


def read_log(path):
    """A window's log as text: UTF-8, or UTF-16 for logs written before 2026-10-08 (Tee-Object)."""
    b = Path(path).read_bytes()
    if b[:2] != b"\xff\xfe":
        return b.decode("utf-8", "replace")
    i = b.rfind(b"exit ")            # the UTF-8 "exit <code>" line Add-Content appended to Tee-Object's UTF-16
    body, tail = (b[:i], b[i:]) if i > 0 and b"\x00" not in b[i:] else (b, b"")
    return body.decode("utf-16-le", "replace").lstrip("﻿") + tail.decode("utf-8", "replace")


def command_line(args, log, python=None):
    """powershell.exe with the script base64-encoded (no quoting can break it)."""
    enc = base64.b64encode(script(args, log, python).encode("utf-16-le")).decode("ascii")
    return f"powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -EncodedCommand {enc}"


def launch(args, run=subprocess.run):
    """Start the window through WMI -> {"pid", "log"}; raises task.Refused when Windows did not start it."""
    if not args:
        raise task.Refused("window <command> [args ...]: which build-harness command?")
    log = log_path(args)
    cl = command_line(args, log)
    ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=" + _ps_quote(cl)
          + "; CurrentDirectory=" + _ps_quote(FK) + "}; Write-Output ($r.ReturnValue.ToString() + ' ' + $r.ProcessId)")
    out = run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60).stdout.strip()
    parts = out.split()
    if len(parts) != 2 or parts[0] != "0":
        raise task.Refused(f"Windows did not start the window (Win32_Process.Create returned: {out or 'nothing'})")
    return {"pid": int(parts[1]), "log": str(log)}


# -- follow: the window's log as milestones, for a person or a watcher ------------------------------------------------
# Born 2026-10-09: the assistant's ad-hoc watchers (tail | grep | cut) went silent for 30 minutes because `cut` buffers
# when it writes to a pipe; a watcher that only matched success lines would also have stayed silent through a crash.
# This prints each milestone line as soon as it is written (flushed) and ends with the run's own exit code.
MILESTONE = re.compile(r"gate passed|GATE FAILED|pre-build \[|NOT OK|NOT PASSED|STOPPED|HALT|build attempt|build OK|"
                       r"package attempt|package OK|dist/bin sweep|BUILD-VERIFY|BUILD OK|INSTALL OK|POST-INSTALL|"
                       r"REPLAY|timings|CHECK-CHANGE|\bFAIL\b|\[FAIL\]|SKIPPED|Traceback|Error:|^exit -?\d+$", re.I)
EXIT = re.compile(r"^exit (-?\d+)$")


def latest_log(cmd=None, folder=None):
    """The newest window log, or the newest of one command (build-run, post-install, ...)."""
    d = Path(folder) if folder else task.STATE.parent / "windows"
    logs = sorted(p for p in d.glob("*.log") if not cmd or f"-{cmd}-" in p.name or p.stem.endswith(f"-{cmd}"))
    return logs[-1] if logs else None


def follow(path, say=print, poll=2.0, sleep=time.sleep, timeout=None, every=False, clock=time.time):
    """Print each milestone line (every line with every=True) of a window log as it is written. -> the run's exit code
    once its "exit N" line arrives, or None at `timeout` seconds."""
    seen, start = 0, clock()
    while True:
        p = Path(path)
        text = read_log(p) if p.is_file() else ""
        lines = text.splitlines()
        if text and not text.endswith(("\n", "\r")):
            lines = lines[:-1]                       # a line still being written waits for its end
        for l in lines[seen:]:
            s = l.strip().lstrip("\ufeff")
            if every or MILESTONE.search(s):
                say(s)
            m = EXIT.match(s)
            if m:
                return int(m.group(1))
        seen = len(lines)
        if timeout is not None and clock() - start >= timeout:
            return None
        sleep(poll)
