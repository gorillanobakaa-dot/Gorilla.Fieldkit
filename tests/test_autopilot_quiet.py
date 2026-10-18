"""Quiet machine (2026-10-18): discovered by sockets, not only by name; Windows, the measuring run and the agent spared."""
import os
from types import SimpleNamespace as NS

from fieldkit.autopilot import quiet


class FakePs:
    Error = Exception
    NoSuchProcess = LookupError

    def __init__(self, procs, conns, parent_of):
        self.procs, self.conns, self.parent_of, self.killed = procs, conns, parent_of, []

    def process_iter(self, attrs):
        return [NS(info=p) for p in self.procs]

    def net_connections(self, kind):
        return self.conns

    def pid_exists(self, pid):
        return False

    def Process(self, pid):
        ps = self

        class P:
            def __init__(self, pid):
                self.pid = pid

            def parents(self):
                out, p = [], ps.parent_of.get(self.pid)
                while p:
                    out.append(P(p))
                    p = ps.parent_of.get(p)
                return out

            def children(self, recursive=False):
                kids = [c for c, p in ps.parent_of.items() if p == self.pid]
                out = [P(k) for k in kids]
                if recursive:
                    for k in kids:
                        out += P(k).children(True)
                return out

            def kill(self):
                ps.killed.append(self.pid)
        return P(pid)


def conn(pid, raddr=None, laddr="192.168.1.5", kind=1):
    return NS(pid=pid, laddr=NS(ip=laddr), raddr=NS(ip=raddr) if raddr else None, type=kind)


