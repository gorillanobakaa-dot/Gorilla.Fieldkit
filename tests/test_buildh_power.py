"""Power (2026-10-10): no compile on battery; a desktop without a battery may; a standby says lid and charge."""
import datetime

from fieldkit.buildh import power, stops


def _ps(out):
    return lambda args, **k: type("R", (), {"stdout": out, "returncode": 0})()


def test_battery_is_said_never_refused():
    # the owner, 2026-10-10: "the gate must work even when the laptop is on battery but let the user know"
    st = power.status(run=_ps('{"line":0,"percent":0.19,"flag":0}'), platform="win32")
    assert st == {"mains": False, "percent": 19, "battery": True}
    r = power.row(st)
    assert r["ok"] and r["on_battery"] and "ON BATTERY" in r["evidence"] and "herald" in r["evidence"]
    assert not power.row(power.status(run=_ps('{"line":1,"percent":0.8,"flag":8}'), platform="win32"))["on_battery"]
    assert "no battery" in power.row(power.status(run=_ps('{"line":255,"percent":2.55,"flag":128}'), platform="win32"))["evidence"]
    assert power.row(power.status(run=_ps("not json"), platform="win32"))["ok"]


def test_a_standby_says_lid_and_charge_and_still_counts_as_the_machine():
    ev = stops.power_events(datetime.datetime(2026, 10, 9), run=_ps(
        "2026-10-10 00:02:05|506|false|12900|50340\n2026-10-10 06:20:59|507|||\n"))
    assert ev[0][1] == "standby (lid closed), battery 26 %"
    rs = [{"log": "x", "cmd": "build-run", "start": datetime.datetime(2026, 10, 9, 23, 50),
           "end": datetime.datetime(2026, 10, 10, 6, 21), "exit": None, "passed": False, "why": ""}]
    got = stops.classify(rs, [], ev, lambda a, b, c: {"tree": [], "harness": [], "check": []},
                         now=datetime.datetime(2026, 10, 10, 9, 0))
    assert got[0]["verdict"] == "machine" and "lid closed" in got[0]["evidence"]


def _supply(root, name, **files):
    d = root / name
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).write_text(v + "\n", encoding="ascii")


def test_linux_reads_the_kernels_power_supply(tmp_path):
    _supply(tmp_path, "AC", type="Mains", online="0")
    _supply(tmp_path, "BAT0", type="Battery", present="1", capacity="19", status="Discharging")
    st = power.status(platform="linux", root=tmp_path)
    assert st == {"mains": False, "percent": 19, "battery": True}
    assert power.row(st)["on_battery"] and "battery 19 %" in power.row(st)["evidence"]
    (tmp_path / "AC" / "online").write_text("1\n", encoding="ascii")
    assert power.status(platform="linux", root=tmp_path)["mains"] is True


def test_linux_without_a_battery_is_on_mains_and_unreadable_is_unknown(tmp_path):
    assert power.status(platform="linux", root=tmp_path) == {"mains": True, "percent": None, "battery": False}
    st = power.status(platform="linux", root=tmp_path / "missing")
    assert st["mains"] is None and "did not say" in power.row(st)["evidence"]


def test_this_machine_can_be_asked():
    st = power.status()
    assert set(st) == {"mains", "percent", "battery"} and power.row(st)["ok"]
