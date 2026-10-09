"""Stop classifier (2026-10-09, proactive programme item 3): every run that did not pass is put down to the browser,
the check or the machine, from the records only."""
import datetime

from fieldkit.buildh import stops as st

T = datetime.datetime


def _run(cmd, start, end, exit_code, why="", passed=None):
    return {"log": f"{start:%Y%m%d-%H%M%S}-{cmd}-firefox-x.log", "cmd": cmd, "start": start, "end": end,
            "exit": exit_code, "passed": exit_code == 0 if passed is None else passed, "why": why}


def _changed(tree=(), harness=(), check=()):
    return lambda a, b, ck: {"tree": list(tree), "harness": list(harness), "check": list(check)}


NOW = T(2026, 10, 10, 12, 0)


def test_thermal_stop_on_a_cool_laptop_is_a_false_alarm_and_a_hot_one_is_real():
    rs = [_run("build-run", T(2026, 10, 9, 11, 1), T(2026, 10, 9, 11, 50), 3, "11:44:30  build STOPPED rc=1: thermal")]
    j = [{"event": "thermal", "t": "2026-10-09 11:44:00", "peak": 45.0}]
    assert st.classify(rs, j, [], _changed(), now=NOW)[0]["verdict"] == "false alarm"
    j[0]["peak"] = 81.0
    assert st.classify(rs, j, [], _changed(), now=NOW)[0]["verdict"] == "real"


def test_standby_inside_a_run_without_an_exit_is_the_machine_and_a_fresh_run_is_still_running():
    rs = [_run("build-run", T(2026, 10, 9, 0, 13), T(2026, 10, 9, 0, 30), -1)]
    ev = [(T(2026, 10, 9, 0, 20), "standby")]
    assert st.classify(rs, [], ev, _changed(), now=NOW)[0]["verdict"] == "machine"
    fresh = [_run("build-run", NOW - datetime.timedelta(minutes=20), NOW - datetime.timedelta(minutes=5), None)]
    assert st.classify(fresh, [], [], _changed(), now=NOW) == []


def test_what_changed_before_the_next_pass_decides():
    rs = [_run("build-verify", T(2026, 10, 9, 14, 8), T(2026, 10, 9, 14, 20), 3, "FAIL  stamp: x"),
          _run("build-verify", T(2026, 10, 9, 14, 40), T(2026, 10, 9, 15, 0), 0)]
    assert st.classify(rs, [], [], _changed(harness=["fix stamp"], check=["fix stamp"]), now=NOW)[0]["verdict"] == "false alarm"
    assert st.classify(rs, [], [], _changed(tree=["D-157-40 css"]), now=NOW)[0]["verdict"] == "real"
    assert st.classify(rs, [], [], _changed(tree=["css"], check=["fix stamp"]), now=NOW)[0]["verdict"] == "unclear"
    assert st.classify(rs, [], [], _changed(), now=NOW)[0]["verdict"] == "transient"
    assert st.classify(rs[:1], [], [], _changed(), now=NOW)[0]["verdict"] == "open"


def test_a_post_install_with_only_the_keyboard_check_skipped_counts_as_passed(tmp_path):
    (tmp_path / "20261009-204422-post-install-firefox-x.log").write_text(
        "  [ok] a: b\n  [SKIPPED] verify_address_bar: keyboard\nPOST-INSTALL NOT OK: \n  SKIPPED: verify_address_bar\nexit 3\n",
        encoding="utf-8")
    (tmp_path / "20261009-191921-post-install-firefox-x.log").write_text(
        "  [FAIL] claims: 4 contradicted\nPOST-INSTALL NOT OK: \n  FAIL: claims\nexit 3\n", encoding="utf-8")
    rs = st.runs(tmp_path, "firefox-x")
    assert [r["passed"] for r in rs] == [False, True] and rs[0]["why"].startswith("[FAIL] claims")
    assert st.check_of(rs[0]["why"]) == "claims"
