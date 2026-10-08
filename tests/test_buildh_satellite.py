"""Satellite mode judged (2026-10-08, owner: "save all the satellite mobile and calls probes and make them part of the
harness"): each probe case has an expected identity (the header the page received) and script state."""
from fieldkit.buildh import satellite as sat

AND = "user-agent: Mozilla/5.0 (Android 10; Mobile; rv:157.0) Gecko/157.0 Firefox/157.0"
WIN = "user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:157.0) Gecko/20100101 Firefox/157.0"


def srv(case, ua):
    return f"SERVER [echo-ua 127.0.0.1:8765] GET /{case} | {ua}"


def test_parse_reads_headers_scripts_menu_and_the_list():
    p = sat.parse(["CALLS|list|web.whatsapp.com,meet.google.com", srv("very-slow-on-call-list", WIN),
                   "CALLS|very-slow-on-call-list|JS ran, navigator: Mozilla/5.0 (Windows NT 10.0)",
                   "CALLS-MENU|very-slow-on-call-list|desktop-site checked=true disabled=true|javascript-site checked=true disabled=true|This site makes calls: ...",
                   srv("level2", AND), "level2 | JS OFF (page loaded, script did not run)"])
    assert p["header"] == {"very-slow-on-call-list": "desktop", "level2": "android"}
    assert p["js"] == {"very-slow-on-call-list": True, "level2": False}
    assert p["menu"]["very-slow-on-call-list"]["js_disabled"] and p["calls_list"].startswith("web.whatsapp")


def test_judge_names_every_case_that_misses_and_every_case_not_run():
    p = sat.parse([srv("very-slow-microphone-remembered", AND), "CALLS|very-slow-microphone-remembered|JS OFF (page loaded, script did not run)"])
    bad = sat.judge(p, {"very-slow-microphone-remembered": ("desktop", True), "off-plain-site": ("desktop", True)})
    assert "very-slow-microphone-remembered: identity android, expected desktop" in bad
    assert "very-slow-microphone-remembered: scripts off, expected on" in bad
    assert "off-plain-site: not run" in bad


def test_rows_pass_a_good_run_and_fail_a_build_without_the_call_list(monkeypatch):
    mobile = []
    for case, (ident, js) in sat.MOBILE_CASES.items():
        mobile += [srv(case, WIN if ident == "desktop" else AND), f"{case} | " + ("JS ran, navigator: x" if js else "JS OFF (page loaded, script did not run)")]
    mobile += ["tab kept open, level Off, reloaded | JS ran", "level 0: clear cache at shutdown true, check_doc_frequency 3, ua override (none), platform override (none)"]
    calls = ["CALLS|list|web.whatsapp.com"]
    for case, (ident, js) in sat.CALL_CASES.items():
        calls += [srv(case, WIN if ident == "desktop" else AND), f"CALLS|{case}|" + ("JS ran, navigator: x" if js else "JS OFF (page loaded, script did not run)")]
    for case, call in sat.CALL_MENU_CASES.items():
        flag = "true" if call else "false"
        calls.append(f"CALLS-MENU|{case}|desktop-site checked={flag} disabled={flag}|javascript-site checked={flag} disabled={flag}|"
                     + ("This site makes calls: x" if call else "For a site that works badly"))
    runs = {"satellite-mobile": mobile, "satellite-calls": calls}
    monkeypatch.setattr("fieldkit.buildh.probe.run", lambda app, js, **k: {"timeline": runs[js], "done": True})
    assert all(r["ok"] for r in sat.rows("app", say=lambda m: None))
    runs["satellite-calls"] = [l if not l.startswith("CALLS|list|") else "CALLS|list|(no call_sites pref in this build)" for l in calls]
    r = sat.rows("app", say=lambda m: None)
    assert r[0]["ok"] and not r[1]["ok"] and "no gorilla.linkmode.call_sites list" in r[1]["evidence"]
    assert sat.rows("app", say=lambda m: None, need_call_list=False)[1]["ok"]
