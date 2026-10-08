"""Run a build-harness command in its own visible window that outlives this session.

    fieldkit build-harness window <command> [args ...]      e.g.  window build-run firefox-157.0-truth

Born 2026-10-08 (owner: "make sure is running in screen"). A long run started from an assistant's shell is that
shell's child: it is invisible, and it dies when the session ends (2026-10-0x: Explorer restarted from the session
vanished twice overnight). This starts `python -m fieldkit build-harness <command> ...` in a new PowerShell window
created by Windows itself (WMI Win32_Process.Create), so the owner can watch it and it keeps running whatever
happens to the session, the way `screen` keeps a job on Linux. Everything it prints also goes to a log file
(state/windows/<stamp>-<command>.log) that ends with "exit <code>"; the window stays open afterwards (-NoExit).
-> {"pid", "log"}. Windows only.
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
        f"& {_ps_quote(python)} -m fieldkit build-harness {argv} 2>&1 | ForEach-Object {{ $s = \"$_\"; $log.WriteLine($s); $s }}",
        "$code = $LASTEXITCODE",
        "$log.WriteLine('exit ' + $code)",
        "$log.Close()",
        "Write-Host ''",
        f"Write-Host ('Finished with exit ' + $code + '. Log: ' + {_ps_quote(log)})",
    ])


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
