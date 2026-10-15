# Version: 1.1.1 · updated 26-10-15-23-26
"""Isolate the machine before a browser test: warn the person, quiet what competes, measure that it is quiet, restore.

Owner, 2026-10-15, after a decode test "failed" because he was using his own browser at the same time and minimised
the test window: "the harness learns to ALERT the users to stay away from the keyboard as tests are underway ... smart
enough to kill all the other processes that keep the ASIC / network card and processor busy so it can isolate /
pinpoint the problem. that's troubleshooting 101." On a real (Windows) computer the GPU, network and CPU are busy
with "tons" of other programs: a measurement taken in that noise proves nothing either way.

What it DOES, in order:
  1. warn(): says it out loud (the herald: Amy, offline), shows a desktop notice and prints it, then counts down
     (DRIVE_COUNTDOWN, the Windows harness's 20 s rule) so the person can step away or say no (Ctrl-C)
  1b. own_work(): the harness's OWN background work, the worst noise because the harness started it (owner,
     2026-10-15, when the first survey caught the pre-commit test suite at 73% CPU: "the harness will also need to
     check for cases like this"): a pre-commit run (.git/pre-commit.running.<pid>), a build in screen, compilers and
     linkers, a test run. Never killed: waited for (wait_own), and a run that is still going is INCONCLUSIVE
  2. survey(): other browsers (any Firefox build, Gorilla, Chrome/Chromium/Brave/Edge/Epiphany), the processes using
     the most CPU, the video engine's load and the network's bytes per second, each measured, never guessed
  2b. net_by_process(): bytes moved per program, from `ss -tpni` taken twice (owner, 2026-10-15, who left Telegram
     open on purpose: "telegram is constantly downloading ... in the background. it was not closed. the harness needs
     to check"). Any program moving data is named, on the list below or not
  3. quiet(): closes the other browsers AND the background-heavy apps (HEAVY: messengers that fetch media, game
     launchers, torrent and sync clients) with SIGTERM, which lets them save their session (Firefox restores the tabs),
     and records each one's command line to start it again afterwards
  4. survey() again: if the video engine, the network or the CPU is still busy, the test is INCONCLUSIVE, and the
     report names what is busy (a test never passes or fails in noise)
  5. restore(): starts the closed browsers again with their own command lines

What it NEVER does (owner's troubleshooting rules, and the estate's):
  - never SIGKILL a person's program; never closes anything that is not a browser (the desktop, the sound server, the
    terminal, the harness, the AI session doing the test stay up); a busy non-browser program is REPORTED, not killed
  - never starts quietly: no warning and countdown, no test
  - never leaves the person without their browser: what it closed it reopens, and says so
"""
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DRIVE_COUNTDOWN = 20                       # the Windows harness's rule for anything that takes the screen or keyboard
BROWSERS = re.compile(r"^(firefox|firefox-bin|firefox-esr|gorilla-unleash\w*|chrome|chromium\w*|brave\w*|msedge|"
                      r"epiphany\w*|librewolf|waterfox|opera)$")
HEAVY = re.compile(r"^(telegram[\w-]*|whatsapp[\w-]*|discord[\w-]*|signal[\w-]*|slack|steam\w*|"
                   r"qbittorrent\w*|transmission\w*|deluge\w*|ktorrent|dropbox\w*|syncthing|nextcloud\w*|"
                   r"megasync|spotify|zoom|teams\w*|skypeforlinux)$", re.I)
NEVER = re.compile(r"^(gnome-shell|mutter|Xwayland|Xorg|pipewire\w*|wireplumber|systemd\w*|dbus\w*|sshd|bash|zsh|"
                   r"claude\w*|node|python3?|screen|tmux|gnome-terminal\w*|kgx|ptyxis)$")


