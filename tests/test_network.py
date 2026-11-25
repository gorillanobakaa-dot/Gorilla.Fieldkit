"""fieldkit network: one human CLI for Wi-Fi, routes, router, LAN and the offline watchdog (Windows, Linux, macOS).

Every test runs on every system: parsers are fed captured text, commands are faked, nothing touches a real radio.
"""
import json

import pytest

from fieldkit import cli as topcli
from fieldkit.network import cli as N
from fieldkit.network import diagnose as D
from fieldkit.network import radios as R
from fieldkit.watchdog import install as wdinstall
from fieldkit.watchdog import net as wdnet
import fieldkit.watchdog as watchdog


# -- the help a person reads -------------------------------------------------------------------------------------
def test_help_lists_every_command_with_an_example():
    for cmd in ("fieldkit show", "fieldkit diagnose", "fieldkit connect to", "fieldkit network scan",
                "fieldkit network radios", "fieldkit network routes", "fieldkit network watchdog resume"):
        assert cmd in N.HELP_EXAMPLES, cmd
    assert "ALL ALIASES AT A GLANCE" in N.HELP_EXAMPLES


@pytest.mark.parametrize("word", ["help", "examples", "cheatsheet", "guide", "?"])
def test_every_help_word_prints_the_examples(word, capsys):
    assert N.main([word]) == 0
    assert "fieldkit connect to" in capsys.readouterr().out


# -- every alias resolves to its command ---------------------------------------------------------------------------
ALIASES = {
    "show": ["status", "info", "overview", "check"],
    "diagnose": ["diag", "why", "troubleshoot", "doctor"],
    "connect": ["join", "rejoin", "reconnect", "ensure"],
    "scan": ["list", "nearby", "air"],
    "radios": ["cards", "adapters", "card", "hardware"],
    "profiles": ["saved", "known"],
    "routes": ["route", "interfaces", "ifaces"],
    "router": ["gateway", "hub"],
    "devices": ["lan", "neighbours", "neighbors"],
    "events": ["logs", "history"],
    "watchdog": ["guard", "keep-online"],
}


@pytest.mark.parametrize("canon,alias", [(c, a) for c, al in ALIASES.items() for a in al])
def test_each_alias_parses(canon, alias):
    a = N.build_parser().parse_args([alias])
    assert a.action == alias


def test_every_alias_in_the_help_is_a_real_parser_choice():
    sub = next(x for x in N.build_parser()._actions if x.dest == "action")
    for canon, al in ALIASES.items():
        for name in [canon] + al:
            assert name in sub.choices, name


# -- connect / diagnose take natural words, hostile names stay whole ------------------------------------------------
@pytest.mark.parametrize("argv,want", [
    (["connect", "to", "MyHomeWiFi"], "MyHomeWiFi"),
    (["join", ":("], ":("),
    (["connect", "to", ":)", ":)", "_|_"], ":) :) _|_"),
    (["connect", "to"], "to"),
])
def test_connect_strips_only_the_filler_word(monkeypatch, argv, want):
    seen = {}
    monkeypatch.setattr(R, "join_saved", lambda **k: seen.update(k) or {"exit": 0, "verdict": "ok", "next": "-"})
    assert N.main(argv) == 0
    assert seen["ssid"] == want


def test_connect_with_no_name_joins_the_strongest_saved(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "join_saved", lambda **k: seen.update(k) or {"exit": 0, "verdict": "ok", "next": "-"})
    N.main(["connect"])
    assert seen["ssid"] is None and seen["fix_auto"] is True


def test_diagnose_passes_the_ssid_and_fix_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(D, "diagnose", lambda **k: seen.update(k) or {"exit": 3, "verdict": "x", "findings": [], "next": []})
    monkeypatch.setattr(D, "format_diagnosis", lambda d: ["x"])
    assert N.main(["why", "network", ":(", "--fix-auto"]) == 3
    assert seen["ssid"] == ":(" and seen["fix_auto"] is True


# -- the top-level shortcuts on `fieldkit` itself ------------------------------------------------------------------
@pytest.mark.parametrize("argv,kind", [
    (["show", "radios"], "show"), (["diagnose", "X"], "diagnose"), (["diag"], "diagnose"),
    (["connect", "to", "X"], "connect"), (["join", "X"], "connect"), (["reconnect"], "connect"),
])
def test_top_level_shortcuts_route_to_the_network_tool(monkeypatch, argv, kind):
    seen = []
    monkeypatch.setattr(N, "shortcut_main", lambda k, a, prog: seen.append((k, a, prog)) or 0)
    assert topcli.main(argv) == 0
    assert seen == [(kind, argv[1:], f"fieldkit {argv[0]}")]


