# Version: 1.1.0 · updated 26-10-18-20-00
"""Quiet machine: every app that talks to the network is stopped by PID before a measuring run, and KEPT stopped until
it ends.

Owner, 2026-10-17 (to the overnight agent): "this is Windows and everything calls home. Kill the noisy apps AND MAKE
SURE THEY STAY KILLED". Owner, 2026-10-18: "Dolby Access, Intel Graphics Command, Lenovo tools, LM Studio, Telegram,
Zoom, WhatsApp, Antigravity, ChatGPT Codex, KDE Connect, the phone app ... constantly pinging all the UDP ports".
Version 1.0 stopped a short NAME list once; apps restart themselves, and an app not on the list kept talking. Now:

  - DISCOVERED, not listed: every process outside Windows' own folder that holds a non-loopback socket (TCP or UDP)
    is noisy, whatever its name; the name list (fieldkit.local.json autopilot.quiet, default below) adds apps that
    wake up now and then and hold no socket at the moment of the sweep;
  - HELD: `hold(pid)` repeats the sweep every 30 s while the measuring run lives (the leak gate starts it for its
    whole run), so an app that restarts is stopped again;
  - by PID, never by name (the owner runs the same programs); every stop is logged with name, PID, path and sockets;
    nothing is uninstalled or disabled; they start again normally afterwards;
  - SPARED: Windows' own processes (C:\\Windows, System), the measuring run and everything it started (the browser
    under test), this process's ancestors and their descendants, and the agent watching the run (Claude, by its
    executable, because a run started detached through WMI has no agent among its ancestors).

    fieldkit autopilot quiet [--apply] [--hold PID | --minutes N]   (new processes judged every 2 s, all every 30 s)
"""
import os
import time
from pathlib import Path

DEFAULT = ["WhatsApp", "Telegram", "LM Studio", "LMStudio", "lms", "Codex", "ChatGPT", "Discord", "Slack", "Teams",
           "Zoom", "OneDrive", "Dropbox", "Spotify", "KDE Connect", "kdeconnect", "CrossDeviceService",
           "PhoneExperienceHost", "YourPhone", "Antigravity", "Dolby", "DAX3API", "IGCC", "IntelGraphicsSoftware",
           "OneApp.IGCC", "LenovoVantage", "Lenovo.Modern", "ImController", "LenovoUtility", "msedge.exe"]
#: never stopped, whatever they hold: Windows itself and the agent that watches the run
SPARE_NAMES = {"system", "idle", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
               "lsass.exe", "svchost.exe", "dashost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "spoolsv.exe",
               "searchhost.exe", "conhost.exe", "openconsole.exe", "windowsterminal.exe", "python.exe", "pythonw.exe",
               "powershell.exe", "pwsh.exe", "cmd.exe", "pktmon.exe", "mitmdump.exe", "firefox.exe",
               "gorilla-unleashed.exe"}
AGENT_EXES = ("\\claude\\claude-code\\", "\\claude_", "\\anthropic\\")      # this Claude session and its app
WINDOWS = os.environ.get("SystemRoot", r"C:\Windows").lower() + "\\"
AGENT_EXES = AGENT_EXES + ("/claude-code/", "/.local/share/claude/", "/.claude/local/", "/@anthropic-ai/claude-code/",
                           "/bin/claude")

#: Linux (2026-10-18, owner: "this of course won't work on linux"): the same table (ss -tunap = netstat -ano), other
#: rules for what is the system. A process of a system user (UID below 1000), in a system path, or a kernel thread
#: (no executable) is the system: never stopped, but a system daemon that talks is NAMED (avahi's mDNS, cups-browsed)
#: because stopping it means changing a system service, which is the owner's call. The desktop session is spared.
LINUX_DEFAULT = ["kdeconnect", "telegram", "signal", "discord", "slack", "spotify", "steam", "zoom", "teams",
                 "antigravity", "lm-studio", "lmstudio", "lms", "whatsapp", "dropbox", "megasync", "syncthing",
                 "nextcloud", "chatgpt", "codex", "vivaldi", "chrome", "chromium", "brave", "opera", "skype"]
LINUX_SPARE = {"systemd", "kwin_wayland", "kwin_x11", "plasmashell", "ksmserver", "kded6", "kded5", "xorg", "xwayland",
               "gnome-shell", "mutter", "pipewire", "pipewire-pulse", "wireplumber", "pulseaudio", "dbus-daemon",
               "dbus-broker", "sddm", "gdm", "sshd", "networkmanager", "wpa_supplicant", "konsole", "bash", "zsh",
               "python3", "python", "sudo", "tcpdump", "tshark", "mitmdump", "firefox", "gorilla-unleashed",
               "gorilla-unleashed-bin", "screen", "tmux"}