def _comm(pid):
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def _ppid(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def _cmdline(pid):
    try:
        return [x.decode("utf-8", "replace") for x in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if x]
    except OSError:
        return []


def _processes():
    """(name, cmdline()) of every running process: /proc on Linux; psutil where there is no /proc (Windows), with
    the .exe dropped from the name so the same OWN names match. Nothing when neither is available."""
    if Path("/proc").is_dir():
        for p in Path("/proc").iterdir():
            if p.name.isdigit():
                yield _comm(int(p.name)), (lambda pid=int(p.name): _cmdline(pid))
        return
    try:
        import psutil
    except ImportError:
        return
    for p in psutil.process_iter(["name", "cmdline"]):
        n = p.info.get("name") or ""
        n = n[:-4] if n.lower().endswith(".exe") else n
        yield n, (lambda c=p.info.get("cmdline") or []: c)


OWN = re.compile(r"^(cc1plus|cc1|clang\S*|rustc|ld\.lld|ld|lld|mach|sccache|pytest\S*|make|gmake|ninja|cargo|"
                 r"dpkg-deb|xz|build_deb\.sh)$")


def own_work(roots=None):
    """The harness's own background work now -> [reason, ...]. Pre-commit markers under each repo in `roots`
    (default: the Fieldkit checkout), screen sessions, and compiler / test / packaging processes by name."""
    from ..core import proc, settings
    why = []
    for root in roots or [settings.ROOT]:
        for m in sorted(Path(root).glob(".git/pre-commit.running.*")):
            pid = m.name.rsplit(".", 1)[-1]
            if pid.isdigit() and proc.create_time(int(pid)) is not None:     # /proc on Linux, the process table on Windows
                why.append(f"a pre-commit check is running in {root} (pid {pid})")
    try:
        sl = subprocess.run(["screen", "-ls"], capture_output=True, text=True, timeout=5).stdout
        why += [f"a screen session is running: {m}" for m in re.findall(r"^\s+(\d+\.\S+)", sl, re.M)]
    except (OSError, subprocess.TimeoutExpired):
        pass
    names = {}
    for n, cmdline in _processes():
        if OWN.match(n):
            names[n] = names.get(n, 0) + 1
        elif n.startswith("python"):
            if "pytest" in " ".join(cmdline()):
                names["pytest"] = names.get("pytest", 0) + 1
    why += [f"{c} {n} process(es) running (build, test or packaging)" for n, c in sorted(names.items())]
    return why


def wait_own(limit=1800, out=print, sleep=time.sleep, check=own_work):
    """Wait (never kill) until the harness's own work is done. -> [] when quiet, else what is still running."""
    end = time.monotonic() + limit
    why = check()
    told = False
    while why and time.monotonic() < end:
        if not told:
            out("  waiting for the harness's own work to finish first: " + "; ".join(why))
            told = True
        sleep(15)
        why = check()
    return why


def net_by_process(seconds=3, sleep=time.sleep, run=subprocess.run):
    """Bytes per second each program moves over TCP, measured from two `ss -tpni` snapshots `seconds` apart.
    -> [{pid, name, kbps}] busiest first. Connections opened between the snapshots count from zero."""
    def snap():
        out, owner = {}, None
        text = run(["ss", "-tpni"], capture_output=True, text=True).stdout
        for line in text.splitlines():
            if not line.startswith(("\t", " ")) and "users:((" in line:
                m = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
                f = line.split()
                owner = ((f[3], f[4]), m.group(1), int(m.group(2))) if m and len(f) > 4 else None
            elif owner:
                b = sum(int(x) for x in re.findall(r"bytes_(?:received|acked):(\d+)", line))
                out[owner] = b
                owner = None
        return out
    a = snap()
    sleep(seconds)
    b = snap()
    per = {}
    for k, v in b.items():
        _, name, pid = k
        per[(pid, name)] = per.get((pid, name), 0) + max(v - a.get(k, 0), 0)
    rows = [{"pid": pid, "name": name, "kbps": round(v / 1024 / seconds, 1)} for (pid, name), v in per.items()]
    return sorted([r for r in rows if r["kbps"] > 0], key=lambda r: -r["kbps"])


def heavy_apps(exclude=()):
    """Background-heavy apps that are running (top-level processes only) -> [{pid, name, cmdline}]."""
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        pid = int(p.name)
        name = _comm(pid)
        cmd = _cmdline(pid)
        exe = Path(cmd[0]).name if cmd else ""
        if pid in exclude or not (HEAVY.match(name) or HEAVY.match(exe)):
            continue
        if HEAVY.match(_comm(_ppid(pid))) or any(a.startswith("--type=") for a in cmd):
            continue
        out.append({"pid": pid, "name": name or exe, "cmdline": cmd})
    return out


def browsers(exclude=()):
    """Top-level browser processes (not their content/GPU children) -> [{pid, name, cmdline}]."""
    pids = [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]
    out = []
    for pid in pids:
        name = _comm(pid)
        if pid in exclude or not BROWSERS.match(name):
            continue
        if BROWSERS.match(_comm(_ppid(pid))):
            continue                                       # a child of a browser: closing the parent closes it
        cmd = _cmdline(pid)
        if any(a.startswith("-contentproc") or a.startswith("--type=") for a in cmd):
            continue
        out.append({"pid": pid, "name": name, "cmdline": cmd})
    return out


def _cpu_snapshot():
    snap = {}
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                f = (p / "stat").read_text().rsplit(")", 1)[1].split()
                snap[int(p.name)] = int(f[11]) + int(f[12])
            except (OSError, IndexError, ValueError):
                pass
    return snap


def _net_bytes():
    total = 0
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, data = line.split(":", 1)
        if name.strip() != "lo":
            f = data.split()
            total += int(f[0]) + int(f[8])
    return total


def _video_engine(seconds=3):
    from .decode_test import parse_gpu_top
    r = subprocess.run(["sudo", "-n", "timeout", "--signal=INT", str(seconds), "intel_gpu_top", "-J", "-s", "1000"],
                       capture_output=True, text=True)
    v = [x[k] for x in parse_gpu_top(r.stdout) for k in x if k.lower().split("/")[0] == "video"]
    return round(sum(v) / len(v), 2) if v else None


def survey(seconds=3, exclude=()):
    """What competes with a test right now, measured over `seconds`."""
    clk = os.sysconf("SC_CLK_TCK")
    c0, n0, t0 = _cpu_snapshot(), _net_bytes(), time.monotonic()
    video = _video_engine(seconds)
    c1, n1, t1 = _cpu_snapshot(), _net_bytes(), time.monotonic()
    dt = max(t1 - t0, 0.001)
    busy = sorted(((round(100 * (c1[p] - c0.get(p, c1[p])) / clk / dt, 1), p, _comm(p)) for p in c1), reverse=True)
    me = {os.getpid(), os.getppid()}
    top = [{"pid": p, "name": n, "cpu_pct": c} for c, p, n in busy if c >= 10 and p not in me][:8]
    return {"browsers": browsers(exclude), "heavy_apps": heavy_apps(exclude), "video_engine_pct": video,
            "net_kbps": round((n1 - n0) / 1024 / dt, 1), "cpu_hogs": top,
            "net_hogs": [r for r in net_by_process(1) if r["kbps"] >= 16 and r["pid"] not in exclude]}


def noisy(s, video_max=0.3, net_max_kbps=64, cpu_max=50):
    """-> [reason, ...] why the machine is not quiet enough to measure (empty = quiet)."""
    why = []
    if s["browsers"]:
        why.append("other browsers open: " + ", ".join(f"{b['name']} ({b['pid']})" for b in s["browsers"]))
    if s.get("heavy_apps"):
        why.append("background-heavy apps open: " + ", ".join(f"{b['name']} ({b['pid']})" for b in s["heavy_apps"]))
    net_hogs = [h for h in s.get("net_hogs", []) if not NEVER.match(h["name"])]
    if net_hogs:
        why.append("programs moving data: " + ", ".join(f"{h['name']} {h['kbps']} KB/s" for h in net_hogs[:5]))
    if s["video_engine_pct"] is not None and s["video_engine_pct"] >= video_max:
        why.append(f"the video engine is already {s['video_engine_pct']}% busy")
    if s["net_kbps"] >= net_max_kbps:
        why.append(f"the network is moving {s['net_kbps']} KB/s")
    hogs = [h for h in s["cpu_hogs"] if h["cpu_pct"] >= cpu_max]
    if hogs:
        why.append("busy programs: " + ", ".join(f"{h['name']} {h['cpu_pct']}%" for h in hogs))
    return why


def warn(what, seconds=DRIVE_COUNTDOWN, out=print, speak=True, sleep=time.sleep):
    """Out loud, on the desktop and in the terminal, then a countdown. Ctrl-C stops the test."""
    words = (f"Test starting in {seconds} seconds: {what}. Please step away from the keyboard and mouse, "
             "and do not use the browser until I say it is finished. Your browser will be closed and reopened.")
    out("=" * 72)
    out("HANDS OFF: " + words)
    out("Press Ctrl-C now to stop it.")
    out("=" * 72)
    if speak:
        try:
            from ..buildh import herald
            herald.say(words, out=lambda *_: None)
        except Exception:                                  # a missing voice never stops the warning on screen
            pass
    try:
        subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications", "--object-path",
                        "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.Notify",
                        "Fieldkit", "0", "dialog-warning", "Hands off: test starting", words, "[]", "{}", "20000"],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for left in range(seconds, 0, -5):
        out(f"  {left} s")
        sleep(min(5, left))


def quiet(found, out=print, wait=20):
    """SIGTERM each browser (it saves its session), wait for it to end. -> the ones closed, with their command lines."""
    closed = []
    for b in found:
        if NEVER.match(b["name"]):
            continue
        try:
            os.kill(b["pid"], signal.SIGTERM)
        except OSError:
            continue
        end = time.monotonic() + wait
        while time.monotonic() < end and Path(f"/proc/{b['pid']}").exists():
            time.sleep(0.5)
        gone = not Path(f"/proc/{b['pid']}").exists()
        out(f"  closed {b['name']} ({b['pid']}){'' if gone else ': still running after ' + str(wait) + ' s, left alone'}")
        if gone:
            closed.append(b)
    return closed


def restore(closed, out=print):
    for b in closed:
        if not b["cmdline"]:
            continue
        subprocess.Popen(b["cmdline"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        out(f"  reopened {b['name']}: {' '.join(b['cmdline'])[:120]}")


def finished(what, out=print, speak=True):
    words = f"The test is finished: {what}. You can use the computer again."
    out("TEST FINISHED: " + words)
    if speak:
        try:
            from ..buildh import herald
            herald.say(words, out=lambda *_: None)
        except Exception:
            pass
