"""fieldkit peers link setup: one command per computer. The decisions are tested on recorded facts; the join is run
for real between two processes (loopback, the test flag); nothing touches this machine's cards or firewall."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.peers import link, link_setup as ls


# 2026-10-22 on the laptop: Ethernet = the cable (1 Gbps, only 169.254.x), Wi-Fi = the internet.
LAPTOP = [{"name": "Ethernet", "wired": True, "up": True, "addresses": ["169.254.87.61/16"]}]


def test_the_cable_is_the_wired_card_with_a_link_that_does_not_carry_the_internet():
    assert ls.pick_cable(LAPTOP, "Wi-Fi")[0] == "Ethernet"
    # the morning of 2026-10-22: the same card carried the internet; it must never be re-addressed
    name, why = ls.pick_cable(LAPTOP, "Ethernet")
    assert name is None and "wifiradios ensure" in why
    assert ls.pick_cable([{"name": "eth0", "wired": True, "up": False}], "wlan0")[0] is None
    two = [{"name": "eth0", "wired": True, "up": True}, {"name": "eth1", "wired": True, "up": True}]
    name, why = ls.pick_cable(two, "wlan0")
    assert name is None and "--iface" in why


def test_the_windows_admin_script_does_only_what_is_missing_and_fails_closed():
    s = ls.windows_admin_script("Ethernet", "10.77.0.1", "10.77.0.2", True, True)
    assert s.startswith("$ErrorActionPreference = 'Stop'")
    assert "New-NetIPAddress -InterfaceAlias 'Ethernet' -IPAddress 10.77.0.1 -PrefixLength 30" in s
    assert "-DefaultGateway" not in s and "-RemoteAddress 10.77.0.2" in s and "-LocalPort 47077" in s
    assert "PrefixOrigin -ne 'WellKnown'" in s                    # only a manual address is replaced
    only_rule = ls.windows_admin_script("Ethernet", "10.77.0.1", "10.77.0.2", False, True)
    assert "New-NetIPAddress" not in only_rule and "New-NetFirewallRule" in only_rule
    assert "New-NetFirewallRule" not in ls.windows_admin_script("Ethernet", "10.77.0.1", "10.77.0.2", True, False)


def test_linux_address_and_firewall_plans_are_narrow():
    cmds, how = ls.linux_address_plan("enp0s25", "10.77.0.2")
    add = cmds[0]
    assert add[:3] == ["nmcli", "connection", "add"] and "10.77.0.2/30" in add
    assert add[add.index("ipv4.never-default") + 1] == "yes" and "ipv4.gateway" not in add
    cmds, how = ls.linux_address_plan("enp0s25", "10.77.0.2", nmcli=False)
    assert cmds[0] == ["ip", "addr", "add", "10.77.0.2/30", "dev", "enp0s25"] and "NOT kept" in how
    ufw = ls.linux_firewall_plan("ufw", "enp0s25", "10.77.0.2", "10.77.0.1")[0]
    assert ufw == ["ufw", "allow", "in", "on", "enp0s25", "from", "10.77.0.1", "to", "10.77.0.2", "port", "47077",
                   "proto", "tcp"]
    assert "source address=10.77.0.1/32" in ls.linux_firewall_plan("firewalld", "e", "10.77.0.2", "10.77.0.1")[0][2]
    assert ls.linux_firewall_plan("", "e", "x", "y") == []


def test_root_is_asked_once_through_pkexec_only_when_refused():
    seen = []

    class R:
        def __init__(self, rc, err=""):
            self.returncode, self.stdout, self.stderr = rc, "", err

    def run(c, **k):
        seen.append(c)
        return R(1, "Error: Insufficient privileges.") if c[0] == "nmcli" else R(0)
    ok, _ = ls.run_root([["nmcli", "connection", "add"]], run=run)
    assert ok and seen == [["nmcli", "connection", "add"], ["pkexec", "nmcli", "connection", "add"]]


def test_the_usb_kit_holds_code_key_one_script_and_checksums(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "feat/agent-link", str(repo)], check=True)
    (repo / "x").write_text("x")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], check=True)
    pf = tmp_path / "link-pair-host.json"
    pf.write_text("{}")
    files = ls.write_usb_kit(tmp_path / "stick", repo, pf)
    assert files == ["SHA256SUMS", "fieldkit-agent-link.bundle", "link-pair-host.json", "setup-linux.sh"]
    kit = tmp_path / "stick" / "fieldkit-link"
    sh = (kit / "setup-linux.sh").read_bytes()
    assert b"\r\n" not in sh and b"sha256sum -c SHA256SUMS" in sh and b"setup --join" in sh
    assert b"worktree add" in sh                                    # never switches a busy checkout
    sums = (kit / "SHA256SUMS").read_text().splitlines()
    assert len(sums) == 3 and all("  " in s for s in sums)
    if shutil.which("sh"):
        assert subprocess.run(["sh", "-n", str(kit / "setup-linux.sh")]).returncode == 0
    if shutil.which("sha256sum"):
        assert subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=kit, capture_output=True).returncode == 0
    assert subprocess.run(["git", "bundle", "verify", str(kit / "fieldkit-agent-link.bundle")], cwd=repo,
                          capture_output=True).returncode == 0


def test_a_pairing_file_is_found_on_the_stick(tmp_path):
    (tmp_path / "fieldkit-link").mkdir()
    (tmp_path / "fieldkit-link" / "link-pair-win.json").write_text("{}")
    assert [p.name for p in ls.find_pair_file([tmp_path])] == ["link-pair-win.json"]
    code, v, nxt = ls.setup(join=str(tmp_path / "nothing"))
    assert code == 2 and v.startswith("NO PAIRING FILE") and "stick" in nxt


def test_setup_first_skips_what_is_already_done_and_keeps_the_carried_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    link.pair_init("10.77.0.1", "10.77.0.2")
    fp0 = link.fingerprint(link.key())
    called = []
    monkeypatch.setattr(ls, "windows_rule_exists", lambda run=None: True)
    monkeypatch.setattr(ls, "listening", lambda ip, port=0: True)
    monkeypatch.setattr(ls, "WINDOWS", True)
    cards = [{"name": "Ethernet", "wired": True, "up": True, "addresses": ["10.77.0.1/30"]}]
    code, v, nxt = ls.setup(first=True, cards_fn=lambda: (cards, "Wi-Fi"),
                            elevate_fn=lambda s: called.append(s) or (0, ""))
    assert code == 0 and v.startswith("READY") and fp0 in v
    assert called == []                                             # nothing missing: no UAC prompt at all
    assert link.fingerprint(link.key()) == fp0                      # the key on the stick stays valid
    cards[0]["addresses"] = ["169.254.87.61/16"]
    code, v, _ = ls.setup(first=True, cards_fn=lambda: (cards, "Wi-Fi"),
                          elevate_fn=lambda s: called.append(s) or (0, "LINKSETUP: ADDRESS 10.77.0.1"))
    assert code == 0 and len(called) == 1 and "New-NetIPAddress" in called[0] and "New-NetFirewallRule" not in called[0]
    code, v, _ = ls.setup(first=True, cards_fn=lambda: (cards, "Wi-Fi"),
                          elevate_fn=lambda s: (0, "LINKSETUP: FAILED address: access denied"))
    assert code == 3 and v.startswith("ADMIN STEP FAILED")
    code, v, _ = ls.setup(first=True, cards_fn=lambda: (cards, "Ethernet"), elevate_fn=lambda s: 1 / 0)
    assert code == 2 and v.startswith("NO CABLE PORT")              # the internet card is never touched


def test_join_end_to_end_says_hello_to_the_first_computer(tmp_path, monkeypatch):
    """Computer A serves for real (a subprocess); computer B runs setup --join from the 'stick'."""
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    a, b, stick = tmp_path / "A", tmp_path / "B", tmp_path / "stick"
    stick.mkdir()
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(a))
    pf, fp = link.pair_init("127.0.0.1", "127.0.0.1", port=port, out=stick / "link-pair-a.json", allow_loopback=True)
    env = dict(os.environ, FIELDKIT_LINK_HOME=str(a))
    p = subprocess.Popen([sys.executable, "-m", "fieldkit", "peers", "link", "serve", "--seconds", "30",
                          "--loopback-test"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time
        for _ in range(100):
            if (a / "link.log").is_file() and "serving" in (a / "link.log").read_text():
                break
            time.sleep(0.1)
        monkeypatch.setenv("FIELDKIT_LINK_HOME", str(b))
        monkeypatch.setattr(link, "check_address", lambda ip, allow_loopback=False: ip)   # loopback for this test
        monkeypatch.setattr(ls, "listening", lambda ip, port=0: True)
        monkeypatch.setattr(ls, "WINDOWS", False)
        code, v, nxt = ls.setup(join=str(stick), cards_fn=lambda: ([{"name": "eth0", "wired": True, "up": True,
                                                                    "addresses": ["127.0.0.1/8"]}], "wlan0"),
                                firewall_fn=lambda: "", root_fn=lambda c: (True, "ok"))
    finally:
        p.kill()
        p.wait()
    assert code == 0 and v.startswith("LINKED") and fp in v and "approve" in nxt
    assert link.fingerprint(link.key()) == fp