def test_any_app_talking_is_stopped_and_windows_the_gate_and_the_agent_are_spared():
    me = os.getpid()
    procs = [{"pid": me, "name": "python.exe", "exe": r"C:\py\python.exe"},
             {"pid": 100, "name": "Zoom.exe", "exe": r"C:\Program Files\Zoom\Zoom.exe"},            # talks, not listed
             {"pid": 101, "name": "svchost.exe", "exe": os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\svchost.exe"},
             {"pid": 102, "name": "firefox.exe", "exe": r"C:\t\firefox.exe"},                        # under test
             {"pid": 103, "name": "claude.exe", "exe": r"C:\U\AppData\Roaming\Claude\claude-code\2\claude.exe"},
             {"pid": 104, "name": "Telegram.exe", "exe": r"C:\U\Telegram.exe"},                      # listed, silent
             {"pid": 105, "name": "notepad.exe", "exe": r"C:\Program Files\np.exe"},                 # loopback only
             {"pid": 106, "name": "kdeconnectd.exe", "exe": r"C:\KDE\kdeconnectd.exe"},              # UDP bound
             {"pid": 107, "name": "msedgewebview2.exe", "exe": r"C:\EdgeWebView\msedgewebview2.exe"},  # Windows' (Search)
             {"pid": 108, "name": "msedgewebview2.exe", "exe": r"C:\EdgeWebView\msedgewebview2.exe"}]  # Zoom's
    conns = [conn(100, "1.2.3.4"), conn(101, "4.4.4.4"), conn(102, "8.8.8.8"), conn(103, "9.9.9.9"),
             conn(105, "127.0.0.1"), conn(106, None, "0.0.0.0", kind=2), conn(107, "5.5.5.5"),
             conn(108, "6.6.6.6")]
    ps = FakePs(procs, conns, {102: 900, 107: 101, 108: 100})
    rows = quiet.noisy(ps, wanted=["Telegram"], also=(900,))
    assert sorted(r["pid"] for r in rows) == [100, 104, 106, 108]       # WebView2 judged by its host
    quiet.stop(rows, ps, say=lambda m: None)
    assert sorted(ps.killed) == [100, 104, 106, 108]


def test_a_respawned_app_is_stopped_on_the_next_tick_without_a_full_sweep():
    # 2026-10-18: "they will restart again": every 2 s only NEW processes are judged; a full sweep every 30 s
    me = os.getpid()
    base = [{"pid": me, "name": "python.exe", "exe": "py"}]
    ps = FakePs(list(base), [], {})
    ticks = {"n": 0}
    calls = []
    real_noisy = quiet.noisy

    def pids():
        return [p["pid"] for p in ps.procs]
    ps.pids = pids

    def sleep(s):
        ticks["n"] += 1
        if ticks["n"] == 1:                                   # Telegram respawns after the first full sweep
            ps.procs.append({"pid": 500, "name": "Telegram.exe", "exe": "tg"})

    def noisy(psutil, wanted=None, also=(), only=None, **kw):
        calls.append(only)
        return real_noisy(psutil, wanted=["Telegram"], also=also, only=only)
    clock = iter(range(0, 100, 2))
    old = quiet.noisy
    quiet.noisy = noisy
    try:
        n = quiet.hold(None, every=2, full=30, minutes=0.2, say=lambda m: None, psutil=ps, sleep=sleep,
                       clock=lambda: next(clock))
    finally:
        quiet.noisy = old
    assert 500 in ps.killed and n == 1
    assert calls[0] is None and calls[1] == {500}             # the respawn was judged alone, not by a full sweep


def test_linux_user_apps_are_stopped_and_the_system_and_session_are_spared_and_named():
    # 2026-10-18 (owner: "this of course won't work on linux"): the same socket table, Linux's idea of the system
    me = os.getpid()
    U = lambda uid: NS(real=uid)
    procs = [{"pid": me, "name": "python3", "exe": "/usr/bin/python3", "uids": U(1000)},
             {"pid": 300, "name": "kdeconnectd", "exe": "/usr/lib/x86_64-linux-gnu/libexec/kdeconnectd", "uids": U(1000)},
             {"pid": 301, "name": "telegram-desktop", "exe": "/usr/bin/telegram-desktop", "uids": U(1000)},
             {"pid": 302, "name": "avahi-daemon", "exe": "/usr/sbin/avahi-daemon", "uids": U(108)},       # system, talks
             {"pid": 303, "name": "plasmashell", "exe": "/usr/bin/plasmashell", "uids": U(1000)},         # session
             {"pid": 304, "name": "kworker/0:1", "exe": "", "uids": U(0)},                               # kernel thread
             {"pid": 305, "name": "zoom", "exe": "/opt/zoom/zoom", "uids": U(1000)},                     # talks, unlisted
             {"pid": 306, "name": "claude", "exe": "/srv/agent/.local/share/claude/versions/2/claude", "uids": U(1000)}]
    conns = [conn(302, None, "0.0.0.0", kind=2), conn(303, "1.1.1.1"), conn(305, "2.2.2.2"), conn(306, "3.3.3.3")]
    ps = FakePs(procs, conns, {})
    talking = []
    rows = quiet.noisy(ps, wanted=["telegram"], platform="linux", talking_system=talking)
    assert sorted(r["pid"] for r in rows) == [301, 305]
    assert sorted(t["name"] for t in talking) == ["avahi-daemon", "plasmashell"]   # named, not stopped


def test_a_linux_service_is_stopped_only_from_the_allowlist_and_started_again(tmp_path):
    # owner 2026-10-18: "kill them for the duration of the test and then bring them back again"
    proc = tmp_path / "proc"
    for pid, cg in ((302, "0::/system.slice/avahi-daemon.service"), (310, "0::/system.slice/NetworkManager.service"),
                    (303, "0::/user.slice/user-1000.slice/session-2.scope")):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cgroup").write_text(cg + "\n")
    calls = []
    run = lambda argv, **k: calls.append(argv) or NS(returncode=0, stderr="")
    uf = tmp_path / "units.json"
    talking = [{"pid": 302}, {"pid": 310}, {"pid": 303}]
    assert quiet.stop_units(talking, run=run, say=lambda m: None, proc=proc, units_file=uf) == ["avahi-daemon"]
    assert calls == [["systemctl", "stop", "avahi-daemon.socket", "avahi-daemon.service"]]     # never NetworkManager
    assert uf.is_file()                                                     # recorded before, for a crashed holder
    calls.clear()
    assert quiet.restore_units(run=run, say=lambda m: None, units_file=uf) == ["avahi-daemon"]
    assert ["systemctl", "start", "avahi-daemon.service"] in calls and not uf.exists()
