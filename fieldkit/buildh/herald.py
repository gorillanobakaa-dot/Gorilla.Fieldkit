"""The herald, on any platform: speaks up while a long run goes on, so nobody has to watch a window.

    fieldkit build-harness herald "a test"                   one sentence, said the way the herald says it
    fieldkit build-harness herald name="the build run" [pid=PID] [log=FILE] [every=60]
    python -m fieldkit.buildh.herald ...                     the same, as the run window starts it

The same job as herald.ps1 (2026-10-10), in Python so Linux has it too (2026-10-10, the cloud session brief):
  - on battery, it says so once at 20, 10 and 5 per cent (again after the charger was plugged in and pulled out);
  - when the watched process ends (or, with --log and no --pid, when the log gets its "exit N" line), it says the run
    finished, and with --log whether it passed.
The voice is offline and nothing is sent anywhere: Windows' own System.Speech; on Linux espeak-ng, espeak or spd-say
when one is installed. With no voice at all the sentence is printed as a "HERALD:" line, and the herald says which.
"""
import argparse
import os
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
        # the sentence travels in FIELDKIT_SAY, never on the command line: with -Command, powershell.exe joins any
        # word after the script INTO the script (2026-10-10 on the owner's laptop: "test" broke it, nothing spoken)
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Add-Type -AssemblyName System.Speech; "
                "(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak($env:FIELDKIT_SAY)"]
    for exe in ("espeak-ng", "espeak", "spd-say"):
        p = which(exe)
        if p:
            return [p, "--wait"] if exe == "spd-say" else [p]
    return None


LAST_PROBLEM = {"why": ""}

PURPOSE = ("The herald speaks out loud while a long build or check runs: it warns when the battery drops to 20, 10 and "
           "5 per cent, and it says when the run has finished and whether it passed, so nobody has to sit and watch the "
           "screen. The voice works offline; nothing is sent anywhere.")
# the voice program can succeed with no sound at all (no speakers, muted): only a person can say it was heard
HEARD = (" - the voice program ran. Did you hear it? If not: check the sound is on, the volume is up and the right "
         "speakers or headphones are chosen, then try again.")
WIN_PROBE = ("Add-Type -AssemblyName System.Speech; "
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices().Count")


def voice_check(platform=None, which=shutil.which, run=subprocess.run, h=None):
    """Is there an offline voice on THIS machine? Checked, never assumed (2026-10-10: the owner's laptop printed
    instead of speaking, and the message blamed a program that is not used on Windows).
    -> {"ok", "voice", "why", "install": [lines], "purpose"}"""
    win = (platform or sys.platform) == "win32"
    if win:
        try:
            r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WIN_PROBE], capture_output=True,
                    text=True, timeout=60)
            n = int((r.stdout or "0").strip().splitlines()[-1]) if r.returncode == 0 and (r.stdout or "").strip() else 0
        except (OSError, subprocess.SubprocessError, ValueError):
            n = 0
        if n:
            return {"ok": True, "voice": f"Windows speech ({n} voice(s) installed)", "why": "", "install": [],
                    "purpose": PURPOSE}
        return {"ok": False, "voice": None, "purpose": PURPOSE,
                "why": "Windows speech has no voice installed, or PowerShell could not load it.",
                "install": ["Open Settings, then Time & language, then Speech. Under 'Manage voices' choose 'Add "
                            "voices', pick English, and wait until it says installed. Then run the test again."]}
    cmd = voice(platform, which)
    if cmd:
        return {"ok": True, "voice": cmd[0], "why": "", "install": [], "purpose": PURPOSE}
    from ..core.host import is_debian_family
    line = "sudo apt-get install -y espeak-ng" if is_debian_family() else "sudo dnf install -y espeak-ng"
    return {"ok": False, "voice": None, "purpose": PURPOSE,
            "why": "This computer has no offline voice program. The herald uses espeak-ng (or espeak, or spd-say).",
            "install": [line]}


def explain(check):
    """The plain-words answer when there is no voice: what is missing, why it matters, the exact next steps."""
    if check["ok"]:
        return [f"Voice found: {check['voice']}."]
    return (["NO VOICE ON THIS COMPUTER - the herald cannot speak yet.", "",
             f"Why it matters: {check['purpose']}", "", f"What is missing: {check['why']}", "", "To fix it:"]
            + [f"  {i}. {l}" for i, l in enumerate(check["install"], 1)]
            + [f"  {len(check['install']) + 1}. Then test it again: fieldkit build-harness herald \"test\"", "",
               "Until then the herald still works: it prints its sentences on the screen instead of speaking them."])


def say(text, platform=None, which=shutil.which, run=subprocess.run, out=print):
    """Speak `text`. -> "spoken" or "printed" (no voice on this machine, or the voice failed: LAST_PROBLEM says why)."""
    win = (platform or sys.platform) == "win32"
    cmd = voice(platform, which)
    LAST_PROBLEM["why"] = "" if cmd else "no offline voice on this machine: install espeak-ng"
    if cmd:
        argv = cmd if win else cmd + [text]
        env = dict(os.environ, FIELDKIT_SAY=text)
        try:
            r = run(argv, capture_output=True, timeout=120, env=env)
            if r.returncode == 0:
                return "spoken"
            raw = getattr(r, "stderr", b"") or b""
            err = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            LAST_PROBLEM["why"] = ("Windows speech (System.Speech) failed: " if win else f"{argv[0]} failed: ") + \
                (err.strip().splitlines() or ["no message"])[-1][:200]
        except (OSError, subprocess.SubprocessError) as e:
            LAST_PROBLEM["why"] = f"{argv[0]} could not run: {e}"
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
        print(f"herald: {how}" + (HEARD if how == "spoken" else f" ({LAST_PROBLEM['why']})"))
        if how != "spoken":
            print("\n".join(explain(voice_check())))
        return 0
    watch(a.name, a.pid, a.log or None, a.every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
