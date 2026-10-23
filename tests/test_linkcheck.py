"""linkcheck (kits/linkcheck/linkcheck.py): the cable's setup plan, the read-back, the measurements and the verdict.

The Intel I219-V rows are the laptop's own Get-NetAdapterAdvancedProperty answer, recorded 2026-10-22. The server is
exercised for real on 127.0.0.1. Nothing here elevates, changes an address or touches a network card.
"""
import importlib.util
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "kits" / "linkcheck" / "linkcheck.py"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="kits/linkcheck missing")

spec = importlib.util.spec_from_file_location("linkcheck", TOOL)
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

INTEL_PROPS = [
    {"DisplayName": "Jumbo Packet", "DisplayValue": "1514", "RegistryKeyword": "*JumboPacket"},
    {"DisplayName": "Energy Efficient Ethernet", "DisplayValue": "On", "RegistryKeyword": "EEELinkAdvertisement"},
    {"DisplayName": "Reduce Speed On Power Down", "DisplayValue": "Enabled", "RegistryKeyword": "ReduceSpeedOnPowerDown"},
]
CABLE_NOW = {"iface": "Ethernet", "status": "Up", "speed_mbps": 1000, "duplex": "full", "eee": "on", "mtu": 1500,
             "addresses": [{"ip": "169.254.87.61", "prefix": 16, "origin": "wellknown"}], "dhcp": True,
             "gateways": [], "dns": []}
CABLE_SET = dict(CABLE_NOW, dhcp=False, addresses=[{"ip": "10.77.0.1", "prefix": 30, "origin": "manual"}])


# ---- reading the card ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [("1 Gbps", 1000), ("100 Mbps", 100), ("2.5 Gbps", 2500), ("1000", 1000),
                                       ("1000Mb/s", 1000), ("-1", None), ("Unknown!", None), (None, None)])
def test_speed(text, want):
    assert L.speed_mbps(text) == want


def test_ethtool():
    t = "Settings for enp0s25:\n\tSpeed: 1000Mb/s\n\tDuplex: Full\n\tLink detected: yes\n"
    assert L.parse_ethtool(t) == {"speed_mbps": 1000, "duplex": "full", "link": True}
    assert L.parse_ethtool("\tSpeed: Unknown!\n\tDuplex: Unknown! (255)\n")["duplex"] is None


@pytest.mark.parametrize("line,want", [("EEE status: enabled - active", "on"), ("EEE status: enabled - inactive", "enabled-inactive"),
                                       ("EEE status: disabled", "off"), ("EEE status: not supported", "unsupported"), ("", "unknown")])
def test_ethtool_eee(line, want):
    assert L.parse_ethtool_eee("EEE Settings for enp0s25:\n\t" + line + "\n") == want


def test_windows_eee():
    assert L.parse_win_eee(INTEL_PROPS) == "on"
    assert L.parse_win_eee([{"DisplayName": "Energy-Efficient Ethernet", "DisplayValue": "Disabled", "RegistryKeyword": "*EEE"}]) == "off"
    assert L.parse_win_eee(INTEL_PROPS[:1]) == "unsupported"


# ---- setup -------------------------------------------------------------------------------------------------------------------
def test_cidr_is_private_ipv4_only():
    assert L.parse_cidr("10.77.0.1/30") == ("10.77.0.1", 30)
    for bad in ("8.8.8.8/30", "fd00::1/64", "10.77.0.1/33"):
        with pytest.raises(ValueError):
            L.parse_cidr(bad)


def test_windows_plan_from_apipa():
    lines = L.plan_windows_setup(CABLE_NOW, "10.77.0.1", 30)
    joined = "\n".join(lines)
    assert "-Dhcp Disabled" in joined
    assert "New-NetIPAddress -InterfaceAlias 'Ethernet' -IPAddress '10.77.0.1' -PrefixLength 30" in joined
    assert "DefaultGateway" not in joined                         # never a gateway on the cable
    assert "RegisterThisConnectionsAddress $false" in joined


