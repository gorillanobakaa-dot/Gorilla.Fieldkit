"""Keep this laptop online, or say so out loud when it cannot.

    fieldkit watchdog status             online now? what network? is it running?
    fieldkit watchdog run                the loop, in this terminal
    fieldkit watchdog test               say the alarm once and stop
    fieldkit watchdog pause MINUTES      no alarms for a while (a deliberate offline test)
    fieldkit watchdog install [--apply]  start it at login

# WHY THIS EXISTS

From the original Windows script, 2026-09-23: while testing the Portable Network
Hub, an AI session repeatedly took the laptop off its only internet, the owner's
phone hotspot, by switching the wifi radio off to measure the hotspot in "bush"
conditions. The owner had no idea the laptop needed help, and the AI could not
ask, because the thing it would have asked through was the connection it had
just broken.

So this is deliberately NOT an AI tool. It is a dead simple loop that opens a
TCP socket, and if it cannot, rejoins the wifi by itself and then shouts.

# WHAT CHANGED, 2026-10-13

It was Windows-only, and it lived as a loose script in Documents/Scripts rather
than in the harness. This is the same logic as one cross-platform module, which
is what the handover asked for: "Fieldkit is meant to be one cross-platform
codebase, not duplicated scripts."

Windows behaviour is unchanged: same netsh calls, same PowerShell radio
incantation, same wording. Only Linux is new (nmcli, and a systemd user unit
instead of a scheduled task).

It also no longer carries its own speak(). It calls the herald, which goes
through whatever voice the machine is configured to use. On Linux that is now
the neural voice (see fieldkit/voice), so the alarm sounds like a person rather
than a 1995 robot, without this module knowing anything about it.
"""
import datetime as dt
import os
import socket
import sys
import time

from .net import current_ssid, reconnect, state_dir, tools_present

PROBES = [("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443), ("208.67.222.222", 443)]

# SHORT, AND CALM. Measured 2026-10-13: the old danger alert took 8.58 seconds
# and the rewritten one takes 3.39, with NO change of pace, purely by removing
# words that were not information.
#
# The alarm repeats while nobody is in the room, so every word is paid for on
# every repetition. "and cannot get back on the internet" restates "offline",
# and "Claude needs your help" is the program talking about itself rather than
# telling you what to do.
#
# It is NOT hurried. A calm voice is correct for an emergency: aviation and
# medical alarm practice settled this long ago, because a panicked voice
# induces panic and degrades comprehension. Urgency comes from what is said
# and from repetition, never from delivery. Do not "fix" this to sound urgent.
ALARM = "The laptop is offline. Switch the hotspot on."
RECOVERED = "The laptop is back online."
TEST = "This is a test of the offline alarm."


def log_path(platform=None):
    return state_dir(platform) / "online-watchdog.log"


def pause_path(platform=None):
    return state_dir(platform) / "online-watchdog.pause"


def log(line, platform=None):
    d = state_dir(platform)
    d.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with (d / "online-watchdog.log").open("a", encoding="utf-8") as f:
        f.write(f"{stamp}  {line}\n")


def online(timeout=2.5, probes=None):
    """A TCP connection opens to one of several well-known addresses.

    A socket, deliberately, not an HTTP request: it proves the route works
    without fetching anything, so nothing is sent anywhere and no page is read.
    """
    for host, port in (probes or PROBES):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def speak(text, platform=None):
    """Say it through the machine's configured voice.

    This used to be a PowerShell System.Speech call inlined here. It now defers
    to the herald, so on Linux it goes through speech-dispatcher and inherits
    whatever voice the owner set up, and on Windows it is the same System.Speech
    path as before.
    """
    try:
        from ..buildh import herald
        herald.say(text)
    except Exception as e:                      # never let a mute machine stop the loop
        log(f"could not speak ({e}): {text}", platform)
        return "not-spoken"
    log(f"spoke: {text}", platform)
    return "spoken"


def paused(platform=None):
    try:
        return time.time() < float(pause_path(platform).read_text().strip())
    except (OSError, ValueError):
        return False


def set_pause(minutes, platform=None):
    d = state_dir(platform)
    d.mkdir(parents=True, exist_ok=True)
    (d / "online-watchdog.pause").write_text(str(time.time() + minutes * 60))
    log(f"alarms paused for {minutes} minutes", platform)
    return {"paused_minutes": minutes, "file": str(pause_path(platform))}


def status(platform=None):
    """What a person wants to know without reading a log."""
    missing = tools_present(platform)
    return {"online": online(), "ssid": current_ssid(platform), "paused": paused(platform),
            "log": str(log_path(platform)), "missing_tools": missing,
            "ok": not missing}


def loop(interval=5, reconnect_after=20, alarm_after=90, repeat=60, ssid="",
         platform=None, forever=True, _now=time.time, _sleep=time.sleep):
    """The watchdog itself. `forever=False` runs one pass, which is what tests use."""
    log(f"watchdog started (pid {os.getpid()}), reconnect after {reconnect_after} s, "
        f"alarm after {alarm_after} s", platform)
    remembered, offline_since = ssid, None
    last_try = last_alarm = 0.0
    alarmed = False
    while True:
        if online():
            if offline_since is not None:
                log(f"back online after {int(_now() - offline_since)} s", platform)
                if alarmed:
                    speak(RECOVERED, platform)
            offline_since, alarmed = None, False
            seen = current_ssid(platform)
            if seen and not ssid and seen != remembered:
                remembered = seen
                log(f"online via '{seen}'", platform)
        else:
            now = _now()
            if offline_since is None:
                offline_since = now
                log(f"offline (last network: '{remembered or 'unknown'}')", platform)
            down = now - offline_since
            if down >= reconnect_after and now - last_try >= 15:
                last_try = now
                reconnect(remembered, platform, log=lambda s: log(s, platform))
            if down >= alarm_after and now - last_alarm >= repeat and not paused(platform):
                last_alarm, alarmed = now, True
                speak(ALARM, platform)
        if not forever:
            return {"offline_since": offline_since, "remembered": remembered, "alarmed": alarmed}
        _sleep(interval)
