"""Power (2026-10-10): no compile on battery; a desktop without a battery may; a standby says lid and charge."""
import datetime

from fieldkit.buildh import power, stops


def _ps(out):
    return lambda args, **k: type("R", (), {"stdout": out, "returncode": 0})()


def test_battery_refuses_mains_and_desktops_pass_and_silence_fails_closed():
    st = power.status(run=_ps('{"line":0,"percent":0.19,"flag":0}'))
    assert st == {"mains": False, "percent": 19, "battery": True} and not power.row(st)["ok"]
    assert power.row(power.status(run=_ps('{"line":1,"percent":0.8,"flag":8}')))["ok"]
    assert power.row(power.status(run=_ps('{"line":255,"percent":2.55,"flag":128}')))["ok"]       # no battery
    assert not power.row(power.status(run=_ps("not json")))["ok"]


def test_a_standby_says_lid_and_charge_and_still_counts_as_the_machine():
    ev = stops.power_events(datetime.datetime(2026, 10, 9), run=_ps(
        "2026-10-10 00:02:05|506|false|12900|50340\n2026-10-10 06:20:59|507|||\n"))
    assert ev[0][1] == "standby (lid closed), battery 26 %"
    rs = [{"log": "x", "cmd": "build-run", "start": datetime.datetime(2026, 10, 9, 23, 50),
           "end": datetime.datetime(2026, 10, 10, 6, 21), "exit": None, "passed": False, "why": ""}]
    got = stops.classify(rs, [], ev, lambda a, b, c: {"tree": [], "harness": [], "check": []},
                         now=datetime.datetime(2026, 10, 10, 9, 0))
    assert got[0]["verdict"] == "machine" and "lid closed" in got[0]["evidence"]
