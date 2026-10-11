"""PowerShell from Python, the one way: the script goes in as -EncodedCommand (no quoting of the command line, no
script file), its output comes back as UTF-8, and JSON is found in it even after a warning line."""
import base64
import json
import subprocess

PRELUDE = "$ProgressPreference='SilentlyContinue'\n[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"


def q(text):
    """A value for a PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def run_ps(script, timeout=300, run=subprocess.run):
    enc = base64.b64encode((PRELUDE + script).encode("utf-16-le")).decode()
    p = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, p.stdout or "", p.stderr or ""


def ps_json(script, timeout=300, run=subprocess.run):
    code, out, err = run_ps(script, timeout, run)
    out = out.strip()
    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    if not starts:
        raise RuntimeError((err or out or "PowerShell gave no output").strip()[:300])
    return json.loads(out[min(starts):])
