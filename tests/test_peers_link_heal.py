"""fieldkit peers link heal / rescue / keepalive (2026-10-26): the incident replayed offline - fake ping, ARP and
sockets - and every revive path only when declared."""
import errno
import socket
import threading

import pytest

from fieldkit.peers import link, link_heal as H

WIN_PING = """Pinging 10.77.0.2 with 32 bytes of data:
Reply from 10.77.0.2: bytes=32 time<1ms TTL=64
Reply from 10.77.0.2: bytes=32 time<1ms TTL=64
Reply from 10.77.0.2: bytes=32 time=1ms TTL=64

Ping statistics for 10.77.0.2:
    Packets: Sent = 3, Received = 3, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 0ms, Maximum = 1ms, Average = 0ms
"""
WIN_PING_DEAD = """Ping statistics for 10.77.0.2:
    Packets: Sent = 3, Received = 0, Lost = 3 (100% loss),
"""
LINUX_PING = """3 packets transmitted, 3 received, 0% packet loss, time 2003ms
rtt min/avg/max/mdev = 0.310/0.412/0.520/0.085 ms
"""
WIN_ARP = """Interface: 10.77.0.1 --- 0x12
  Internet Address      Physical Address      Type
  10.77.0.2             02-00-5e-10-00-01     dynamic
  10.77.0.3             ff-ff-ff-ff-ff-ff     static
"""


def test_ping_output_of_both_systems_is_read():
    assert H.parse_ping(WIN_PING) == {"sent": 3, "received": 3, "loss": 0, "rtt_ms": 0.0}
    assert H.parse_ping(WIN_PING_DEAD)["received"] == 0 and H.parse_ping(WIN_PING_DEAD)["loss"] == 100
    assert H.parse_ping(LINUX_PING) == {"sent": 3, "received": 3, "loss": 0, "rtt_ms": 0.412}
    assert H.parse_ping("Request timed out.") is None


def test_neighbor_tables_of_both_systems_are_read():
    assert H.parse_neighbor(WIN_ARP, "10.77.0.2") == {"mac": "02:00:5e:10:00:01", "state": "DYNAMIC"}
    assert H.parse_neighbor("10.77.0.2 dev enp3s0 lladdr 02:00:5e:10:00:01 REACHABLE", "10.77.0.2")["state"] == \
        "REACHABLE"
    assert H.parse_neighbor("10.77.0.2 dev enp3s0 FAILED", "10.77.0.2") == {"mac": None, "state": "FAILED"}
    assert H.parse_neighbor("  10.77.0.2   00-00-00-00-00-00   invalid", "10.77.0.2")["state"] == "FAILED"
    assert H.parse_neighbor(WIN_ARP, "10.77.0.9") is None


class _WinErr(OSError):
    def __init__(self, code):
        super().__init__(code, "x")
        self.winerror = code


def test_refused_timeout_and_unreachable_are_told_apart():
    assert H.classify(None) == "OPEN"
    assert H.classify(ConnectionRefusedError()) == "REFUSED"
    assert H.classify(_WinErr(10061)) == "REFUSED"
    assert H.classify(socket.timeout()) == "TIMEOUT"
    assert H.classify(_WinErr(10060)) == "TIMEOUT"
    assert H.classify(OSError(errno.EHOSTUNREACH, "no route")) == "UNREACHABLE"
    assert H.classify(_WinErr(10065)) == "UNREACHABLE"
    assert H.classify(OSError(errno.EPERM, "?")) == "ERROR"

    def refuse(addr, timeout=None, source_address=None):
        raise ConnectionRefusedError(10061, "refused")
    assert H.probe_tcp("10.77.0.2", 47077, connect=refuse)[0] == "REFUSED"


