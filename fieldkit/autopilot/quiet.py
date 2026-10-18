# Version: 1.0.0 · updated 26-10-18-18-00
"""Quiet machine: the apps that call home are stopped by PID before a measuring run, and KEPT stopped until it ends.

Owner, 2026-10-17 (to the overnight agent): "this is Windows and everything calls home. Kill the noisy apps AND MAKE
SURE THEY STAY KILLED". Only a list was made; WhatsApp and Telegram restart themselves. Rules:
  - by PID, never by name (the owner runs the same programs; memory: stop only what I started / quiet machine);
  - never the agent's own app: every ancestor of this process and every descendant of those ancestors is spared
    (the owner's question "does closing msedge/msedgewebview2 affect you?" - it does if the agent's app runs on it,
    so whatever hosts the run is never touched, whatever its name);
  - the names come from fieldkit.local.json autopilot.quiet (default below), matched on the process name;
  - every stop is logged with name, PID and path; nothing is uninstalled or disabled; they start again normally after.

    fieldkit autopilot quiet [--hold PID]   list what holds network sockets, stop the noisy ones;
                                            --hold: repeat every 30 s until PID exits
"""
import os
import time

DEFAULT = ["WhatsApp", "Telegram", "LM Studio", "Codex", "ChatGPT", "Discord", "Slack", "Teams", "OneDrive",
           "Dropbox", "Spotify", "KDE Connect", "kdeconnect", "CrossDeviceService", "PhoneExperienceHost", "YourPhone"]


def names():
    try:
        from ..core.settings import local_settings
        return (local_settings().get("autopilot") or {}).get("quiet") or DEFAULT
    except Exception:  # noqa: BLE001
        return DEFAULT


def protected_pids(psutil, me=None):
    """This process, its ancestors, and every descendant of those ancestors (the agent app and its helpers)."""
    me = psutil.Process(me or os.getpid())
    keep = {me.pid}
    for a in me.parents():
        keep.add(a.pid)
        try:
            keep.update(c.pid for c in a.children(recursive=True))
        except psutil.Error:
            pass
    return keep


def noisy(psutil=None, wanted=None):
    """-> [{pid, name, exe, net}] processes matching the quiet list (case-insensitive substring of the name), the
    agent's own tree excluded; `net` = how many sockets it holds."""
    if psutil is None:
        import psutil
    wanted = [w.lower() for w in (wanted or names())]
    keep = protected_pids(psutil)
    sockets = {}
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.pid:
                sockets[c.pid] = sockets.get(c.pid, 0) + 1
    except (psutil.Error, OSError):
        pass
    out = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        n = (p.info.get("name") or "").lower()
        if p.info["pid"] in keep or not any(w.replace(" ", "") in n.replace(" ", "") for w in wanted):
            continue
        out.append({"pid": p.info["pid"], "name": p.info.get("name"), "exe": p.info.get("exe") or "",
                    "net": sockets.get(p.info["pid"], 0)})
    return out


def stop(rows, psutil=None, say=print):
    if psutil is None:
        import psutil
    done = []
    for r in rows:
        try:
            psutil.Process(r["pid"]).kill()
            done.append(r)
            say(f"  stopped {r['name']} PID {r['pid']} ({r['net']} socket(s)) {r['exe']}")
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as e:
            say(f"  could not stop {r['name']} PID {r['pid']}: {e}")
    return done


def hold(pid, every=30, say=print, psutil=None, sleep=time.sleep):
    """Stop the noisy apps now and again every `every` s while `pid` runs. -> total stops."""
    if psutil is None:
        import psutil
    n = 0
    while psutil.pid_exists(int(pid)):
        n += len(stop(noisy(psutil), psutil, say=say))
        sleep(every)
    say(f"  quiet: {pid} has ended; {n} stop(s); the apps start normally again")
    return n
