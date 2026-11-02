"""R9 fieldkit power incidents and R10 power drift: the classifier, the 6008 cut time, the defect rows, the drift check."""
import time

from fieldkit.power import drift, incidents

T0 = time.mktime((2026, 11, 2, 3, 29, 52, 0, 0, -1))


def ev(t, i, msg="", **data):
    return {"t": t, "id": i, "prov": "x", "msg": msg, "data": data}


IBM = r"\Device\HarddiskVolume3\Windows\System32\DriverStore\FileRepository\ibmpmdrv.inf_amd64_x\x64\ibmpmsvc.exe"


def rows(*pairs):
    return [(t, c, ld) for t, c, ld in pairs]


def sleep_187(extra=()):
    return [ev(T0, 187, ApiCallerName=IBM, SystemAction="2"), ev(T0 + 0.1, 566, "Reason InputPoUserPresent"),
            ev(T0 + 3, 42, "Sleep Reason: Application API"), ev(T0 + 50, 506, "Reason: Sleep, Hibernate, or Shutdown."), *extra]


def test_187_names_the_process_and_the_sub_trigger():
    inc = incidents.classify_windows(sleep_187(), rows((T0 - 30, 57.0, 30.0)))
    assert len(inc) == 1                       # 566, 42 and the 506 are the same incident
    assert inc[0]["class"] == "program sleep request (ibmpmsvc.exe)"
    assert inc[0]["sub_trigger"] == "InputPoUserPresent"
    assert inc[0]["peak_c"] == 57.0


def test_6008_cut_is_the_last_journal_line_when_later_than_the_stated_time():
    logged = T0
    stated = logged - 3 * 3600
    msg = "The previous system shutdown at %s on ‎11/‎2/‎2026 was unexpected." % time.strftime("%I:%M:%S %p", time.localtime(stated)).lstrip("0")
    last = stated + 600
    inc = incidents.classify_windows([ev(logged, 6008, msg), ev(logged - 5, 6005)], rows((last - 60, 32.0, 5.0), (last, 32.0, 5.0)))
    assert inc[0]["t"] == last
    assert inc[0]["class"] == "unexpected cut (cause unknown)"
    assert "not heat (peak 32 C)" in inc[0]["class_note"]


def test_6008_with_a_hot_journal_is_heat():
    msg = "The previous system shutdown at 3:00:00 AM on 11/2 was unexpected."
    inc = incidents.classify_windows([ev(T0, 6008, msg)], rows((T0 - 60, 97.0, 90.0), (T0 - 30, 98.0, 90.0)))
    assert inc[0]["class"] == "heat"


def test_6008_after_a_real_scheme_change_is_a_power_plan_reset():
    msg = "The previous system shutdown at %s on 11/2 was unexpected." % time.strftime("%I:%M:%S %p", time.localtime(T0 - 600)).lstrip("0")
    inc = incidents.classify_windows([ev(T0, 6008, msg), ev(T0 - 600 - 10, 12)], rows((T0 - 700, 40.0, 5.0)))
    assert inc[0]["class"] == "power-plan reset"


def test_ordinary_standby_is_not_unknown():
    inc = incidents.classify_windows([ev(T0, 506, "Reason: Idle Timeout")], [])
    assert inc[0]["class"] == "standby (Idle Timeout)"
    assert inc[0]["class"] not in incidents.UNKNOWN_CLASSES


def test_defect_row_once_per_incident(tmp_path):
    p = tmp_path / "ZERO-OPEN-PROBLEMS.md"
    p.write_text("| # | x |\n|---|---|\n| 7 | old | OPEN | y |\n", encoding="utf-8")
    inc = [{"class": "unexpected cut (cause unknown)", "t_text": "2026-10-27 04:13:50", "what": "EventLog 6008", "peak_c": None}]
    assert incidents.defect_rows(inc, path=p) == 1
    assert incidents.defect_rows(inc, path=p) == 0
    body = p.read_text(encoding="utf-8")
    assert body.count("[power-incident 2026-10-27 04:13:50]") == 1 and "| 8 |" in body
    assert incidents.defect_rows([dict(inc[0], **{"class": "heat", "t_text": "2026-10-28 01:00:00"})], path=p) == 0


def test_run_exit_3_on_unknown_and_0_otherwise(monkeypatch, tmp_path):
    bad = {"class": "unknown", "t": T0, "t_text": "x", "what": "w", "peak_c": None}
    good = {"class": "standby (Idle Timeout)", "t": T0, "t_text": "y", "what": "w", "peak_c": 30.0}
    monkeypatch.setattr(incidents, "collect", lambda since: {"incidents": [bad], "scheme_resets": [], "error": None})
    assert incidents.run(since="2026-10-27", write_rows=False, out=lambda *_: None) == 3
    monkeypatch.setattr(incidents, "collect", lambda since: {"incidents": [good], "scheme_resets": [], "error": None})
    assert incidents.run(since="2026-10-27", write_rows=False, out=lambda *_: None) == 0