@pytest.mark.parametrize("word", ["network", "wlan"])
def test_network_and_wlan_route_to_the_network_cli(monkeypatch, word):
    seen = []
    monkeypatch.setattr(N, "main", lambda a, prog: seen.append((a, prog)) or 0)
    assert topcli.main([word, "help"]) == 0
    assert seen == [(["help"], f"fieldkit {word}")]


def test_an_unknown_command_does_not_dump_every_verb(capsys):
    assert topcli.main(["netwrk"]) == 2
    err = capsys.readouterr().err
    assert "{host,where" not in err and "choose from" not in err
    assert "network" in err and "fieldkit --help" in err


def test_shortcut_connect_to(monkeypatch):
    seen = {}
    monkeypatch.setattr(R, "join_saved", lambda **k: seen.update(k) or {"exit": 0, "verdict": "ok", "next": "-"})
    assert N.shortcut_main("connect", ["to", "Office-5G"], prog="fieldkit connect") == 0
    assert seen["ssid"] == "Office-5G"


# -- parsers fed captured OS text ----------------------------------------------------------------------------------
def test_nmcli_colon_escapes_keep_the_ssid_whole():
    assert R.nmcli_fields(r"\:(:wlan0:yes") == [":(", "wlan0", "yes"]
    assert R.nmcli_fields(r"a\\b:c") == ["a\\b", "c"]


NETSH = """\
SSID 1 : MyHomeWiFi
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
    BSSID 1                 : AA:BB:CC:00:00:01
         Signal             : 81%
         Radio type         : 802.11ax
         Band               : 5 GHz
         Channel            : 100
         Bss Load:
             Connected Stations:        3
             Channel Utilization:       40 (15 %)
    BSSID 2                 : AA:BB:CC:00:00:02
         Signal             : 60%
         Radio type         : 802.11n
         Band               : 2.4 GHz
         Channel            : 13
"""


def test_netsh_networks_flags_dfs_and_uk_channels_and_ignores_bss_load():
    nets = R.parse_netsh_networks(NETSH)
    assert [n["channel"] for n in nets] == [100, 13]
    assert nets[0]["signal"] == 81 and nets[0]["dfs"] is True
    assert nets[1]["uk_eu_ch"] is True and nets[1]["auth"] == "WPA2-Personal"


def test_macos_hardware_ports_find_the_wifi_device():
    txt = "Hardware Port: Ethernet\nDevice: en1\n\nHardware Port: Wi-Fi\nDevice: en0\nEthernet Address: aa:bb:cc:dd:ee:ff\n"
    ports = R.macos_wifi_ports(txt)
    assert [p["device"] for p in ports] == ["en0"]


def test_choose_saved_profile_picks_the_strongest_saved_in_range():
    vis = [{"ssid": "A", "signal": 40}, {"ssid": "B", "signal": 90}, {"ssid": "Stranger", "signal": 99}]
    assert R.choose_saved_profile(["A", "B"], vis)[0] == "B"
    assert R.choose_saved_profile(["A", "B"], vis, exclude={"B"})[0] == "A"
    assert R.choose_saved_profile(["A"], vis, want="Z")[0] is None


# -- the offline diagnosis -----------------------------------------------------------------------------------------
def _radio(**k):
    base = dict(name="Wi-Fi", os_label="x", status="Disconnected")
    base.update(k)
    return R.Radio(**base)


def test_all_radios_off_is_said_first():
    d = D.analyse_ssid_events("X", [], {"saved": True}, [], [_radio(status="Disabled")])
    assert "RADIO IS OFF" in d["verdict"] and d["next"] == ["fieldkit network on"]


def test_connected_then_reset_is_an_upstream_internet_fault(monkeypatch):
    monkeypatch.setattr(R, "probe_internet_quick", lambda timeout=0: False)
    ev = [{"log": "WLAN", "id": 8001, "ssid": "X", "time": "2026-01-01T10:00:00"},
          {"log": "NCSI", "id": 4003, "time": "2026-01-01T10:00:30", "msg": "limited connectivity detected"}]
    d = D.analyse_ssid_events("X", ev, {"saved": True, "autoconnect": True}, [{"ssid": "X", "signal": 70, "band": "5 GHz",
                                                                               "channel": 36}], [_radio()])
    assert "UPSTREAM INTERNET FAULT" in d["verdict"]


def test_association_failure_and_manual_profile(monkeypatch):
    ev = [{"log": "WLAN", "id": 8002, "ssid": "X", "time": "t", "reason": "connection timed out"}]
    d = D.analyse_ssid_events("X", ev, {"saved": True, "autoconnect": False, "connection_mode": "Connect manually"},
                              [{"ssid": "X", "signal": 50, "band": "5 GHz", "channel": 100, "dfs": True}], [_radio()])
    assert "ASSOCIATION" in d["verdict"]
    assert any("--fix-auto" in s for s in d["next"]) and any("DFS" in s for s in d["next"])