@pytest.fixture
def house(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    link.device_add("win", "10.77.0.1", "boss", "Windows", me=True, via="cable")
    link.device_add("vaio", "10.77.0.2", "worker", "Linux", via="cable")
    (tmp_path / "link.log").write_text("2026-10-26 00:37:02  message from vaio stored\n"
                                       "2026-10-26 13:40:00  serving on 10.77.0.1:47077 for 10.77.0.2 only\n",
                                       encoding="utf-8")
    return tmp_path


def probes(ping=WIN_PING, arp=WIN_ARP, tcp="REFUSED", hello=None, own=True, rescue=None):
    exc = {"REFUSED": _WinErr(10061), "TIMEOUT": socket.timeout(), "UNREACHABLE": _WinErr(10065), "OPEN": None}
    return {"has_address": lambda ip: True, "ping": lambda ip: ping, "neighbor": lambda ip: arp,
            "tcp": lambda ip, port, t, src: (tcp, exc[tcp], 0.01), "own": lambda ip, port: own,
            "hello": lambda name: hello or {"ok": True, "host": "vaio", "os": "Linux"},
            "rescue": lambda name: rescue or {"ok": False, "error": "refused"}}


def test_the_incident_says_host_up_listener_down(house):
    rows, facts = H.ladder("vaio", probes())
    v = {r["rung"]: r["verdict"] for r in rows}
    assert v == {"interface": "OK", "own": "OK", "neighbor": "OK", "ping": "OK", "tcp": "PROBLEM", "hello": "SKIP",
                 "logs": "SKIP"}
    layer, nxt = H.verdict(rows, facts)
    assert "up but its listener is down" in layer and "REFUSED" in layer and "--revive --device vaio" in nxt
    assert facts["last_seen"] == "2026-10-26 00:37:02"         # the serving-on line is ours, not the peer's
    assert facts["neighbor"]["mac"] == "02:00:5e:10:00:01"


@pytest.mark.parametrize("kw,word", [
    ({"tcp": "TIMEOUT"}, "a firewall drops it"),
    ({"tcp": "UNREACHABLE"}, "no route"),
    ({"tcp": "OPEN", "hello": {"ok": False, "error": "stale"}}, "refuses the signed hello"),
    ({"ping": WIN_PING_DEAD, "arp": "", "tcp": "TIMEOUT"}, "down or asleep"),
    ({"ping": WIN_PING_DEAD, "tcp": "TIMEOUT"}, "drops ping"),
    ({"tcp": "OPEN", "own": False}, "own listener"),
    ({"tcp": "OPEN"}, "none"),
])
def test_each_broken_layer_gets_its_own_verdict(house, kw, word):
    layer, _ = H.verdict(*H.ladder("vaio", probes(**kw)))
    assert word in layer


def test_both_ends_deaf_is_said_once(house):
    out = []
    H.report(*H.ladder("vaio", probes(own=False)), say=out.append)
    assert any(x.startswith("ALSO") and "own listener is down" in x for x in out)


def test_the_peer_rescue_door_logs_are_shown(house):
    rs = {"ok": True, "listening": False, "keepalive": [{"item": "linger", "ok": False}]}
    rows, _ = H.ladder("vaio", probes(rescue=rs))
    logs = next(r for r in rows if r["rung"] == "logs")
    assert logs["verdict"] == "OK" and "listening=False" in logs["detail"] and "linger" in logs["detail"]


# ---- revive -------------------------------------------------------------------------------------------------------
class Flip:
    """Probes that turn healthy once the revive action ran."""
    def __init__(self, **broken):
        self.fixed, self.broken = False, broken

    def __call__(self):
        return probes(tcp="OPEN") if self.fixed else probes(**self.broken)


def _revive(house, flip, declared, actions):
    if declared:
        H.declare("vaio", declared, mac="02:00:5e:10:00:01")
    box, out = [], []
    p = {k: (lambda k: lambda *a: flip()[k](*a))(k) for k in probes()}
    code = H.revive("vaio", p, actions, inbox=lambda *a: box.append(a), wait=3, sleep=lambda s: None,
                    say=out.append)
    return code, box, out


def test_nothing_is_tried_when_nothing_is_declared_and_the_owner_gets_the_exact_command(house):
    called = []
    code, box, out = _revive(house, Flip(), [], {p: lambda d, f, p=p: called.append(p) or (True, p) for p in H.REVIVE})
    assert code == 3 and called == []
    title, why, cmd = box[0]
    assert "vaio" in title and "02:00:5e:10:00:01" in why and "no revive path declared" in why
    assert cmd.startswith("on vaio in a terminal: systemctl --user restart fieldkit-link.service")
    assert "NOT revived" in (house / "link.log").read_text(encoding="utf-8")


def test_a_refused_port_is_revived_through_the_rescue_door_never_wol(house):
    flip, called = Flip(), []

    def rescue(d, f):
        called.append("rescue")
        flip.fixed = True
        return True, "restarted"
    acts = {"rescue": rescue, "wol": lambda d, f: called.append("wol") or (True, "x"),
            "ssh": lambda d, f: called.append("ssh") or (False, "x")}
    code, box, out = _revive(house, flip, ["rescue", "wol"], acts)
    assert code == 0 and called == ["rescue"] and box == []
    assert any(x.startswith("REVIVED") for x in out)


def test_a_silent_host_gets_wol_only_and_the_inbox_when_it_stays_silent(house):
    called = []
    acts = {p: (lambda p: lambda d, f: called.append(p) or (True, p))(p) for p in H.REVIVE}
    code, box, _ = _revive(house, Flip(ping=WIN_PING_DEAD, arp="", tcp="TIMEOUT"), ["rescue", "wol"], acts)
    assert code == 3 and called == ["wol"] and "rescue: skipped (host silent)" in box[0][1]


def test_magic_packet_goes_to_the_cable_broadcast_from_the_cable_address():
    sent = {}

    class Sock:
        def __init__(self, *a):
            pass

        def setsockopt(self, *a):
            sent["broadcast"] = a

        def bind(self, a):
            sent["bind"] = a

        def sendto(self, data, a):
            sent["data"], sent["to"] = data, a

        def close(self):
            pass
    H.wake("02-00-5e-10-00-01", "10.77.0.1", sock_factory=Sock)
    assert sent["bind"] == ("10.77.0.1", 0) and sent["to"] == ("10.77.0.3", 9)
    assert sent["data"] == b"\xff" * 6 + bytes.fromhex("02005e100001") * 16
    with pytest.raises(ValueError):
        H.magic_packet("3c:07")


def test_the_ssh_door_is_key_only_one_command_from_the_cable_only():
    line = H.ssh_authorized_line("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIabc owner@win", "10.77.0.1")
    assert line.startswith('restrict,from="10.77.0.1",command="systemctl --user restart fieldkit-link.service" ')
    with pytest.raises(ValueError):
        H.ssh_authorized_line("rm -rf /", "10.77.0.1")
    argv = H.ssh_argv({"ssh_key": "k", "ssh_user": "u", "address": ["10.77.0.2"]}, "10.77.0.1")
    assert "BatchMode=yes" in argv and "PasswordAuthentication=no" in argv and argv[argv.index("-b") + 1] == \
        "10.77.0.1"


def test_declare_records_paths_and_refuses_what_it_cannot_do(house):
    d = H.declare("vaio", ["rescue", "wol"], mac="02:00:5E:10:00:01")
    assert link.device(link.household(), "vaio")["revive"] == ["rescue", "wol"] and d["wol_mac"] == \
        "02:00:5e:10:00:01"
    for bad in ((["telnet"],), (["ssh"],)):
        with pytest.raises(ValueError):
            H.declare("vaio", *bad)
    with pytest.raises(ValueError):
        H.declare("win", ["rescue"])


def test_orchestrate_names_the_broken_layer_before_giving_up(house):
    from fieldkit import orchestrate
    _h, rows = orchestrate.devices(True, call_fn=lambda req, device=None: {"ok": False, "error": "10061"},
                                   why_fn=lambda name: f"heal: {name} listener down")
    assert next(r for r in rows if r["name"] == "vaio")["heal"] == "heal: vaio listener down"


# ---- the rescue door ----------------------------------------------------------------------------------------------
def test_the_rescue_door_answers_two_signed_verbs_only():
    from fieldkit.peers import link_rescue as R
    k = b"k" * 32
    v = link.Verifier(k)
    answers = {"rescue-status": lambda: {"ok": True, "s": 1}, "rescue-restart": lambda: {"ok": True, "r": 1}}
    assert R.handle({"verb": "rescue-status"}, v, answers)["error"] == "unsigned"
    assert R.handle(link.sign({"verb": "job"}, k), v, answers)["error"].startswith("the rescue door answers")
    assert R.handle(link.sign({"verb": "rescue-status"}, k), v, answers) == {"ok": True, "s": 1}
    signed = link.sign({"verb": "rescue-restart"}, k)
    assert R.handle(signed, v, answers)["r"] == 1 and R.handle(signed, v, answers)["error"] == "replayed"


def test_rescue_restart_uses_the_service_manager_first():
    from fieldkit.peers import link_rescue as R
    r = R.restart_answer(manager=lambda: (True, 0, ["systemctl ok"]), fallback=lambda: pytest.fail("old way"))
    assert r == {"ok": True, "lines": ["systemctl ok"]}
    assert R.restart_answer(manager=lambda: (False, 0, []), fallback=lambda: (3, ["no"]))["ok"] is False


def test_the_rescue_door_end_to_end_on_loopback(tmp_path, monkeypatch):
    from test_peers_link import free_port
    from fieldkit.peers import link_rescue as R
    a, b = tmp_path / "win", tmp_path / "vaio"
    pa, pb = free_port(), free_port()
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(a))
    link.device_add("win", "127.0.0.1", "boss", "Windows", port=pa, me=True, via="cable", allow_loopback=True)
    link.device_add("vaio", "127.0.0.1", "worker", "Linux", port=pb, via="cable", allow_loopback=True)
    out, _fp = link.household_export(tmp_path / "household")
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(b))
    link.household_import(out, "vaio")
    ready = threading.Event()
    t = threading.Thread(target=R.serve, kwargs={"seconds": 6, "allow_loopback": True, "ready": lambda a: ready.set(),
                                                 "answers": {"rescue-status": lambda: {"ok": True, "listening": 0},
                                                             "rescue-restart": lambda: {"ok": True}}}, daemon=True)
    t.start()
    assert ready.wait(5)
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(a))
    assert R.call("rescue-status", "vaio", timeout=5) == {"ok": True, "listening": 0}
    t.join(8)
    log = (b / "rescue.log").read_text(encoding="utf-8")
    assert "rescue door on 127.0.0.1" in log and "rescue-status: ok" in log


def test_a_lan_device_has_no_rescue_door(house):
    from fieldkit.peers import link_rescue as R
    link.device_add("mac", "192.168.1.9", "worker", "Darwin", via="lan")
    assert "not on the cable" in R.call("rescue-status", "mac")["error"]