def test_linux_reader_classes_a_boot_without_a_shutdown_line():
    inc = incidents.classify_linux([{"t": T0, "line": "boot ended without a shutdown line", "clean": False},
                                    {"t": T0 + 1, "line": "boot ended cleanly", "clean": True},
                                    {"t": T0 + 2, "line": "systemd-logind: Lid closed."}], [])
    assert [i["class"] for i in inc] == ["unexpected cut (cause unknown)", "button/lid"]


def test_macos_classes_causes():
    inc = incidents.classify_macos([{"t": T0, "kind": "Sleep", "line": "Entering Sleep state due to 'Clamshell Sleep'"},
                                    {"t": T0, "kind": "Shutdown Cause", "line": "Shutdown Cause: -61"}], [])
    assert [i["class"] for i in inc] == ["button/lid", "unexpected cut (cause unknown)"]


def test_readers_return_nothing_on_a_system_without_them(monkeypatch):
    monkeypatch.setattr(incidents.sys, "platform", "freebsd")
    assert incidents.read_linux(0) == [] and incidents.read_macos(0) == []
    assert incidents.read_windows(0) == {"events": [], "resets": []}


# ---- R10 ------------------------------------------------------------------------------------------------------

def test_drift_fails_on_a_planted_ibmpmsvc_sleep_on_ac_during_work():
    found = drift.sleep_events([ev(T0, 187, ApiCallerName=IBM)], rows((T0 - 20, 58.0, 40.0)), [(T0 - 3600, "AC", 76)], 7, now=T0 + 60)
    assert found and found[0]["on_ac"] and found[0]["busy"]
    assert drift.verdict(found, {"SBUTTONACTION": {"ac": 0}})


def test_drift_ignores_other_callers_idle_and_battery():
    other = ev(T0, 187, ApiCallerName=r"\Device\x\svchost.exe")
    assert drift.sleep_events([other], rows((T0 - 20, 58.0, 40.0)), [(T0 - 3600, "AC", 76)], 7, now=T0) == []
    idle = drift.sleep_events([ev(T0, 187, ApiCallerName=IBM)], rows((T0 - 20, 31.0, 3.0)), [(T0 - 3600, "AC", 76)], 7, now=T0)
    on_bat = drift.sleep_events([ev(T0, 187, ApiCallerName=IBM)], rows((T0 - 20, 58.0, 40.0)), [(T0 - 3600, "Battery", 76)], 7, now=T0)
    assert drift.verdict(idle, {}) == [] and drift.verdict(on_bat, {}) == []


def test_drift_old_events_are_outside_the_window():
    assert drift.sleep_events([ev(T0 - 9 * 86400, 187, ApiCallerName=IBM)], rows((T0 - 9 * 86400 - 5, 60.0, 50.0)), [], 7, now=T0) == []


def test_drift_fails_when_the_sleep_button_is_not_off():
    assert drift.verdict([], {"SBUTTONACTION": {"ac": 1, "dc": 1}})
    assert drift.verdict([], {"SBUTTONACTION": {"ac": 0, "dc": 1}}) == []


QH = """  GUID Alias: SCHEME_BALANCED
    GUID Alias: SUB_BUTTONS
      GUID Alias: LIDACTION
    Current AC Power Setting Index: 0x00000001
    Current DC Power Setting Index: 0x00000001
      GUID Alias: SBUTTONACTION
    Current AC Power Setting Index: 0x00000000
    Current DC Power Setting Index: 0x00000001
      GUID Alias: SHUTDOWN
    Current AC Power Setting Index: 0x00000009
"""


class R:
    def __init__(self, out="", rc=0):
        self.stdout, self.returncode = out, rc


def test_read_settings_parses_qh(monkeypatch):
    monkeypatch.setattr(drift.sys, "platform", "win32")
    s = drift.read_settings(run=lambda *a, **k: R(QH))
    assert s == {"LIDACTION": {"ac": 1, "dc": 1}, "SBUTTONACTION": {"ac": 0, "dc": 1}}


def test_apply_records_before_after_and_names_the_elevate_path_when_refused(monkeypatch):
    monkeypatch.setattr(drift.sys, "platform", "win32")
    calls = []
    state = {"v": 1}

    def fake(cmd, **k):
        calls.append(cmd[1])
        if cmd[1] == "/qh":
            return R(QH.replace("SBUTTONACTION\n    Current AC Power Setting Index: 0x00000000", "SBUTTONACTION\n    Current AC Power Setting Index: 0x0000000%d" % state["v"]))
        if cmd[1] == "/setacvalueindex":
            return R("", 5)
        return R()
    r = drift.apply({"SBUTTONACTION": {"ac": 1}}, run=fake)
    assert r["ok"] is False and r["before"] == 1 and "elevate" in r and "/setactive" not in calls
    state["v"] = 0
    ok = drift.apply({"SBUTTONACTION": {"ac": 1}}, run=lambda c, **k: R(QH) if c[1] == "/qh" else R())
    assert ok["ok"] and ok["before"] == 1 and ok["after"] == 0


