"""The herald, on any platform: speaks up while a long run goes on, so nobody has to watch a window.

    python -m fieldkit.buildh.herald --name "the build run" [--pid PID] [--log FILE] [--every 60]
    python -m fieldkit.buildh.herald --say "a test"          one sentence, said the way the herald says it

The same job as herald.ps1 (2026-10-10), in Python so Linux has it too (2026-10-10, the cloud session brief):
  - on battery, it says so once at 20, 10 and 5 per cent (again after the charger was plugged in and pulled out);
  - when the watched process ends (or, with --log and no --pid, when the log gets its "exit N" line), it says the run
    finished, and with --log whether it passed.
The voice is offline and nothing is sent anywhere: Windows' own System.Speech; on Linux espeak-ng, espeak or spd-say
when one is installed. With no voice at all the sentence is printed as a "HERALD:" line, and the herald says which.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import power

WARN_AT = (20, 10, 5)
EXIT_LINE = re.compile(r"(?m)^exit (-?\d+)\s*$")


def voice(platform=None, which=shutil.which):
    """-> the command list that speaks one sentence (the sentence is appended), or None when there is no voice."""
    if (platform or sys.platform) == "win32":
        return ["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($args[0])"]
    for exe in ("espeak-ng", "espeak", "spd-say"):
        p = which(exe)
        if p:
            return [p, "--wait"] if exe == "spd-say" else [p]
    return None


def say(text, platform=None, which=shutil.which, run=subprocess.run, out=print):
    """Speak `text`. -> "spoken" or "printed" (no voice on this machine, or the voice failed)."""
    cmd = voice(platform, which)
    if cmd:
        try:
            if run(cmd + [text], capture_output=True, timeout=120).returncode == 0:
                return "spoken"
        except (OSError, subprocess.SubprocessError):
            pass
    out(f"HERALD: {text}", flush=True)
    return "printed"


def verdict(text):
    """What the run's log says about how it ended -> the words after the run's name."""
    m = re.search(r'"exit_code"\s*:\s*(-?\d+)', text)
    if m:
        return "has finished, and it passed" if int(m.group(1)) == 0 else "has finished, and it failed. Have a look."
    m = re.search(r'FINAL_RESULT"?\s*[:=]\s*"?(PASS|FAIL)', text)
    if m:
        return "has finished, and it passed" if m.group(1) == "PASS" else "has finished, and it failed. Have a look."
    codes = EXIT_LINE.findall(text)
    if codes:
        code = int(codes[-1])
        return "has finished, and it passed" if code == 0 else f"has stopped with code {code}. Have a look."
    return "has finished"


def _pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        import os
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def running(pid=0, log=None, alive=_pid_alive):
    if pid:
        return alive(pid)
    if not log:
        return False
    p = Path(log)
    if not p.is_file():
        return True
    return not EXIT_LINE.search(p.read_text(encoding="utf-8", errors="replace"))


def battery_warning(st, warned, name):
    """-> the sentence to say now, or None; `warned` (a set) remembers what was said since the charger was last in."""
    if st.get("mains") is not False or st.get("percent") is None:
        warned.clear()
        return None
    for t in WARN_AT:
        if st["percent"] <= t and t not in warned:
            warned.add(t)
            return f"Heads up. The battery is at {st['percent']} per cent and {name} is still running. Plug your charger in."
    return None


def watch(name="the run", pid=0, log=None, every=60, status=power.status, speak=say, sleep=time.sleep,
          alive=_pid_alive):
    """Speak battery warnings until the run ends, then its result. -> the last sentence said."""
    warned = set()
    while running(pid, log, alive):
        w = battery_warning(status(), warned, name)
        if w:
            speak(w)
        sleep(every)
    # the result file is written just after the process ends: give it up to two minutes
    for _ in range(24):
        if not log or Path(log).is_file():
            break
        sleep(5)
    text = Path(log).read_text(encoding="utf-8", errors="replace") if log and Path(log).is_file() else ""
    last = f"{name} {verdict(text).rstrip('.')}."
    speak(last)
    return last


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fieldkit.buildh.herald")
    ap.add_argument("--name", default="the run")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--log", default="")
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--say", default=None)
    a = ap.parse_args(argv)
    if a.say is not None:
        how = say(a.say)
        print(f"herald: {how}" + ("" if how == "spoken" else " (no offline voice on this machine: install espeak-ng)"))
        return 0
    watch(a.name, a.pid, a.log or None, a.every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