LINUX_SYSTEM_PATHS = ("/usr/sbin/", "/sbin/", "/usr/lib/systemd/", "/lib/systemd/", "/usr/libexec/",
                      "/usr/lib/x86_64-linux-gnu/libexec/")


def is_system(name, exe, uid=None, platform=None):
    """True for what belongs to the operating system (and the session that runs it): never stopped."""
    import sys as _sys
    n, e = (name or "").lower(), (exe or "").lower()
    if (platform or _sys.platform) == "win32":
        return n in SPARE_NAMES or e.startswith(WINDOWS)
    return (not e or (uid is not None and uid < 1000) or e.startswith(LINUX_SYSTEM_PATHS)
            or n in LINUX_SPARE or n.split(".")[0] in LINUX_SPARE)


def names(platform=None):
    import sys as _sys
    base = DEFAULT if (platform or _sys.platform) == "win32" else LINUX_DEFAULT
    try:
        from ..core.settings import local_settings
        return (local_settings().get("autopilot") or {}).get("quiet") or base
    except Exception:  # noqa: BLE001
        return base


def _tree(psutil, pid):
    try:
        p = psutil.Process(pid)
        return {pid} | {c.pid for c in p.children(recursive=True)}
    except psutil.Error:
        return set()


def protected_pids(psutil, me=None, also=()):
    """This process, its ancestors and every descendant of those ancestors; each pid in `also` and its descendants
    (the measuring run and the browser it started); every agent process (AGENT_EXES) and its descendants."""
    me = psutil.Process(me or os.getpid())
    keep = {me.pid}
    for a in me.parents():
        keep.add(a.pid)
        try:
            keep.update(c.pid for c in a.children(recursive=True))
        except psutil.Error:
            pass
    for pid in also:
        keep |= _tree(psutil, int(pid))
    for p in psutil.process_iter(["pid", "exe"]):
        exe = (p.info.get("exe") or "").lower()
        if any(m in exe for m in AGENT_EXES):
            keep |= _tree(psutil, p.info["pid"])
    return keep


def _ancestors(psutil, pid, table=None):
    """[(pid, name, exe)] from the parent up; unreadable ones end the walk."""
    out = []
    try:
        for a in psutil.Process(pid).parents():
            try:
                out.append((a.pid, a.name(), a.exe()))
            except (psutil.Error, AttributeError):
                out.append((a.pid,) + tuple((table or {}).get(a.pid, ("", ""))))
    except psutil.Error:
        pass
    return out


def _sockets(psutil):
    """{pid: count} of sockets that reach beyond this computer (a remote address, or a UDP port bound on a real
    interface); loopback is the machine talking to itself."""
    out = {}
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.Error, OSError):
        return out
    for c in conns:
        if not c.pid:
            continue
        local = (c.laddr.ip if c.laddr else "") or ""
        remote = (c.raddr.ip if c.raddr else "") or ""
        loop = lambda ip: ip.startswith("127.") or ip in ("::1", "localhost")
        if remote and not loop(remote) or (not remote and c.type == 2 and local and not loop(local)):
            out[c.pid] = out.get(c.pid, 0) + 1
    return out


def noisy(psutil=None, wanted=None, also=(), only=None, platform=None, talking_system=None):
    """-> [{pid, name, exe, net, why}] the processes to stop: any process outside Windows' folder holding a
    non-loopback socket, or matching the name list; Windows, the measuring run (`also`), the agent and this
    process's own tree are spared."""
    if psutil is None:
        import psutil
    import sys as _sys
    platform = platform or _sys.platform
    wanted = [w.lower().replace(" ", "") for w in (wanted or names(platform))]
    keep = protected_pids(psutil, also=also)
    socks = _sockets(psutil)
    procs = list(psutil.process_iter(["pid", "name", "exe"] + ([] if platform == "win32" else ["uids"])))
    table = {p.info["pid"]: (p.info.get("name") or "", p.info.get("exe") or "") for p in procs}
    out = []
    for p in procs:
        if only is not None and p.info["pid"] not in only:
            continue
        pid, n, exe = p.info["pid"], (p.info.get("name") or ""), (p.info.get("exe") or "")
        uids = p.info.get("uids")
        uid = (getattr(uids, "real", None) if not isinstance(uids, (list, tuple)) else uids[0]) if uids else None
        if pid in keep or pid <= (4 if platform == "win32" else 2):
            continue
        if is_system(n, exe, uid, platform):
            if talking_system is not None and socks.get(pid, 0):
                talking_system.append({"pid": pid, "name": n, "exe": exe, "net": socks[pid]})
            continue
        if n.lower().startswith("msedgewebview2"):
            # WebView2 is a renderer other apps embed (WhatsApp, Windows Search, possibly the agent's own app): never
            # judged by its name, only by its HOST, the first ancestor that is not WebView2 (owner 2026-10-18: "be
            # careful with WebView2"; measured: 7 under WhatsApp.Root.exe, 6 under Windows' SearchHost.exe)
            host = next((h for h in _ancestors(psutil, pid, table) if not (h[1] or "").lower().startswith("msedgewebview2")), None)
            # a host that cannot be read (a SYSTEM process seen from a normal shell) is spared: unknown is not noisy
            if (host is None or host[0] in keep or not (host[1] or host[2])
                    or is_system(host[1], host[2], None, platform)):
                continue
            if not (any(w in (host[1] or "").lower().replace(" ", "") for w in wanted) or socks.get(pid, 0)):
                continue
            out.append({"pid": pid, "name": n, "exe": exe, "net": socks.get(pid, 0),
                        "why": f"WebView2 of {host[1]} {host[0]}"})
            continue
        listed = any(w in n.lower().replace(" ", "") for w in wanted)
        talks = socks.get(pid, 0) > 0
        if listed or talks:
            out.append({"pid": pid, "name": n, "exe": exe, "net": socks.get(pid, 0),
                        "why": "holds network sockets" if talks else "on the quiet list"})
    return out