def test_drift_run_exit_code(monkeypatch, tmp_path):
    monkeypatch.setattr(drift.sys, "platform", "win32")
    bad = [ev(T0, 187, ApiCallerName=IBM)]
    monkeypatch.setattr(drift.incidents, "read_windows", lambda since: {"events": bad, "resets": []})
    monkeypatch.setattr(drift.incidents, "journal_rows", lambda: rows((T0 - 20, 58.0, 40.0)))
    monkeypatch.setattr(drift.incidents, "battery_usage", lambda: [(T0 - 3600, "AC", 76)])
    monkeypatch.setattr(drift, "read_settings", lambda: {"SBUTTONACTION": {"ac": 0, "dc": 0}})
    monkeypatch.setattr(drift, "lenovo_keys", lambda: [])
    monkeypatch.setattr(drift.time, "time", lambda: T0 + 60)
    assert drift.run(days=7, out=lambda *_: None) == 3
    monkeypatch.setattr(drift.incidents, "read_windows", lambda since: {"events": [], "resets": []})
    assert drift.run(days=7, out=lambda *_: None) == 0


def _cut(extra, t_cut=T0 - 600):
    msg = "The previous system shutdown at %s on 11/2 was unexpected." % time.strftime("%I:%M:%S %p", time.localtime(t_cut)).lstrip("0")
    boot = [ev(T0, 6008, msg), ev(T0 - 5, 6005), ev(T0 - 14, 20, "The last shutdown's success status was true. The last boot's success status was true.")]
    return incidents.classify_windows(boot + list(extra), rows((t_cut - 700, 32.0, 5.0)))


def _writes(n):
    return [ev(T0 - 600 - 30 * k, 112, "scheme write", proc="powercfg.exe") for k in range(1, n + 1)]


def test_cut_below_the_os_during_powercfg_writes_is_named_with_its_evidence():
    inc = _cut(_writes(4))[0]
    assert inc["class"] == incidents.CUT_BELOW_OS_WRITES
    assert inc["class"] in incidents.OPEN_CLASSES        # correlated, not proven: still a defect row, exit 3
    ev_text = " | ".join(inc["evidence"])
    assert "no Kernel-Power 41" in ev_text and "success status was true" in ev_text and "scheme resets" in ev_text and ": 4," in ev_text


def test_few_or_no_powercfg_writes_stay_cause_unknown_with_the_boot_evidence():
    for n in (0, 2):
        inc = _cut(_writes(n))[0]
        assert inc["class"] == "unexpected cut (cause unknown)"
        assert any("Kernel-Boot 20" in x for x in inc["evidence"])


def test_writes_outside_the_window_do_not_count():
    old = [ev(T0 - 600 - 3600 - k, 112, "scheme write", proc="powercfg.exe") for k in range(5)]
    assert _cut(old)[0]["class"] == "unexpected cut (cause unknown)"


def test_a_kernel_power_41_beside_the_cut_blocks_the_below_the_os_class():
    inc = _cut(_writes(4) + [ev(T0 - 3, 41, "rebooted without cleanly shutting down")])[0]
    assert inc["class"] != incidents.CUT_BELOW_OS_WRITES


def test_last_log_record_after_the_heartbeat_is_quoted():
    last = ev(T0 - 15, 9001, "last", last=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(T0 - 200)) + "Z", src="Microsoft-Windows-UserModePowerService/12")
    assert any("last System record before the boot" in x for x in _cut([last])[0]["evidence"])


def test_open_classes_get_a_defect_row_and_exit_3(tmp_path, monkeypatch):
    p = tmp_path / "P.md"
    p.write_text("| 1 | a |\n", encoding="utf-8")
    inc = {"class": incidents.CUT_BELOW_OS_WRITES, "t": T0, "t_text": "2026-10-28 04:11:08", "what": "EventLog 6008", "peak_c": 33.0}
    assert incidents.defect_rows([inc], path=p) == 1
    monkeypatch.setattr(incidents, "collect", lambda since: {"incidents": [inc], "scheme_resets": [], "error": None})
    assert incidents.run(since="2026-10-27", write_rows=False, out=lambda *_: None) == 3


def test_a_42_just_before_the_187_is_still_one_incident_named_by_the_187():
    inc = incidents.classify_windows([ev(T0 - 5, 42, "Sleep Reason: Application API"), ev(T0, 187, ApiCallerName=IBM, SystemAction="2")], [])
    assert [i["class"] for i in inc] == ["program sleep request (ibmpmsvc.exe)"]