def test_windows_plan_idempotent_and_undo():
    assert L.plan_windows_setup(CABLE_SET, "10.77.0.1", 30) == []
    assert L.plan_windows_setup(CABLE_NOW, "10.77.0.1", 30, undo=True) == []
    undo = "\n".join(L.plan_windows_setup(CABLE_SET, "10.77.0.1", 30, undo=True))
    assert "Remove-NetIPAddress" in undo and "-Dhcp Enabled" in undo


def test_windows_plan_removes_gateway_dns_and_wrong_prefix():
    c = dict(CABLE_SET, addresses=[{"ip": "10.77.0.1", "prefix": 24, "origin": "manual"}], gateways=["10.77.0.2"], dns=["10.77.0.2"])
    j = "\n".join(L.plan_windows_setup(c, "10.77.0.1", 30))
    assert "Remove-NetIPAddress -InterfaceAlias 'Ethernet' -IPAddress '10.77.0.1'" in j
    assert "Remove-NetRoute" in j and "-ResetServerAddresses" in j


def test_script_fails_closed():
    s = L.setup_script("Ethernet", ["New-NetIPAddress x"])
    assert s.startswith("$ErrorActionPreference = 'Stop'")
    assert "LINKCHECK: SET Ethernet" in s and "LINKCHECK: FAILED" in s


def test_problems_read_back():
    assert L.setup_problems(CABLE_SET, "10.77.0.1", 30) == []
    assert L.setup_problems(dict(CABLE_SET, gateways=["10.77.0.2"]), "10.77.0.1", 30)
    assert L.setup_problems(dict(CABLE_SET, dns=["1.1.1.1"]), "10.77.0.1", 30)
    assert L.setup_problems(CABLE_NOW, "10.77.0.1", 30)


def _win(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")


def test_setup_windows_ok(monkeypatch, capsys):
    _win(monkeypatch)
    states = iter([CABLE_NOW, CABLE_SET])
    seen = []
    rc = L.setup("10.77.0.1/30", "Ethernet", read=lambda i: next(states),
                 elevated=lambda s: (seen.append(s), (0, "LINKCHECK: SET Ethernet\nELEVATE: script finished"))[1],
                 sleep=lambda s: None)
    assert rc == 0 and "VERDICT  SET" in capsys.readouterr().out and len(seen) == 1


def test_setup_windows_nothing_to_do(monkeypatch, capsys):
    _win(monkeypatch)
    rc = L.setup("10.77.0.1/30", "Ethernet", read=lambda i: CABLE_SET, elevated=lambda s: pytest.fail("elevated"))
    assert rc == 0 and "NOTHING TO DO" in capsys.readouterr().out


def test_setup_windows_marker_missing_fails(monkeypatch, capsys):
    _win(monkeypatch)
    states = iter([CABLE_NOW, CABLE_SET])                         # even if the card then looks right
    rc = L.setup("10.77.0.1/30", "Ethernet", read=lambda i: next(states),
                 elevated=lambda s: (0, "LINKCHECK: FAILED Ethernet: access denied"), sleep=lambda s: None)
    assert rc == 3 and "admin step did not finish" in capsys.readouterr().out


def test_run_elevated_closes_its_window(monkeypatch, tmp_path):
    kit = tmp_path / "elevate.py"
    kit.write_text("")
    monkeypatch.setattr(L, "ELEVATE", str(kit))
    monkeypatch.setattr(L, "RESULTS", str(tmp_path / "results"))
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    L.run_elevated("Write-Host x\n", run=run)
    start = next(c for c in calls if "run" in c)
    assert "--close" in start and start[start.index("run") + 1].endswith(".ps1")


def test_setup_windows_read_back_fails(monkeypatch, capsys):
    _win(monkeypatch)
    rc = L.setup("10.77.0.1/30", "Ethernet", read=lambda i: CABLE_NOW,
                 elevated=lambda s: (0, "LINKCHECK: SET Ethernet"), sleep=lambda s: None)
    assert rc == 3 and "read back" in capsys.readouterr().out


def test_setup_refuses_public(capsys):
    assert L.setup("8.8.8.8/30", "Ethernet", read=lambda i: pytest.fail("read")) == 2


class FakeRun:
    def __init__(self, fail_first=False, profiles=""):
        self.calls, self.fail_first, self.profiles = [], fail_first, profiles

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:3] == ["nmcli", "-t", "-f"]:
            return subprocess.CompletedProcess(cmd, 0, self.profiles, "")
        if self.fail_first and cmd[0] != "pkexec":
            return subprocess.CompletedProcess(cmd, 4, "", "Insufficient privileges")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_setup_linux(monkeypatch, capsys):
    monkeypatch.setattr(L.sys, "platform", "linux")
    monkeypatch.setattr(L.shutil, "which", lambda n: "/usr/bin/" + n)
    run = FakeRun(fail_first=True)
    lin_now = dict(CABLE_NOW, iface="enp0s25", addresses=[])
    lin_set = dict(CABLE_SET, iface="enp0s25", addresses=[{"ip": "10.77.0.2", "prefix": 30, "origin": "manual"}])
    states = iter([lin_now, lin_set])
    rc = L.setup("10.77.0.2/30", "enp0s25", read=lambda i: next(states), run=run, sleep=lambda s: None)
    assert rc == 0, capsys.readouterr().out
    add = next(c for c in run.calls if c[:3] == ["nmcli", "connection", "add"])
    assert add[add.index("ipv4.never-default") + 1] == "yes" and add[add.index("ipv4.method") + 1] == "manual"
    assert any(c[0] == "pkexec" for c in run.calls)              # root asked once, only when refused


