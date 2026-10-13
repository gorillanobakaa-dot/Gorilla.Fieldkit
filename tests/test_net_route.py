"""Which interface carries your traffic, and which box you are actually talking to.

Written 2026-10-13 during a muster that found these two modules shipped with no
tests, despite having found two real bugs that day. Both tests here are built
around the failures that actually happened.
"""
import pytest

from fieldkit.net import route, router


TWO_GATEWAYS = """\
default via 192.168.0.1 dev Gorilla.WIFI proto dhcp src 192.168.0.20 metric 600
default via 192.168.1.1 dev enp3s0 proto dhcp src 192.168.1.207 metric 900
192.168.0.0/24 dev Gorilla.WIFI proto kernel scope link src 192.168.0.20 metric 600
"""


def test_every_default_gateway_is_listed_not_just_the_winner(monkeypatch):
    """The real failure: a probe followed the default route, reached the WRONG
    box, and spent twenty minutes identifying the other hub's manufacturer."""
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, TWO_GATEWAYS))
    gw = route.gateways()
    assert len(gw) == 2
    assert [g["gateway"] for g in gw] == ["192.168.0.1", "192.168.1.1"]


def test_the_lowest_metric_is_marked_as_the_winner(monkeypatch):
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, TWO_GATEWAYS))
    gw = route.gateways()
    assert gw[0]["metric"] == 600 and gw[0]["wins"] is True
    assert gw[1]["metric"] == 900 and gw[1]["wins"] is False


def test_the_interface_is_captured_so_you_know_which_cable(monkeypatch):
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, TWO_GATEWAYS))
    assert {g["iface"] for g in route.gateways()} == {"Gorilla.WIFI", "enp3s0"}


def test_more_than_one_router_produces_a_loud_warning(monkeypatch):
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, TWO_GATEWAYS))
    w = route.gateway_warning()
    assert w and "MORE THAN ONE ROUTER" in w
    assert "192.168.1.1" in w and "192.168.0.1" in w
    assert "LOWEST metric wins" in w
    assert "--ip" in w, "the warning must say how to choose"


def test_a_single_router_produces_no_warning(monkeypatch):
    one = "default via 10.0.0.1 dev eth0 proto dhcp metric 100\n"
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, one))
    assert route.gateway_warning() is None


def test_no_default_route_at_all_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, "192.168.0.0/24 dev eth0\n"))
    assert route.gateways() == []
    assert route.gateway_warning() is None


# ---------------------------------------------------- fixed line or cellular
def _ping(rtts):
    return "\n".join(f"64 bytes from x: icmp_seq={i} ttl=55 time={r} ms"
                     for i, r in enumerate(rtts))


def test_a_wandering_latency_is_called_cellular(monkeypatch):
    """Measured on the EE hub: min 13.6, max 31.5, mdev 5.5 ms."""
    monkeypatch.setattr(route.shutil, "which", lambda n: "/bin/ping")
    monkeypatch.setattr(route, "_run",
                        lambda *a, **k: (0, _ping([13.6, 28.1, 15.2, 31.5, 19.0, 24.4])))
    d = route.kind()
    assert d["kind"] == "cellular-or-shared"
    assert "NORMAL, not a fault" in d["why"]


def test_a_steady_latency_is_called_a_fixed_line(monkeypatch):
    monkeypatch.setattr(route.shutil, "which", lambda n: "/bin/ping")
    monkeypatch.setattr(route, "_run",
                        lambda *a, **k: (0, _ping([8.1, 8.3, 8.2, 8.4, 8.2, 8.3])))
    assert route.kind()["kind"] == "fixed-line"


def test_too_few_replies_refuses_rather_than_deciding(monkeypatch):
    monkeypatch.setattr(route.shutil, "which", lambda n: "/bin/ping")
    monkeypatch.setattr(route, "_run", lambda *a, **k: (0, _ping([9.0, 9.1])))
    d = route.kind()
    assert d["kind"] == "unknown" and "not enough replies" in d["why"]


def test_no_ping_installed_says_so(monkeypatch):
    monkeypatch.setattr(route.shutil, "which", lambda n: None)
    assert route.kind()["kind"] == "unknown"


