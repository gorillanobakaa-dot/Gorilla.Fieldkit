# Version: 1.0.0 · updated 26-10-22-16-50
"""fieldkit mac: an old Mac is found by its mDNS name, else by its hardware address, else after a ping sweep (all three
proven on the owner's MacBook Air 2011 on 2026-10-22); the ssh alias is replaced with the old file kept; nothing
private lives in the code."""
import json
import os
import stat

import pytest

from fieldkit.mac import macssh as M

MAC = {"user": "user1", "key": "~/.ssh/id_test", "mdns": "Old-Mac.local", "hw_addr": "aa:bb:cc:dd:ee:ff",
       "host_key_alias": "old-mac.local", "note": "an old Mac"}
NEIGH = "192.168.7.20 dev wlan0 lladdr 11:22:33:44:55:66 REACHABLE\n"
NEIGH_MAC = NEIGH + "192.168.7.51 dev wlan0 lladdr aa:bb:cc:dd:ee:ff STALE\n"


class Net:
    """A fake network: answers avahi-resolve, ip neigh and ip addr; the Mac enters the neighbour table only after a
    ping reaches it, as a sleeping device does."""

    def __init__(self, mdns_ok, in_table):
        self.mdns_ok, self.in_table, self.calls = mdns_ok, in_table, []

    def __call__(self, argv):
        self.calls.append(argv[0])
        if argv[0] == "avahi-resolve":
            return (0, "Old-Mac.local\t192.168.7.51") if self.mdns_ok else (1, "Failed to resolve host name")
        if argv[:3] == ["ip", "-4", "neigh"]:
            return 0, NEIGH_MAC if self.in_table else NEIGH
        if argv[:3] == ["ip", "-4", "-o"]:
            return 0, "3: wlan0    inet 192.168.7.197/24 brd 192.168.7.255 scope global dynamic wlan0"
        return 1, ""

    def ping(self, ip):
        if str(ip) == "192.168.7.51":
            self.in_table = True
        return 0, ""


def test_found_by_mdns_first():
    net = Net(mdns_ok=True, in_table=False)
    assert M.find(MAC, runner=net, ping=net.ping) == ("192.168.7.51", "mDNS name Old-Mac.local")
    assert net.calls == ["avahi-resolve"]


def test_found_by_hardware_address_when_mdns_is_silent():
    net = Net(mdns_ok=False, in_table=True)
    ip, how = M.find(MAC, runner=net, ping=net.ping)
    assert ip == "192.168.7.51" and "neighbour table" in how and "waking" not in how


def test_found_after_a_sweep_when_it_was_asleep():
    net = Net(mdns_ok=False, in_table=False)
    ip, how = M.find(MAC, runner=net, ping=net.ping)
    assert ip == "192.168.7.51" and "after waking 254 addresses" in how


def test_not_found_says_why():
    net = Net(mdns_ok=False, in_table=False)
    ip, how = M.find(MAC, runner=net, ping=lambda ip: (1, ""))
    assert ip is None and "did not answer among 254 addresses" in how
    ip, how = M.find({**MAC, "hw_addr": ""}, runner=net)
    assert ip is None and "no hardware address is recorded" in how


def test_ssh_argv_pins_the_host_key_and_the_key():
    argv = M.ssh_argv(MAC, "192.168.7.51", "sw_vers")
    assert "BatchMode=yes" in argv and "HostKeyAlias=old-mac.local" in argv and "IdentitiesOnly=yes" in argv
    assert argv[-2:] == ["user1@192.168.7.51", "sw_vers"]
    assert "BatchMode=yes" not in M.ssh_argv(MAC, "1.2.3.4", batch=False)        # run: the person can answer


SSH = """# keep me
Host other
    HostName 10.0.0.1

# old comment for the mac
Host macair
    HostName Old-Mac.lan
    User user1

Host last
    HostName 10.0.0.2
"""


def test_fix_alias_replaces_only_its_block_and_keeps_the_old_file(tmp_path):
    cfg, trash = tmp_path / "config", tmp_path / "trash"
    cfg.write_text(SSH)
    kept = M.fix_alias("macair", MAC, "Old-Mac.local", ssh_config=cfg, trash=trash)
    new = cfg.read_text()
    assert kept.read_text() == SSH
    assert "Old-Mac.lan" not in new and "old comment for the mac" not in new
    assert new.count("Host macair") == 1 and "HostKeyAlias old-mac.local" in new
    assert "Host other\n    HostName 10.0.0.1" in new and "Host last\n    HostName 10.0.0.2" in new
    assert new.startswith("# keep me")
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600


def test_fix_alias_adds_a_block_when_there_is_none(tmp_path):
    cfg = tmp_path / "config"
    assert M.fix_alias("macair", MAC, "Old-Mac.local", ssh_config=cfg, trash=tmp_path / "t") is None
    assert "Host macair\n    HostName Old-Mac.local" in cfg.read_text()


def test_missing_registry_is_refused_not_invented(tmp_path):
    with pytest.raises(SystemExit) as e:
        M.load(tmp_path / "macs.json")
    assert "REFUSED" in str(e.value) and not (tmp_path / "macs.json").exists()


def test_registry_round_trip_is_private_by_place(tmp_path):
    p = tmp_path / "local" / "macs.json"
    M.save({"macair": MAC}, p)
    assert M.load(p) == {"macair": MAC} and "never published" in json.loads(p.read_text())["_comment"]
    assert M.CONFIG.parent.name == "local"                                      # local/ is git-ignored


def test_the_code_holds_no_private_address():
    src = open(M.__file__).read()
    import re
    found = set(re.findall(r"\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", src.lower()))
    assert found <= {"aa:bb:cc:dd:ee:ff"}                       # only the placeholder in a message, no real one
    assert "192.168.1." not in src and "id_ed25519_macair" not in src