def never_stop(psutil, pid, me=None):
    """-> the reason this PID must never be stopped, or None. The last guard, whatever list the PID came from (owner
    2026-10-18: "don't kill yourself ... that netstat is tricky": the agent running the sweep holds internet sockets
    too). This process and its ancestors, an agent process (AGENT_EXES), Windows' own processes."""
    me = me or os.getpid()
    if pid == me:
        return "this process"
    try:
        if pid in {a.pid for a in psutil.Process(me).parents()}:
            return "an ancestor of this process"
    except psutil.Error:
        pass
    try:
        p = psutil.Process(pid)
        exe, name = (p.exe() or "").lower(), (p.name() or "")
    except (psutil.Error, AttributeError):
        return None
    if any(m in exe for m in AGENT_EXES):
        return "an agent (Claude)"
    if is_system(name, exe, None, "win32" if exe[1:3] == ":\\" else None) and exe.startswith(WINDOWS):
        return "Windows itself"
    return None


def stop(rows, psutil=None, say=print):
    if psutil is None:
        import psutil
    done = []
    for r in rows:
        why = never_stop(psutil, r["pid"])
        if why:
            say(f"  REFUSED to stop {r['name']} PID {r['pid']}: {why} (never stopped, whatever list it came from)")
            continue
        try:
            psutil.Process(r["pid"]).kill()
            done.append(r)
            say(f"  stopped {r['name']} PID {r['pid']} ({r['net']} socket(s), {r.get('why', '')}) {r['exe']}")
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as e:
            say(f"  could not stop {r['name']} PID {r['pid']}: {e}")
    return done


def hold(pid=None, every=2, full=30, minutes=None, say=print, psutil=None, sleep=time.sleep, clock=time.time,
         stop_file=None, units=None):
    """Keep the machine quiet while `pid` runs (or for `minutes`, or until `stop_file` exists; at least one).
    Every `every` s only the process IDs are listed (a few ms) and every NEW process is judged at once, so an app that
    respawns is stopped within about `every` seconds; every `full` s a full sweep catches an old process that opened
    a socket since. `pid` and its descendants are spared. -> total stops. (2026-10-18, owner: "they will restart
    again ... shut them down as soon as they are spawned back"; one full sweep measured 0.6 s, too dear every 2 s.)"""
    if psutil is None:
        import psutil
    if pid is None and minutes is None and stop_file is None:
        raise ValueError("hold needs a PID, minutes or a stop file to know when to end")
    also = (int(pid),) if pid else ()
    end = clock() + minutes * 60 if minutes else None
    alive = lambda: ((pid is None or psutil.pid_exists(int(pid))) and (end is None or clock() < end)
                     and not (stop_file and Path(stop_file).exists()))
    import sys as _sys
    units = _sys.platform != "win32" and hasattr(os, "geteuid") and os.geteuid() == 0 if units is None else units
    n, last_full, seen = 0, None, set()
    try:
        while alive():
            now = clock()
            cur = set(psutil.pids())
            if last_full is None or now - last_full >= full:
                talking = []
                n += len(stop(noisy(psutil, also=also, talking_system=talking), psutil, say=say))
                if units:
                    n += len(stop_units(talking, say=say))
                last_full = now
            elif cur - seen:
                n += len(stop(noisy(psutil, also=also, only=cur - seen), psutil, say=say))
            seen = cur
            sleep(every)
    finally:
        if units:
            restore_units(say=say)
    say(f"  quiet: ended; {n} stop(s); the apps start normally again")
    return n