def test_setup_linux_undo_leaves_foreign_address(monkeypatch, capsys):
    monkeypatch.setattr(L.sys, "platform", "linux")
    lin = dict(CABLE_SET, iface="enp0s25", addresses=[{"ip": "10.77.0.2", "prefix": 30, "origin": "manual"}])
    run = FakeRun(profiles="Wired connection 1\n")
    assert L.setup("10.77.0.2/30", "enp0s25", undo=True, read=lambda i: lin, run=run) == 3
    assert not any(c[:3] == ["nmcli", "connection", "delete"] for c in run.calls)


# ---- serve and the measurements, for real on 127.0.0.1 -----------------------------------------------------------------
def test_bind_refuses_every_network():
    assert L.check_bind("0.0.0.0")
    assert L.check_bind("eth0")
    assert L.check_bind("10.77.0.2") is None


@pytest.fixture
def server():
    s = L.Server("127.0.0.1", 0, allow="127.0.0.1", idle=60, say=lambda *a: None)
    t = threading.Thread(target=s.serve, daemon=True)
    t.start()
    yield s
    s.stop.set()
    t.join(5)


def test_echo_and_throughput(server):
    r = L.rtt("127.0.0.1", server.port, 50, gap=0)
    assert r["received"] == 50 and r["loss_pct"] == 0 and r["p50_ms"] < 50
    for op in ("up", "down"):
        t = L.throughput("127.0.0.1", op, server.port, 0.5)
        assert t["bytes"] > 0 and t["mbit_s"] > 0 and t["op"] == op


def test_echo_same_size_only(server):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.sendto(b"x" * 64, ("127.0.0.1", server.port))
    with pytest.raises(socket.timeout):
        s.recv(2048)                                               # a wrong-sized packet gets nothing back
    s.sendto(struct.pack(L.ECHO_FMT, 7, 9), ("127.0.0.1", server.port))
    assert struct.unpack(L.ECHO_FMT, s.recv(64)) == (7, 9)
    s.close()


