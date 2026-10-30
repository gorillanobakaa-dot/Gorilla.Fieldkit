# Version: 1.0.0 · updated 26-10-28-19-48
"""The household list (fieldkit/peers/household.py, plan item C1): every device by its own path, which are up.
Fake link calls and a fake SSH probe: nothing here touches the cable, the network or a Mac."""
import pytest

from fieldkit.peers import household as hh
from fieldkit.peers import link

PLAN = [{"tag": "[L15-WIN]", "path": "cable"}, {"tag": "[VAIO-LNX]", "path": "cable"},
        {"tag": "[AIR-MAC]", "path": "ssh", "mac": "macair", "never": ["cable"], "from": ["[VAIO-LNX]"]}]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(link, "save_household", lambda h: h)


def house(**l15):
    return {"me": "vaio", "devices": [{"name": "vaio", "tag": "[VAIO-LNX]", "link": "cable"},
                                      dict({"name": "l15", "link": "cable"}, **l15)]}


def hello_ok(req, timeout, device):
    return {"ok": True, "tag": {"l15": "[L15-WIN]", "air": "[AIR-MAC]"}[device], "host": device}


def test_the_shipped_list_names_the_three_devices_and_the_mac_never_on_the_cable():
    rows = {r["tag"]: r for r in hh.load()}
    assert rows["[L15-WIN]"]["path"] == rows["[VAIO-LNX]"]["path"] == "cable"
    assert rows["[AIR-MAC]"]["path"] == "ssh" and "cable" in rows["[AIR-MAC]"]["never"]


@pytest.mark.parametrize("body, why", [("devices: []\n", "no `devices:`"),
                                        ("devices:\n  - {tag: '[X-LNX]', path: pigeon}\n", "not one of"),
                                        ("devices:\n  - {tag: '[X-MAC]', path: ssh}\n", "names its `mac`"),
                                        ("devices:\n  - {tag: '[X-LNX]', path: cable, never: [cable]}\n", "never")])
def test_a_bad_list_is_refused_never_read_as_empty(tmp_path, body, why):
    p = tmp_path / "h.yaml"
    p.write_text(body)
    with pytest.raises(ValueError, match=why):
        hh.load(p)
    with pytest.raises(ValueError, match="missing"):
        hh.load(tmp_path / "none.yaml")


def test_each_device_by_its_own_path_from_the_vaio():
    probed = []
    rows = hh.reach(PLAN, h=house(), me="[VAIO-LNX]", call=hello_ok, macs={"macair": {"user": "u"}},
                    ssh_probe=lambda mac, sweep: probed.append(mac) or (True, "ssh answered"), now=1.0)
    s = {r["tag"]: r["state"] for r in rows}
    assert s == {"[L15-WIN]": "UP", "[VAIO-LNX]": "HERE", "[AIR-MAC]": "UP"} and probed == [{"user": "u"}]
    assert hh.code(rows) == 0


def test_a_silent_cable_device_and_a_mac_that_does_not_answer_are_down():
    rows = hh.reach(PLAN, h=house(tag="[L15-WIN]"), me="[VAIO-LNX]",
                    call=lambda req, timeout, device: {"ok": False, "error": "refused"},
                    macs={"macair": {"user": "u"}}, ssh_probe=lambda mac, sweep: (False, "timed out"))
    s = {r["tag"]: r["state"] for r in rows}
    assert s["[L15-WIN]"] == "DOWN" and s["[AIR-MAC]"] == "DOWN" and hh.code(rows) == 3
    assert "[AIR-MAC] DOWN" in hh.lines(rows)[-2]


def test_from_the_windows_laptop_the_mac_is_not_from_here_and_never_probed():
    h = {"me": "l15", "devices": [{"name": "l15", "tag": "[L15-WIN]", "link": "cable"},
                                  {"name": "vaio", "tag": "[VAIO-LNX]", "link": "cable"}]}
    rows = hh.reach(PLAN, h=h, me="[L15-WIN]", call=lambda req, timeout, device: {"ok": True, "tag": "[VAIO-LNX]"},
                    macs={}, ssh_probe=lambda mac, sweep: pytest.fail("probed from the wrong machine"))
    assert {r["tag"]: r["state"] for r in rows}["[AIR-MAC]"] == "NOT FROM HERE" and hh.code(rows) == 0


def test_the_mac_put_on_the_cable_is_a_wrong_path():
    h = house()
    h["devices"].append({"name": "air", "tag": "[AIR-MAC]", "link": "cable"})
    rows = hh.reach(PLAN, h=h, me="[VAIO-LNX]", call=hello_ok, macs={"macair": {"user": "u"}},
                    ssh_probe=lambda mac, sweep: (True, "ok"))
    assert {r["tag"]: r["state"] for r in rows}["[AIR-MAC]"] == "WRONG PATH" and hh.code(rows) == 3


def test_not_paired_and_no_mac_entry_are_said_not_set_up():
    rows = hh.reach(PLAN, h={}, me="[VAIO-LNX]", macs={}, ssh_probe=lambda mac, sweep: (True, "ok"))
    s = {r["tag"]: r["state"] for r in rows}
    assert s["[L15-WIN]"] == "NOT SET UP" and s["[AIR-MAC]"] == "NOT SET UP"


def test_only_ssh_asks_no_hello_over_the_cable():
    rows = hh.reach(PLAN, h=house(), me="[VAIO-LNX]", call=lambda *a, **k: pytest.fail("hello sent"),
                    macs={"macair": {"user": "u"}}, ssh_probe=lambda mac, sweep: (True, "ok"), only=("ssh",))
    assert [r["tag"] for r in rows] == ["[AIR-MAC]"]
