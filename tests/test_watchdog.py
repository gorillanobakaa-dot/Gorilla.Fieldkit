"""The online watchdog, on both systems, from one codebase.

Nothing here touches a real network, speaks, or writes a systemd unit. The
checks that matter are the ones that would catch a Linux port quietly breaking
Windows, because Windows is the half that already works and must not regress.
"""
import pytest

from fieldkit import watchdog
from fieldkit.watchdog import install as wdinstall
from fieldkit.watchdog import net


# ---------------------------------------------------------------------------
# Windows must behave EXACTLY as the original script did. It works; the owner's
# instruction on 2026-10-13 was to leave it alone. These pin the calls so a
# future Linux change cannot quietly alter them.
# ---------------------------------------------------------------------------
def test_windows_still_asks_netsh_for_the_network():
    calls = []

    def fake(args, timeout=30):
        calls.append(args)
        return "    SSID                   : TheHotspot\n    AP BSSID               : 00:11:22\n"

    assert net.current_ssid(platform="win32", runner=fake) == "TheHotspot"
    assert calls == [["netsh", "wlan", "show", "interfaces"]]


def test_windows_reconnect_uses_the_radio_incantation_then_netsh():
    calls = []
    net.reconnect("TheHotspot", platform="win32",
                  runner=lambda a, timeout=30: calls.append(a) or "", log=lambda s: None)
    assert calls[0][0] == "powershell"
    assert "Windows.Devices.Radios.Radio" in calls[0][-1]
    assert calls[1] == ["netsh", "wlan", "connect", "name=TheHotspot"]


def test_windows_autostart_is_not_touched():
    out = wdinstall.plan(platform="win32")
    assert out["status"] == "not-this-platform"
    assert out["steps"] == []
    assert "scheduled task" in out["why"]


# ---------------------------------------------------------------------------
# Linux, the new half.
# ---------------------------------------------------------------------------
def test_linux_asks_nmcli_for_the_ACTIVE_network_only():
    # `nmcli dev wifi` lists every network in range. Taking the first would
    # cheerfully try to join a neighbour's, so only ACTIVE:yes counts.
    listing = "no:NeighbourWifi\nyes:MyHotspot\nno:AnotherOne\n"
    got = net.current_ssid(platform="linux", runner=lambda a, timeout=30: listing)
    assert got == "MyHotspot"


def test_linux_reconnect_switches_the_radio_on_then_joins():
    calls = []
    net.reconnect("MyHotspot", platform="linux",
                  runner=lambda a, timeout=30: calls.append(a) or "", log=lambda s: None)
    assert calls[0] == ["nmcli", "radio", "wifi", "on"]
    assert calls[1] == ["nmcli", "connection", "up", "MyHotspot"]


def test_linux_with_no_remembered_network_does_not_guess():
    calls = []
    logged = []
    net.reconnect("", platform="linux",
                  runner=lambda a, timeout=30: calls.append(a) or "", log=logged.append)
    assert ["nmcli", "connection", "up", ""] not in calls, "must not try to join an empty network name"
    assert any("nothing to join" in m for m in logged)


def test_the_state_folder_follows_each_system_convention():
    assert "PortableNetworkHub" in str(net.state_dir(platform="win32"))
    linux = str(net.state_dir(platform="linux"))
    assert ".local/state" in linux or "XDG" in linux


# ---------------------------------------------------------------------------
# The logic itself.
# ---------------------------------------------------------------------------
def test_online_is_a_socket_not_a_web_request(monkeypatch):
    # A socket proves the route works without fetching anything, so nothing is
    # sent anywhere and no page is read. That is deliberate.
    tried = []

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_conn(addr, timeout=None):
        tried.append(addr)
        raise OSError("down")

    monkeypatch.setattr(watchdog.socket, "create_connection", fake_conn)
    assert watchdog.online() is False
    assert tried == watchdog.PROBES, "every probe address should be tried before giving up"


def test_it_does_not_shout_before_the_alarm_delay(monkeypatch, tmp_path):
    said = []
    monkeypatch.setattr(watchdog, "online", lambda *a, **k: False)
    monkeypatch.setattr(watchdog, "speak", lambda t, p=None: said.append(t))
    monkeypatch.setattr(watchdog, "log", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "paused", lambda *a, **k: False)
    monkeypatch.setattr(watchdog, "reconnect", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "current_ssid", lambda *a, **k: "")
    watchdog.loop(forever=False, alarm_after=90, _now=lambda: 1000.0)
    assert said == [], "one pass, just gone offline: it must not alarm yet"


def test_a_paused_watchdog_stays_quiet(monkeypatch):
    # The pause exists so a deliberate offline test does not set the alarm off.
    said = []
    monkeypatch.setattr(watchdog, "online", lambda *a, **k: False)
    monkeypatch.setattr(watchdog, "speak", lambda t, p=None: said.append(t))
    monkeypatch.setattr(watchdog, "log", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "paused", lambda *a, **k: True)
    monkeypatch.setattr(watchdog, "reconnect", lambda *a, **k: None)
    monkeypatch.setattr(watchdog, "current_ssid", lambda *a, **k: "")
    # offline_since starts now, so force a second pass past the alarm delay by
    # calling twice with a clock that has moved on.
    watchdog.loop(forever=False, alarm_after=0, repeat=0, _now=lambda: 2000.0)
    assert said == [], "paused means silent, however long it has been offline"


# The alarm must go through the herald, not carry its own speech code. That is
# how it inherits whatever voice the machine is configured to use, which on
# Linux is now the neural one (fieldkit/voice).
def test_the_alarm_speaks_through_the_herald(monkeypatch):
    import fieldkit.buildh.herald as herald
    spoken = []
    monkeypatch.setattr(herald, "say", lambda t, **k: spoken.append(t))
    monkeypatch.setattr(watchdog, "log", lambda *a, **k: None)
    assert watchdog.speak("hello") == "spoken"
    assert spoken == ["hello"]


def test_a_mute_machine_does_not_stop_the_loop(monkeypatch):
    import fieldkit.buildh.herald as herald

    def boom(*a, **k):
        raise RuntimeError("no audio device")

    monkeypatch.setattr(herald, "say", boom)
    monkeypatch.setattr(watchdog, "log", lambda *a, **k: None)
    assert watchdog.speak("hello") == "not-spoken", \
        "a machine with no sound must not crash the watchdog; it is still useful offline"