# ------------------------------------------- identifying the box you probe
def test_the_mac_belongs_to_the_box_probed_not_the_default_gateway(monkeypatch):
    """The second real bug: identify() reported the DEFAULT gateway's MAC for
    every --ip, so one hub's manufacturer was attributed to the other."""
    monkeypatch.setattr(router, "gateway",
                        lambda: {"ip": "192.168.0.1", "mac": "48:d3:43:25:62:30",
                                 "oui": "48:D3:43"})
    monkeypatch.setattr(router.subprocess, "run", lambda *a, **k: type(
        "R", (), {"stdout": "192.168.1.1 dev enp3s0 lladdr 88:33:74:7f:e4:f0 REACHABLE"})())
    monkeypatch.setattr(router, "_get", lambda *a, **k: ("", 200))
    d = router.identify("192.168.1.1")
    assert d["mac"] == "88:33:74:7f:e4:f0", "must not be the other box's MAC"
    assert d["oui"] == "88:33:74"


def test_probing_the_default_gateway_still_uses_its_known_mac(monkeypatch):
    monkeypatch.setattr(router, "gateway",
                        lambda: {"ip": "10.0.0.1", "mac": "aa:bb:cc:dd:ee:ff", "oui": "AA:BB:CC"})
    monkeypatch.setattr(router, "_get", lambda *a, **k: ("", 200))
    assert router.identify("10.0.0.1")["mac"] == "aa:bb:cc:dd:ee:ff"


def test_an_unresolvable_mac_is_none_rather_than_wrong(monkeypatch):
    monkeypatch.setattr(router, "gateway",
                        lambda: {"ip": "10.0.0.1", "mac": "aa:bb:cc:dd:ee:ff", "oui": "AA:BB:CC"})
    monkeypatch.setattr(router.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": ""})())
    monkeypatch.setattr(router, "_get", lambda *a, **k: ("", 200))
    d = router.identify("10.0.0.99")
    assert d["mac"] is None, "reporting the wrong device's MAC is worse than none"


def test_a_page_full_of_mobile_words_is_called_cellular(monkeypatch):
    page = ("sim " * 40) + ("signal " * 30) + ("5g " * 20) + ("lte " * 20)
    monkeypatch.setattr(router, "gateway",
                        lambda: {"ip": "1.1.1.1", "mac": None, "oui": None})
    monkeypatch.setattr(router, "_get", lambda *a, **k: (page, 200))
    d = router.identify("1.1.1.1")
    assert d["wan_kind"] == "cellular"
    assert "10x" in d["why"] and "NORMAL" in d["why"]


def test_a_page_without_mobile_words_is_not_claimed_as_cellular(monkeypatch):
    monkeypatch.setattr(router, "gateway",
                        lambda: {"ip": "1.1.1.1", "mac": None, "oui": None})
    monkeypatch.setattr(router, "_get", lambda *a, **k: ("<html>cable modem</html>", 200))
    assert router.identify("1.1.1.1")["wan_kind"] == "probably-fixed"


def test_no_gateway_at_all_is_an_error_not_a_guess(monkeypatch):
    monkeypatch.setattr(router, "gateway", lambda: {"ip": None, "mac": None, "oui": None})
    assert "error" in router.identify(None)


# ------------------------------- the harness is shared with a Windows machine
def test_on_an_unsupported_os_it_refuses_instead_of_reporting_nothing(monkeypatch):
    """`[]` from gateways() reads as 'this machine has no default route', which
    is alarming and false. Silence and success must never look alike."""
    monkeypatch.setattr(route.platform, "system", lambda: "Windows")
    msg = route.unsupported_here()
    assert msg and "Windows" in msg
    assert "nothing was LOOKED AT" in msg
    assert route.gateway_warning() == msg, "the warning must carry the refusal"


def test_on_linux_nothing_is_blocked(monkeypatch):
    monkeypatch.setattr(route.platform, "system", lambda: "Linux")
    assert route.unsupported_here() is None


def test_the_refusal_points_at_what_DOES_work_everywhere(monkeypatch):
    monkeypatch.setattr(route.platform, "system", lambda: "Darwin")
    assert "net antenna" in route.unsupported_here()


def test_the_band_maths_really_is_platform_free(monkeypatch):
    """Claimed in the refusal, so it has to be true."""
    from fieldkit.net import cellular
    monkeypatch.setattr(route.platform, "system", lambda: "Windows")
    d = cellular.antenna_spec(["B3", "B7", "n78"])
    assert d["must_cover_mhz"] == "1805-3800"