#: Linux system services stopped for the duration of a measuring run and started again after it (owner 2026-10-18:
#: "we can kill them for the duration of the test and then we can bring them back again"). An ALLOWLIST: a talking
#: daemon not named here is only named, never stopped; networking, DNS, systemd, dbus, sshd and the display manager
#: are never on it. Extra units: fieldkit.local.json autopilot.quiet_units.
STOPPABLE_UNITS = {"avahi-daemon", "cups-browsed", "cups", "ModemManager", "geoclue", "packagekit", "fwupd", "snapd",
                   "colord", "whoopsie", "apport", "kerneloops", "ubuntu-advantage", "motd-news", "apt-daily",
                   "apt-daily-upgrade", "unattended-upgrades", "rsyslog-remote", "bluetooth", "minissdpd",
                   "upower-geoip", "tracker-miner-fs"}
UNITS_FILE = Path(os.environ.get("FIELDKIT_STATE") or Path.home() / ".fieldkit") / "quiet-stopped-units.json"


def stoppable_units():
    try:
        from ..core.settings import local_settings
        return STOPPABLE_UNITS | set((local_settings().get("autopilot") or {}).get("quiet_units") or [])
    except Exception:  # noqa: BLE001
        return STOPPABLE_UNITS


def unit_of(pid, proc="/proc"):
    """The systemd system service a process belongs to ('avahi-daemon'), from /proc/PID/cgroup; None for a user
    session process or when it cannot be read."""
    try:
        text = (Path(proc) / str(pid) / "cgroup").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        path = line.split(":", 2)[-1]
        if "/system.slice/" in path and path.endswith(".service"):
            return path.rsplit("/", 1)[-1][:-len(".service")]
    return None


def stop_units(talking, run=None, say=print, proc="/proc", units_file=None):
    """Linux, as root: stop the system services of the talking system processes that are on the allowlist, each
    with its .socket (socket activation would start it again). Recorded in `units_file` BEFORE stopping, so a
    holder that dies still leaves the list to restore. -> [unit] stopped by this call."""
    import json
    import subprocess
    run = run or subprocess.run
    allowed = stoppable_units()
    units_file = Path(units_file or UNITS_FILE)
    before = json.loads(units_file.read_text(encoding="utf-8")) if units_file.is_file() else []
    want = []
    for t in talking:
        u = unit_of(t["pid"], proc)
        if u and u in allowed and u not in before and u not in want:
            want.append(u)
        elif u and u not in allowed:
            say(f"  quiet: system service {u} talks (PID {t['pid']}); not on the stop list, named only")
    if not want:
        return []
    units_file.parent.mkdir(parents=True, exist_ok=True)
    units_file.write_text(json.dumps(before + want, indent=1), encoding="utf-8")
    done = []
    for u in want:
        r = run(["systemctl", "stop", f"{u}.socket", f"{u}.service"], capture_output=True, text=True)
        if r.returncode == 0 or "not loaded" in (r.stderr or ""):
            done.append(u)
            say(f"  quiet: stopped system service {u} (and its socket) until the run ends")
        else:
            say(f"  quiet: could not stop {u}: {(r.stderr or '').strip()[:120]}")
    return done


def restore_units(run=None, say=print, units_file=None):
    """Start again exactly the services stop_units stopped, then forget them. -> [unit] started."""
    import json
    import subprocess
    run = run or subprocess.run
    units_file = Path(units_file or UNITS_FILE)
    if not units_file.is_file():
        return []
    units = json.loads(units_file.read_text(encoding="utf-8"))
    done = []
    for u in units:
        r = run(["systemctl", "start", f"{u}.socket"], capture_output=True, text=True)       # absent socket: fine
        r = run(["systemctl", "start", f"{u}.service"], capture_output=True, text=True)
        if r.returncode == 0:
            done.append(u)
            say(f"  quiet: started system service {u} again")
        else:
            say(f"  quiet: could not start {u} again: {(r.stderr or '').strip()[:120]} - start it by hand")
    if len(done) == len(units):
        units_file.unlink()
    else:
        units_file.write_text(json.dumps([u for u in units if u not in done], indent=1), encoding="utf-8")
    return done


def start_holder(pid, log):
    """Start `fieldkit autopilot quiet --apply --hold PID` as its own process (it outlives nothing: it ends when PID
    ends), its output in `log`. -> the holder's PID."""
    import subprocess
    import sys
    f = open(log, "a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen([sys.executable, "-m", "fieldkit", "autopilot", "quiet", "--apply", "--hold", str(pid)],
                         stdout=f, stderr=subprocess.STDOUT, creationflags=flags)
    return p.pid