def test_server_answers_only_the_allowed_peer():
    s = L.Server("127.0.0.1", 0, allow="127.0.0.2", idle=60, say=lambda *a: None)
    t = threading.Thread(target=s.serve, daemon=True)
    t.start()
    try:
        assert L.rtt("127.0.0.1", s.port, 3, timeout=0.2, gap=0)["received"] == 0
        c = socket.create_connection(("127.0.0.1", s.port), timeout=2)
        c.settimeout(2)
        assert c.recv(10) == b""                                   # closed at once
        c.close()
    finally:
        s.stop.set()
        t.join(5)


def test_idle_stop():
    s = L.Server("127.0.0.1", 0, idle=0.3, say=lambda *a: None)
    t0 = time.monotonic()
    assert s.serve() == 0 and time.monotonic() - t0 < 5


def test_rtt_stats():
    st = L.rtt_stats([1.0, 2.0, 1.0, 2.0], 5)
    assert st["loss_pct"] == 20.0 and st["p50_ms"] == 1.5 and st["p99_ms"] == 2.0 and st["jitter_ms"] == 1.0
    assert L.rtt_stats([], 10)["loss_pct"] == 100.0


def test_ping_needs_a_real_reply():
    fake = lambda out, rc=0: (lambda cmd, **k: subprocess.CompletedProcess(cmd, rc, out, ""))
    assert L.ping_ok("10.77.0.2", 1472, run=fake("Reply from 10.77.0.2: bytes=1472 time<1ms TTL=64"))
    assert not L.ping_ok("10.77.0.2", 1472, run=fake("Packet needs to be fragmented but DF set."))
    assert not L.ping_ok("10.77.0.2", 1472, run=fake("Reply from 10.77.0.1: Destination host unreachable."))


def test_path_mtu():
    assert L.path_mtu("p", 1500, probe=lambda p, n: True)["mtu"] == 1500
    assert L.path_mtu("p", 9000, probe=lambda p, n: n <= 1472)["mtu"] == 1500
    assert L.path_mtu("p", 1500, probe=lambda p, n: False)["mtu"] is None


# ---- the verdict ---------------------------------------------------------------------------------------------------------
GOOD = {"card": dict(CABLE_SET), "rtt": L.rtt_stats([0.2] * 99 + [0.4], 100),
        "up": {"mbit_s": 941.0}, "down": {"mbit_s": 938.0}, "mtu": {"mtu": 1500}}


def test_decide_ok():
    rc, line, nxt = L.decide(GOOD)
    assert rc == 0 and line.startswith("LINK: 941.0 Mbit/s up, 938.0 Mbit/s down, RTT p50 0.2 ms")
    assert "EEE on" in line and "EEE" in nxt


@pytest.mark.parametrize("change,word", [
    ({"error": "cannot reach"}, "FAIL"),
    ({"rtt": L.rtt_stats([], 100)}, "FAIL no echo"),
    ({"card": dict(CABLE_SET, duplex="half")}, "FAIL half duplex"),
    ({"rtt": L.rtt_stats([0.2] * 95, 100)}, "FAIL 5.0 %"),
    ({"up": {"mbit_s": 600.0}}, "LINK: SLOW"),
    ({"card": dict(CABLE_SET, status="Disconnected")}, "FAIL Ethernet is Disconnected"),
])
def test_decide_fails(change, word):
    rc, line, _ = L.decide(dict(GOOD, **change))
    assert rc == 3 and word in line


def test_decide_wifi_is_not_judged_as_a_cable():
    wifi = dict(GOOD, card=dict(CABLE_SET, iface="Wi-Fi", wireless=True, duplex="half", speed_mbps=2400, eee="unsupported"),
                up={"mbit_s": 300.0}, down={"mbit_s": 400.0})
    assert L.decide(wifi)[0] == 0


def test_cli_usage():
    assert L.main(["setup"]) == 2
    assert L.main(["serve", "--bind", "0.0.0.0"]) == 2
    assert L.main(["check"]) == 2