# -- watchdog: remembered network, manual switch, fallback, macOS --------------------------------------------------
@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(wdnet, "state_dir", lambda platform=None: tmp_path)
    return tmp_path


def test_the_last_network_survives_a_reboot(state):
    assert wdnet.read_remembered_ssid() == ""
    wdnet.write_remembered_ssid(":(")
    assert (state / "last-ssid.txt").exists() and wdnet.read_remembered_ssid() == ":("


def test_a_manual_switch_has_a_grace_window(state):
    assert not wdnet.manual_switch_active()
    wdnet.mark_manual_switch(60)
    assert wdnet.manual_switch_active()
    assert not wdnet.manual_switch_active(now=10 ** 12)


def test_resume_clears_the_pause(state, monkeypatch):
    monkeypatch.setattr(watchdog, "state_dir", lambda platform=None: state)
    watchdog.set_pause(10)
    assert watchdog.paused()
    watchdog.clear_pause()
    assert not watchdog.paused()


def test_fallback_picks_the_strongest_saved_network(monkeypatch):
    monkeypatch.setattr(R, "list_profiles", lambda sysname=None, with_details=True: [{"ssid": "Home"}, {"ssid": "Phone"}])
    monkeypatch.setattr(R, "scan_networks", lambda radio_name=None, rescan_now=True, sysname=None: {
        "networks": [{"ssid": "Home", "signal": 30}, {"ssid": "Phone", "signal": 80}, {"ssid": "Other", "signal": 99}]})
    assert wdnet.fallback_saved_ssid(platform="linux") == "Phone"
    assert wdnet.fallback_saved_ssid(exclude=("Phone",), platform="linux") == "Home"


def test_macos_reconnect_uses_networksetup():
    calls = []

    def fake(args):
        calls.append(args)
        return "Hardware Port: Wi-Fi\nDevice: en0\n" if args[1] == "-listallhardwareports" else ""
    wdnet.reconnect("MyHomeWiFi", platform="darwin", runner=fake)
    assert ["networksetup", "-setairportpower", "en0", "on"] in calls
    assert ["networksetup", "-setairportnetwork", "en0", "MyHomeWiFi"] in calls


def test_macos_current_ssid_reads_ipconfig_summary():
    def fake(args):
        if args[1] == "-listallhardwareports":
            return "Hardware Port: Wi-Fi\nDevice: en0\n"
        if args[:2] == ["ipconfig", "getsummary"]:
            return "  SSID : Office-5G\n"
        return ""
    assert wdnet.current_ssid(platform="darwin", runner=fake) == "Office-5G"
    assert wdnet.tools_present(platform="darwin", which=lambda t: None) == ["networksetup"]


def test_windows_install_plan_is_still_hands_off():
    assert wdinstall.plan(platform="win32")["status"] == "not-this-platform"


def test_macos_install_plan_is_a_launch_agent():
    p = wdinstall.plan(platform="darwin")
    assert p["platform"] == "darwin" and "LaunchAgents" in p["steps"][0]["detail"]


def test_windows_event_hook_script_names_the_wlan_events(tmp_path):
    r = wdinstall.windows_event_hook_scripts(tmp_path)
    body = (tmp_path / "register-wlan-offline-hooks.ps1").read_text(encoding="utf-8")
    assert "EventID=8002" in body and "WLAN-AutoConfig/Operational" in body and "fieldkit diagnose" in body
    assert r["ok"] and r["script"]


def test_the_cli_watchdog_resume_and_windows_task(state, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(watchdog, "state_dir", lambda platform=None: state)
    assert topcli.main(["watchdog", "resume", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is False
    assert N.main(["watchdog", "windows-task", "--out", str(tmp_path / "o"), "--json"]) == 0
    assert (tmp_path / "o" / "register-wlan-offline-hooks.ps1").exists()


# -- LAN sweep finds this machine's subnet on all three systems ----------------------------------------------------
@pytest.mark.parametrize("sysname,text,want", [
    ("Linux", "    inet 192.168.1.20/24 brd 192.168.1.255 scope global wlan0\n    inet 127.0.0.1/8 scope host lo\n",
     {"192.168.1"}),
    ("Windows", "   IPv4 Address. . . . . . . . . . . : 10.0.0.15(Preferred)\n"
                "   Autoconfiguration IPv4 Address. . : 169.254.3.4\n", {"10.0.0"}),
    ("Darwin", "\tinet 127.0.0.1 netmask 0xff000000\n\tinet 172.20.10.3 netmask 0xfffffff0 broadcast 172.20.10.15\n",
     {"172.20.10"}),
])
def test_local_subnets_on_each_system(monkeypatch, sysname, text, want):
    from fieldkit.wifi import channelsurvey as cs
    monkeypatch.setattr(cs.platform, "system", lambda: sysname)
    monkeypatch.setattr(cs, "_run", lambda *a, **k: (0, text))
    assert cs._local_ipv4_subnets() == want
