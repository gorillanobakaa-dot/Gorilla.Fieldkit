"""P20 fieldkit power investigate: each suspect rule with the numbers of 2026-11-09, and the case that flips it."""
import json
import time

from fieldkit.power import investigate as iv

T1 = time.mktime((2026, 11, 8, 7, 0, 0, 0, 0, -1))
T2 = time.mktime((2026, 11, 9, 16, 4, 0, 0, 0, -1))
UNKNOWN = "unexpected cut (cause unknown)"


def cut(t):
    return {"t": t, "t_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "class": UNKNOWN}


def jrows(pairs):
    return [(t - 60, c, ld) for t, c, ld in pairs]


def win(c1=29.0, l1=5.0, c2=79.0, l2=21.0):
    return iv.thermal_windows([cut(T1), cut(T2)], jrows([(T1, c1, l1), (T2, c2, l2)]))


BAT = iv.parse_battery("<Battery><DesignCapacity>57000</DesignCapacity><FullChargeCapacity>49070</FullChargeCapacity>"
                       "<CycleCount>237</CycleCount></Battery>")


def evidence(**kw):
    ev = {"unknown": [cut(T1), cut(T2)], "thermal": win(), "resets": [], "battery": BAT, "daemons": [], "changes": [],
          "fan": {"service": True, "state": "Stopped", "start": "Manual", "history": []}, "machine": {}}
    ev.update(kw)
    return ev


def by(rows, name):
    return next(r for r in rows if r["suspect"].startswith(name))


def test_thermal_window_takes_the_last_three_minutes_only():
    w = iv.thermal_windows([cut(T2)], [(T2 - 400, 95.0, 99.0), (T2 - 60, 79.0, 21.0)])
    assert (w[0]["peak_c"], w[0]["peak_load"]) == (79.0, 21.0)


def test_cool_or_light_cut_is_not_heat_not_load():
    heat, load = iv.rule_heat_load(win())                    # 29 C and 79 C / 21 % load
    assert heat["verdict"] == load["verdict"] == "ruled out"
    assert "not heat:" in heat["evidence"][-1] and "not load:" in load["evidence"][-1]


def test_hot_and_busy_cut_is_a_heat_suspect():
    rows = iv.rule_heat_load(win(c2=92.0, l2=95.0))
    assert [r["verdict"] for r in rows] == ["suspect", "suspect"]
    assert [r["verdict"] for r in iv.rule_heat_load(win(c2=71.0, l2=60.0))] == ["unproven", "unproven"]   # warm, busy, not proven


def test_fan_state_comes_from_the_journal_mode_column():
    modes = [(T1 - 30, "no-status"), (T2 - 30, "5")]
    assert iv.fan_from_journal(modes, T1) == "stopped" and iv.fan_from_journal(modes, T2) == "running"
    assert iv.fan_from_journal(modes, T2 + 9999) is None
    w = iv.thermal_windows([cut(T2)], [], modes=modes)
    assert iv.rule_fan({"service": True, "state": "Stopped", "start": "Manual"}, w)["verdict"] == "suspect"


def test_battery_86_percent_is_not_the_battery_and_60_percent_is():
    assert BAT["health_pct"] == 86.1 and BAT["cycles"] == 237
    assert iv.rule_battery(BAT)["verdict"] == "ruled out"
    assert iv.rule_battery(dict(BAT, full_mwh=34000, health_pct=59.6))["verdict"] == "suspect"


def test_battery_report_without_capacity_is_no_data():
    assert iv.parse_battery("<x/>") == {}
    assert iv.rule_battery({})["verdict"] == "no data"


STORM = [{"day": "2026-11-09", "process": "ipf_uf.exe", "n": 5}, {"day": "2026-11-09", "process": "WUDFHost.exe", "n": 4},
         {"day": "2026-11-09", "process": "powercfg.exe", "n": 3}]
SVC = [{"name": "DTT", "display": "Intel(R) Dynamic Tuning Technology", "state": "Running", "start": "Automatic"}]


def test_reset_storm_is_a_vendor_daemon_suspect_and_a_quiet_day_is_not():
    assert iv.rule_daemons(SVC, STORM)["verdict"] == "suspect"
    assert iv.rule_daemons(SVC, STORM[:1])["verdict"] == "ruled out"
    assert iv.rule_daemons(SVC, [{"day": "d", "process": "other.exe", "n": 50}])["verdict"] == "ruled out"


def test_driver_install_since_first_cut_is_a_suspect_app_updates_are_not():
    app = {"t": T1 - 86400, "kind": "WU19", "name": "Update for Claude desktop app", "detail": ""}
    sb = {"t": T1 - 3600, "kind": "7045", "name": "CodexSandbox", "detail": "user mode service C:\\x.exe"}
    si = {"t": T1 - 3600, "kind": "WU19", "name": "Security Intelligence Update for Defender driver", "detail": ""}
    assert iv.rule_changes([app, sb, si], T1)["verdict"] == "ruled out"
    drv = {"t": T1 - 3600, "kind": "7045", "name": "newdrv", "detail": "kernel mode driver C:\\d.sys"}
    assert iv.rule_changes([app, sb, drv], T1)["verdict"] == "suspect"
    old = dict(drv, t=T1 - 20 * 86400)
    assert iv.rule_changes([old], T1)["verdict"] == "ruled out"       # before first cut minus 7 days


def test_fan_control_running_at_a_cut_is_a_suspect_stopped_is_not():
    cuts = win()
    hist = [{"t": T1 - 99999, "state": "running"}, {"t": T2 - 99, "state": "stopped"}]
    r = iv.rule_fan({"service": True, "state": "Stopped", "start": "Manual", "history": hist}, cuts)
    assert r["verdict"] == "suspect" and "1 of 2" in " ".join(r["evidence"])   # running at the first cut
    run_now = iv.rule_fan({"service": True, "state": "Running", "start": "Auto", "history": []}, cuts)
    assert run_now["verdict"] == "suspect" and any("undo" in s for s in run_now["steps"])
    hist2 = [{"t": T1 - 99999, "state": "stopped"}]
    assert iv.rule_fan({"service": True, "state": "Stopped", "start": "Manual", "history": hist2}, cuts)["verdict"] == "ruled out"
    assert iv.rule_fan({"service": True, "state": "Stopped", "start": "Manual",
                        "history": [{"t": T1 - 5, "state": "running"}]}, cuts)["verdict"] == "suspect"


def test_hardware_is_first_when_nothing_else_is_suspect():
    rows = iv.rank(evidence())
    assert rows[0]["suspect"].startswith("hardware") and rows[0]["verdict"] == "suspect" and rows[0]["weight"] == 90
    assert any("F10" in s for s in rows[0]["steps"])


def test_hardware_drops_behind_a_stronger_suspect():
    rows = iv.rank(evidence(battery=dict(BAT, health_pct=50.0)))
    assert rows[0]["suspect"] == "battery" and by(rows, "hardware")["weight"] < rows[0]["weight"]


def test_no_unknown_cut_means_no_hardware_suspect_and_exit_0():
    ev = evidence(unknown=[], thermal=[])
    assert by(iv.rank(ev), "hardware")["verdict"] == "no data"
    assert iv.run(ev=ev, out=lambda *_: None) == 0


def test_other_platform_rows_say_not_this_platform():
    ev = evidence(battery=None, daemons=None, changes=None, fan=None)
    rows = iv.rank(ev)
    for n in ("battery", "vendor", "recent", "fan"):
        assert by(rows, n)["verdict"] == iv.NOT_THIS


def test_json_and_exit_3_for_todays_evidence():
    out = []
    assert iv.run(as_json=True, out=out.append, ev=evidence()) == 3
    j = json.loads(out[0])
    assert j["unknown_cuts"] and j["suspects"][0]["suspect"].startswith("hardware")
    assert any("F10" in s for s in j["owner_steps"])
