"""fieldkit peers link find: where is the other computer, on facts recorded 2026-10-22 (nothing on this machine is
touched; the probe is tested against a real listener on loopback)."""
import socket
import threading

from fieldkit.peers import link_find as lf

SILENT = {"received": 0, "cable": [], "lan": [], "gw": "192.168.1.1", "scope": 17}
LATER = {"received": 12, "scope": 17,
         # a STALE all-zero entry, as Windows kept on Ethernet for 192.168.1.1 after the cable moved (2026-10-22)
         "cable": [{"ip": "10.77.0.2", "mac": "00-00-00-00-00-00", "state": "Stale"},
                   {"ip": "fe80::f927:8b4d:cb1e:6443", "mac": "3C-07-71-A2-9F-D0", "state": "Reachable"},
                   {"ip": "fe80::8a33:74ff:fe7f:e4f0", "mac": "00-00-00-00-00-00", "state": "Unreachable"}],
         "lan": [{"ip": "192.168.1.197", "mac": "A4-17-31-EE-A2-ED", "state": "Stale"},
                 {"ip": "192.168.1.167", "mac": "DA-E3-C1-0D-DB-67", "state": "Reachable"},
                 {"ip": "192.168.1.1", "mac": "88-33-74-7F-E4-F0", "state": "Reachable"},
                 {"ip": "224.0.0.22", "mac": "01-00-5E-00-00-16", "state": "Permanent"}],
         "gw": "192.168.1.1"}


def refused(addr, timeout=4):
    raise ConnectionRefusedError("refused")


def test_a_silent_cable_says_the_other_side_is_not_using_its_port():
    code, v, nxt = lf.decide(lf.facts("Ethernet", raw=SILENT))
    assert code == 2 and v.startswith("CABLE SILENT") and "setup-linux.sh" in nxt[0]


def test_the_cable_peer_and_the_home_devices_are_named_by_maker_never_guessed():
    f = lf.facts("Ethernet", raw=LATER, connect=refused)
    assert f["cable_peer"] == "fe80::f927:8b4d:cb1e:6443"          # the all-zero entry is not a peer
    assert [e["ip"] for e in f["lan"]] == ["192.168.1.197", "192.168.1.167"]   # gateway and multicast left out
    assert "Sony" in lf.maker("3C-07-71-A2-9F-D0") and "Foxconn" in lf.maker("a4:17:31:ee:a2:ed")
    assert lf.maker("DA-E3-C1-0D-DB-67").startswith("randomized") and lf.maker("00:11:22:33:44:55") == "unknown maker"
    assert not lf.randomized("3c:07:71:a2:9f:d0")


def test_the_cable_peer_is_probed_by_itself_and_closed_doors_say_what_is_left(monkeypatch):
    seen = []
    monkeypatch.setattr(lf, "probe", lambda h, p, timeout=4, connect=None: seen.append((h, p)) or
                        {"error_kind": "ConnectionRefusedError", "error": "refused"})
    f = lf.facts("Ethernet", probes=["192.168.1.197"], raw=LATER)
    assert seen[0] == ("fe80::f927:8b4d:cb1e:6443%17", 22) and ("192.168.1.197", 47077) in seen
    code, v, nxt = lf.decide(f)
    assert code == 2 and "refused: a live host with nothing listening" in v
    assert any("runs no SSH server" in n for n in nxt) and any("Remote Control" in n for n in nxt)


def test_nothing_beyond_the_cable_is_probed_unless_named(monkeypatch):
    seen = []
    monkeypatch.setattr(lf, "probe", lambda h, p, timeout=4, connect=None: seen.append(h) or {"banner": "x"})
    lf.facts("Ethernet", raw=LATER)
    assert seen == ["fe80::f927:8b4d:cb1e:6443%17"] * 2                  # never a sweep of the home network


def test_every_roadblock_is_explained_in_plain_words_in_order(monkeypatch):
    assert lf.explain(lf.facts("Ethernet", raw=SILENT))[0][0].startswith("The other computer is not using the cable")
    monkeypatch.setattr(lf, "probe", lambda h, p, timeout=4, connect=None: {"error_kind": "ConnectionRefusedError"})
    steps = lf.explain(lf.facts("Ethernet", probes=["192.168.1.197"], raw=LATER))
    whats = [w for w, _ in steps]
    assert whats[0].startswith("The other computer does not accept remote logins")
    assert any("cannot see the Claude session on the other computer" in w for w in whats)
    assert all("->" not in d and d for _, d in steps)                   # each one has a thing to do
    assert "{fp}" in lf.ASK_THE_OTHER_CLAUDE and "setup-linux.sh" in lf.ASK_THE_OTHER_CLAUDE
    monkeypatch.setattr(lf, "probe", lambda h, p, timeout=4, connect=None: {"banner": "SSH-2.0-x"})
    assert lf.explain(lf.facts("Ethernet", raw=LATER)) == []             # a door open: nothing in the way


def test_probe_reads_an_ssh_banner_and_tells_refused_from_timed_out():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def answer():
        c, _ = srv.accept()
        c.sendall(b"SSH-2.0-OpenSSH_9.2p1 Debian-2\r\n")
        c.close()
    threading.Thread(target=answer, daemon=True).start()
    r = lf.probe("127.0.0.1", 22, connect=lambda addr, timeout=4: socket.create_connection(("127.0.0.1", port), timeout))
    srv.close()
    assert lf.classify_probe(r).startswith("OPEN (SSH-2.0-OpenSSH_9.2p1 Debian")
    assert lf.classify_probe({"error_kind": "ConnectionRefusedError"}).startswith("refused")
    assert lf.classify_probe({"error_kind": "TimeoutError"}).startswith("timed out")
