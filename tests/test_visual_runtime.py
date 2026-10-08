"""fieldkit/visual, layer 2 (runtime) with fakes: fake metric JSON, a fake browser process, fake installs.

Fail closed throughout: a run that wrote nothing, a page that did not load, a metric that was not collected, a
probe that cannot see its own planted defects, a DPR never run - each is a FAIL or UNVERIFIABLE, never a PASS.
Nothing here starts a browser.
"""
import json
import subprocess
from pathlib import Path

import pytest

from fieldkit.visual import allow as allowmod, probe_js, runtime


def clean_metrics(**over):
    m = {"dpr": 1, "elements": 120, "images": [], "images_ok": 3, "vector_icons": 9, "broken": [], "zero_size": [],
         "clipped": [], "overlaps": [], "outside": [], "misaligned": [], "page_scrolls_sideways": False, "truncated": []}
    m.update(over)
    return m


def control(dpr=1, drop=()):
    m = clean_metrics(dpr=dpr)
    for key, idents in probe_js.CONTROL_EXPECT.items():
        if key not in drop:
            m[key] = [{"sel": f"div#{i}", "a": f"div#{i} > button", "item": f"div#{i}"} for i in idents]
    return {"url": "resource://gvisual/control.html", "loaded": True, "metrics": m}


def good_run(dpr=1, pages=("about:robots", "about:addons"), **over):
    d = {"schema": 1, "dpr_wanted": dpr, "complete": True, "chrome_dpr": dpr, "list_source": "about:about",
         "listed": list(pages), "errors": [], "control": control(dpr),
         "pages": [{"url": u, "loaded": True, "final_url": u, "dpr": dpr, "metrics": clean_metrics(dpr=dpr)} for u in pages],
         "chrome": [{"surface": s, "opened": True, "dpr": dpr, "metrics": clean_metrics(dpr=dpr)}
                    for s in ("app-menu", "toolbar-context-menu")]}
    d.update(over)
    return d


def runs(**per_dpr):
    out = {}
    for dpr in runtime.DPRS:
        data = per_dpr.get(f"d{dpr}", good_run(dpr))
        out[dpr] = {"data": data, "launch": {"rc": 0, "seconds": 70, "timed_out": False}, "started": data is not None}
    return out


def verdicts(items, rule=None):
    return [(i["rule"], i["item"], i["verdict"]) for i in items if rule is None or i["rule"] == rule]


def summary(items):
    a = {"path": "test", "masters": [], "css_include": [], "css_exclude": [], "accept": [], "problems": []}
    return allowmod.summarise(items, a, "runtime")


# ------------------------------------------------------------------------------------------- judgement
def test_a_clean_build_passes_every_page_menu_and_control():
    items = runtime.judge(runs())
    res = summary(items)
    assert res["ok"], [i for i in items if i["verdict"] != "PASS"]
    assert {i["rule"] for i in items} >= {"RT-RUN", "RT-CONTROL", "RT-LIST", "RT-PAGE", "RT-MENU"}


def test_no_results_at_all_fails_and_says_whether_autoconfig_ran():
    r = runs()
    r[1] = {"data": None, "launch": {"rc": 0, "seconds": 3}, "started": False}
    [it] = [i for i in runtime.judge(r) if i["item"] == "dpr 1"]
    assert it["verdict"] == "FAIL" and "autoconfig never ran" in it["evidence"]
    r[1]["started"] = True
    [it] = [i for i in runtime.judge(r) if i["item"] == "dpr 1"]
    assert it["verdict"] == "FAIL" and "no results" in it["evidence"]


def test_a_crashed_or_hung_run_fails():
    r = runs(d2=good_run(2, complete=False, errors=["run: TypeError"]))
    r[2]["launch"] = {"rc": None, "seconds": 900, "timed_out": True}
    bad = [i for i in runtime.judge(r) if i["rule"] == "RT-RUN" and i["verdict"] == "FAIL"]
    assert len(bad) == 2 and any("did not quit" in i["evidence"] for i in bad)
    assert not summary(runtime.judge(r))["ok"]


def test_a_dpr_that_was_never_run_fails():
    r = runs()
    del r[2]
    assert ("RT-RUN", "dpr 2", "FAIL") in verdicts(runtime.judge(r))


def test_a_page_that_did_not_load_or_was_not_measured_fails():
    d = good_run(1)
    d["pages"][0] = {"url": "about:credits", "loaded": False, "error": "an error page loaded instead: about:neterror"}
    d["pages"][1]["metrics"] = None
    d["pages"][1]["metric_error"] = "TypeError: x is undefined"
    items = runtime.judge(runs(d1=d))
    assert ("RT-LOAD", "about:credits@1x", "FAIL") in verdicts(items)
    assert ("RT-METRIC", "about:addons@1x", "UNVERIFIABLE") in verdicts(items)
    assert not summary(items)["ok"]


def test_empty_measurements_are_not_a_pass():
    d = good_run(1)
    d["pages"][0]["metrics"] = {}
    d["chrome"][0]["metrics"] = {}
    items = runtime.judge(runs(d1=d))
    assert ("RT-METRIC", "about:robots@1x", "UNVERIFIABLE") in verdicts(items)
    assert ("RT-MENU", "app-menu@1x", "FAIL") in verdicts(items)


def test_every_kind_of_defect_is_a_fail_with_its_own_rule():
    m = clean_metrics(dpr=2,
                      images=[{"sel": "img#logo", "kind": "img", "url": "chrome://branding/content/icon32.png",
                               "natural": [32, 32], "painted": [24, 24], "upscale": 1.5}],
                      broken=[{"sel": "img#gone", "kind": "img", "url": "chrome://x/gone.png"}],
                      zero_size=[{"sel": "span#z", "kind": "background", "url": "a.svg", "rect": [262, 0]}],
                      clipped=[{"sel": "span#day0", "how": "spills", "text": "Today", "scroll": 35, "client": 16}],
                      overlaps=[{"a": "button#a", "b": "button#b", "overlap": [30, 20]}],
                      outside=[{"sel": "button#far", "rect": [1500, 1560], "viewport": 1280}],
                      page_scrolls_sideways=[1316, 1280],
                      misaligned=[{"menu": "menupopup#m", "item": "menuitem#i", "what": "label", "x": 40, "mode": 30, "text": "Copy"}],
                      truncated=["clipped"])
    d = good_run(2)
    d["pages"][0]["metrics"] = m
    items = [i for i in runtime.judge(runs(d2=d)) if i["item"].startswith("about:robots@2x")]
    rules = {i["rule"]: i["verdict"] for i in items}
    assert rules == {"RT-UPSCALE": "FAIL", "RT-BROKEN": "FAIL", "RT-ZERO": "FAIL", "RT-CLIP": "FAIL", "RT-OVERLAP": "FAIL",
                     "RT-OFFSCREEN": "FAIL", "RT-MENU-ALIGN": "FAIL", "RT-METRIC": "UNVERIFIABLE"}
    up = next(i for i in items if i["rule"] == "RT-UPSCALE")
    assert "1.5x upscaled" in up["evidence"] and "x 2" in up["evidence"]
    assert not any(i["rule"] == "RT-PAGE" for i in items)


def test_identical_findings_on_repeated_rows_are_merged_with_a_count():
    row = {"a": "list-item > a.cert-url", "b": "list-item > a.cert-url", "overlap": [55, 24]}
    items = runtime.judge_metrics("about:certificate@1x", clean_metrics(overlaps=[row] * 4), 1)
    assert len(items) == 1 and "x4 identical" in items[0]["evidence"]


def test_a_probe_blind_to_a_planted_defect_is_unverifiable_and_a_missing_control_fails():
    d = good_run(1, control=control(1, drop=("clipped",)))
    items = runtime.judge(runs(d1=d))
    assert ("RT-CONTROL", "control@1x clipped", "UNVERIFIABLE") in verdicts(items)
    assert not summary(items)["ok"]
    d = good_run(1)
    del d["control"]
    assert ("RT-CONTROL", "control@1x", "FAIL") in verdicts(runtime.judge(runs(d1=d)))


def test_the_wrong_device_pixel_ratio_fails():
    d = good_run(2, chrome_dpr=1)
    d["pages"][0]["dpr"] = 1
    v = verdicts(runtime.judge(runs(d2=d)), "RT-DPR")
    assert ("RT-DPR", "dpr 2 window", "FAIL") in v and ("RT-DPR", "about:robots@2x", "FAIL") in v


def test_menus_that_did_not_open_or_were_never_measured_fail():
    d = good_run(1)
    d["chrome"] = [{"surface": "app-menu", "opened": False, "error": "app-menu popupshown: no answer in 8000 ms"}]
    v = verdicts(runtime.judge(runs(d1=d)), "RT-MENU")
    assert ("RT-MENU", "app-menu@1x", "FAIL") in v and ("RT-MENU", "toolbar-context-menu@1x", "FAIL") in v


def test_an_empty_page_list_and_runs_that_disagree_fail():
    items = runtime.judge(runs(d1=good_run(1, listed=[], pages=())))
    assert ("RT-LIST", "dpr 1", "FAIL") in verdicts(items)
    items = runtime.judge(runs(d2=good_run(2, pages=("about:robots",))))
    assert ("RT-LIST", "dpr 1 vs 2", "FAIL") in verdicts(items)


def test_the_allowlist_accepts_a_runtime_finding_with_a_reason():
    d = good_run(1)
    d["pages"][0]["metrics"] = clean_metrics(clipped=[{"sel": "span#day0", "how": "spills", "text": "Today", "scroll": 35, "client": 16}])
    items = runtime.judge(runs(d1=d))
    a = {"path": "t", "masters": [], "css_include": [], "css_exclude": [], "problems": [],
         "accept": [{"rule": "RT-CLIP", "item": "about:robots@1x span#day*", "why": "Mozilla's chart labels overflow by design; maintainer, 2026-10-02"}]}
    res = allowmod.summarise(items, a, "runtime")
    assert res["ok"] and res["counts"]["accepted"] == 1


def test_a_probe_that_reports_a_planted_clean_lookalike_is_unverifiable():
    c = control(1)
    c["metrics"]["overlaps"].append({"a": "input#park1", "b": "input#park2", "overlap": [16, 16]})
    items = runtime.judge(runs(d1=good_run(1, control=c)))
    assert ("RT-CONTROL", "control@1x overlaps (clean)", "UNVERIFIABLE") in verdicts(items)
    assert ("RT-CONTROL", "control@2x overlaps (clean)", "PASS") in verdicts(items)
    assert not summary(items)["ok"]
    # one of two planted defects of a kind missed is still blind
    c = control(1)
    c["metrics"]["outside"] = [x for x in c["metrics"]["outside"] if "#cutoff" not in json.dumps(x)]
    it = next(i for i in runtime.judge(runs(d1=good_run(1, control=c))) if i["item"] == "control@1x outside")
    assert it["verdict"] == "UNVERIFIABLE" and "#cutoff" in it["evidence"]


def write_policies(inst, policies):
    (inst / "distribution").mkdir(parents=True, exist_ok=True)
    (inst / "distribution" / "policies.json").write_text(json.dumps({"policies": policies}), encoding="utf-8")


def test_policies_json_names_the_pages_that_must_be_blocked(tmp_path):
    assert runtime.policy_blocked(tmp_path) == {}
    write_policies(tmp_path, {"DisableTelemetry": True, "DisableFirefoxStudies": True, "BlockAboutConfig": False,
                              "PasswordManagerEnabled": False, "DisableDeveloperTools": True})
    b = runtime.policy_blocked(tmp_path)
    assert b["about:telemetry"] == "DisableTelemetry" and b["about:logins"] == "PasswordManagerEnabled"
    assert {"about:debugging", "about:devtools-toolbox", "about:profiling"} <= set(b)
    assert "about:config" not in b                                    # set to false: not blocked
    (tmp_path / "distribution" / "policies.json").write_text("{ not json", encoding="utf-8")
    assert "<unreadable>" in runtime.policy_blocked(tmp_path)


def test_a_page_blocked_by_policy_passes_only_when_it_shows_the_blocked_page():
    d = good_run(1, pages=("about:telemetry", "about:config", "about:robots"))
    d["pages"][0].update(loaded=False, final_url="about:neterror?e=blockedByPolicy&u=about%3Atelemetry",
                         error="an error page loaded instead: about:neterror?e=blockedByPolicy")
    blocked = {"about:telemetry": "DisableTelemetry", "about:config": "BlockAboutConfig"}
    items = runtime.judge(runs(d1=d, d2=good_run(2, pages=("about:telemetry", "about:config", "about:robots"))),
                          blocked=blocked)
    v = verdicts(items)
    assert ("RT-POLICY", "about:telemetry@1x", "PASS") in v
    assert ("RT-POLICY", "about:config@1x", "FAIL") in v               # loaded normally despite the policy
    assert ("RT-POLICY", "about:telemetry@2x", "FAIL") in v            # loaded normally in the 2x run
    assert not any(i["rule"] in ("RT-LOAD", "RT-PAGE") and i["item"].startswith(("about:telemetry", "about:config"))
                   for i in items)
    assert not summary(items)["ok"]
    unread = runtime.judge(runs(), blocked={"<unreadable>": "policies.json: bad"})
    assert ("RT-POLICY", "dpr 1", "FAIL") in verdicts(unread)


def test_a_page_redirected_to_another_listed_page_is_judged_once_where_it_ended():
    pages = ("about:home", "about:welcome", "about:robots")
    d = good_run(1, pages=pages)
    d["pages"][1]["final_url"] = "about:home"
    d["pages"][1]["metrics"] = clean_metrics(overlaps=[{"a": "button#x", "b": "button#y", "overlap": [9, 9]}])
    items = runtime.judge(runs(d1=d, d2=good_run(2, pages=pages)))
    [it] = [i for i in items if i["item"] == "about:welcome@1x"]
    assert it["rule"] == "RT-LOAD" and it["verdict"] == "PASS" and "redirected to about:home, measured there" in it["evidence"]
    # a redirect to a page that is not itself measured is judged where it landed, as before
    d = good_run(1, pages=pages)
    d["pages"][1]["final_url"] = "about:blank"
    assert ("RT-PAGE", "about:welcome@1x", "PASS") in verdicts(runtime.judge(runs(d1=d, d2=good_run(2, pages=pages))))


def test_the_measuring_script_carries_the_clip_ink_and_text_range_rules():
    js = probe_js.MEASURE_MJS
    for needle in ("function clipRegion(", "function inkRects(", "function ownTextRects(", "createRange()",
                   'acs.overflowX === "auto" || acs.overflowX === "scroll"', "inkA, boxB], [boxA, inkB"):
        assert needle in js, needle
    html = probe_js.CONTROL_HTML
    for ids in list(probe_js.CONTROL_EXPECT.values()) + list(probe_js.CONTROL_CLEAN.values()):
        for i in ids:
            assert f'id="{i}"' in html, i


# ------------------------------------------------------------------------------------------- the throwaway copy
def fake_install(tmp_path):
    inst = tmp_path / "Gorilla Unleashed"
    (inst / "defaults" / "pref").mkdir(parents=True)
    (inst / "firefox.exe").write_bytes(b"MZ fake")
    (inst / "omni.ja").write_bytes(b"PK")
    (inst / "defaults" / "pref" / "channel-prefs.js").write_text('pref("app.update.channel", "default");\n')
    return inst


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    t = tmp_path / "temp"
    t.mkdir()
    monkeypatch.setattr(runtime.throwaway, "root", lambda: t)
    return t


def test_the_probe_is_written_only_into_the_copy_and_the_install_is_untouched(tmp_path, temp_root):
    inst = fake_install(tmp_path)
    before = sorted((p.relative_to(inst).as_posix(), p.read_bytes()) for p in inst.rglob("*") if p.is_file())
    root = runtime.make_root()
    copy = runtime.copy_build(inst, root)
    out = root / "results-dpr2.json"
    probe = runtime.write_probe(copy, out, 2, only=("about:robots",), page_ms=5000)
    cfg = (copy / "gvisual.cfg").read_text(encoding="utf-8")
    assert cfg.startswith("//") and json.dumps(str(out)) in cfg and "const DPR = 2;" in cfg and '["about:robots"]' in cfg
    assert '"%' not in cfg and "%DPR%" not in cfg
    assert 'general.config.sandbox_enabled", false' in (copy / "defaults" / "pref" / "autoconfig.js").read_text()
    assert (probe / "GVisualChild.sys.mjs").is_file() and (probe / "control.html").is_file()
    assert "data:image/png;base64," in (probe / "control.html").read_text()
    after = sorted((p.relative_to(inst).as_posix(), p.read_bytes()) for p in inst.rglob("*") if p.is_file())
    assert before == after
    with pytest.raises(runtime.Refused):
        runtime.write_probe(inst, out, 1)                 # the install itself: refused
    assert runtime.discard(root) and not root.exists()


def test_copies_and_deletes_are_refused_outside_our_own_throwaway_folder(tmp_path, temp_root):
    inst = fake_install(tmp_path)
    foreign = temp_root / "gvisual_not_ours"
    foreign.mkdir()
    assert not runtime.discard(foreign) and foreign.exists()                  # no marker: never deleted
    assert not runtime.discard(inst) and inst.exists()
    with pytest.raises(runtime.Refused):
        runtime.copy_build(inst, foreign)
    root = runtime.make_root()
    with pytest.raises(runtime.Refused):
        runtime.copy_build(tmp_path / "nowhere", root)                        # no firefox.exe
    (inst / "gvisual.cfg").write_text("// someone else's")
    (inst / "defaults" / "pref" / "autoconfig.js").write_text('pref("general.config.filename", "x.cfg");')
    root2 = runtime.make_root()
    with pytest.raises(runtime.Refused):
        runtime.copy_build(inst, root2)                                      # an install that already has autoconfig


def test_the_profile_pins_the_dpr_and_a_dead_proxy(tmp_path, temp_root):
    root = runtime.make_root()
    js = runtime.make_profile(root, 2).joinpath("user.js").read_text()
    assert 'user_pref("layout.css.devPixelsPerPx", "2.0");' in js
    assert 'user_pref("network.proxy.http_port", 9);' in js and 'user_pref("network.proxy.type", 1);' in js


class FakeProc:
    def __init__(self, hang, pid=4242):
        self.hang, self.pid = hang, pid

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("firefox", timeout)
        return 0


def test_launch_stops_only_its_own_process_and_only_while_it_is_alive(tmp_path):
    killed, argv = [], []

    def popen(cmd, **kw):
        argv.append((cmd, kw["env"]))
        return FakeProc(hang=len(argv) == 2)
    r = runtime.launch(tmp_path, tmp_path / "p", 2, 5, popen=popen, kill=killed.append, sleep=lambda s: None)
    assert r["rc"] == 0 and not r["timed_out"] and killed == []               # exited: its PID may be reused, never killed
    cmd, env = argv[0]
    assert cmd[1:4] == ["-headless", "-no-remote", "-profile"] and env["MOZ_HEADLESS_WIDTH"] == "2560"
    r = runtime.launch(tmp_path, tmp_path / "p", 1, 5, popen=popen, kill=killed.append, sleep=lambda s: None)
    assert r["timed_out"] and killed == [4242]


def test_a_whole_run_with_a_fake_browser_keeps_evidence_and_deletes_the_copy(tmp_path, temp_root, monkeypatch):
    inst = fake_install(tmp_path)
    seen = {}

    def fake_launch(copy, prof, dpr, timeout_s):
        seen.setdefault("roots", set()).add(Path(copy).parent)
        probe = Path(copy) / "gvisual"
        (probe / "started.txt").write_text("ran")
        cfg = (Path(copy) / "gvisual.cfg").read_text(encoding="utf-8")
        out = json.loads(cfg.split("const OUT = ")[1].split(";")[0])
        Path(out).write_text(json.dumps(good_run(dpr)), encoding="utf-8")
        return {"rc": 0, "seconds": 1.0, "timed_out": False, "pid": 1}
    monkeypatch.setattr(runtime, "launch", fake_launch)
    a = {"path": "t", "masters": [], "css_include": [], "css_exclude": [], "accept": [], "problems": []}
    res = runtime.run(inst, say=lambda m: None, keep_dir=tmp_path / "evidence", allow=a)
    assert res["ok"], [i for i in res["items"] if i["verdict"] != "PASS"]
    assert (tmp_path / "evidence" / "results-dpr1.json").is_file() and (tmp_path / "evidence" / "results-dpr2.json").is_file()
    assert (tmp_path / "evidence" / "runtime-report.json").is_file()
    [root] = seen["roots"]
    assert not root.exists() and inst.joinpath("firefox.exe").is_file()
    # a partial run (some pages only) is never a proof
    res = runtime.run(inst, only=("about:robots",), say=lambda m: None, keep_dir=tmp_path / "e2", allow=a)
    assert not res["ok"] and "partial run" in res["problems"][0]


def test_a_run_whose_probe_never_ran_fails(tmp_path, temp_root, monkeypatch):
    inst = fake_install(tmp_path)
    monkeypatch.setattr(runtime, "launch", lambda copy, prof, dpr, t: {"rc": 1, "seconds": 2.0, "timed_out": False, "pid": 1})
    a = {"path": "t", "masters": [], "css_include": [], "css_exclude": [], "accept": [], "problems": []}
    res = runtime.run(inst, say=lambda m: None, keep_dir=tmp_path / "evidence", allow=a)
    assert not res["ok"] and all(i["verdict"] == "FAIL" for i in res["items"])
    assert "autoconfig never ran" in res["items"][0]["evidence"]


# ------------------------------------------------------------------------------------------- wiring
def test_visual_is_a_post_install_proof_row_and_a_build_preflight_row(monkeypatch, tmp_path):
    from fieldkit import visual
    from fieldkit.buildh import install as inst
    assert "visual" in inst.PROOF_CHECKS
    monkeypatch.setattr(visual, "proof_row", lambda t, target, say=print: {"check": visual.ROW, "ok": False, "evidence": "x", "bad": []})
    [row] = inst._visual_row({"workdir": str(tmp_path)}, tmp_path)
    assert row["check"].split(":")[0] == "visual" and not row["ok"]


def test_preflight_for_a_build_carries_the_static_visual_row_and_a_job_preflight_does_not(tmp_path, monkeypatch):
    from fieldkit import visual
    from fieldkit.buildh import preflight, task
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    task.start("v1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "0"}})
    monkeypatch.setattr(preflight.vault, "verify", lambda *a, **k: {"intact": True, "problems": []})
    monkeypatch.setattr(visual, "preflight_row", lambda t: {"check": visual.ROW, "ok": False, "evidence": "static: 2 fail", "bad": []})
    rows = {r["check"]: r for r in preflight.run("v1", build=True)}
    assert visual.ROW in rows and not rows[visual.ROW]["ok"]
    assert visual.ROW not in {r["check"] for r in preflight.run("v1")}


def test_a_crashing_check_is_a_failed_row_not_a_pass(monkeypatch):
    from fieldkit import visual

    def boom(*a, **k):
        raise RuntimeError("no tree")
    monkeypatch.setattr(visual, "check", boom)
    assert not visual.preflight_row({"workdir": "x"})["ok"]
    assert not visual.proof_row({"workdir": "x"}, "y", say=lambda m: None)["ok"]


def test_no_install_means_the_runtime_layer_fails(tmp_path, monkeypatch):
    from fieldkit import visual
    from fieldkit.visual import static
    monkeypatch.setattr(visual, "task_context", lambda t: (tmp_path, "b", None, None))
    monkeypatch.setattr(static, "check", lambda *a, **k: summary([{"layer": "static", "rule": "X", "item": "i", "verdict": "PASS", "evidence": ""}]))
    res = visual.check({"workdir": str(tmp_path)}, install_dir=tmp_path / "none", say=lambda m: None)
    assert not res["ok"] and res["runtime"]["items"][0]["rule"] == "RT-RUN"
    assert "VISUAL NOT OK" in visual.lines(res)[-1]


def test_the_cli_knows_the_visual_action():
    from fieldkit import cli
    a = cli.build_parser().parse_args(["build-harness", "visual", "t1", "--static"])
    assert a.action == "visual" and a.static


def test_a_box_wider_than_its_parent_is_rt_box_and_the_control_plants_one_and_two_lookalikes():
    row = {"sel": "div#boxover", "parent": "div#boxbox", "rect": [0, 150], "content": [0, 100], "by": 50}
    [it] = runtime.judge_metrics("about:robots@1x", clean_metrics(overflowing=[row]), 1)
    assert it["rule"] == "RT-BOX" and it["verdict"] == "FAIL" and "by 50 px" in it["evidence"]
    assert "boxover" in probe_js.CONTROL_EXPECT["overflowing"]
    assert {"badge", "bleed"} <= set(probe_js.CONTROL_CLEAN["overflowing"])
    assert 'push("overflowing"' in probe_js.MEASURE_MJS
    c = control(1, drop=("overflowing",))
    items = runtime.judge(runs(d1=good_run(1, control=c)))
    assert ("RT-CONTROL", "control@1x overflowing", "UNVERIFIABLE") in verdicts(items)
    c = control(1)
    c["metrics"]["overflowing"].append({"sel": "div#bleed", "parent": "div#bleedbox"})
    items = runtime.judge(runs(d1=good_run(1, control=c)))
    assert ("RT-CONTROL", "control@1x overflowing (clean)", "UNVERIFIABLE") in verdicts(items)


def test_a_change_goes_into_the_copy_only_and_the_run_is_a_preview_never_ok(tmp_path, temp_root, monkeypatch):
    """visual omni=/file=/sub=: a candidate fix on the pages it touches without a build (2026-10-04, four CSS/JS
    fixes proven by a throwaway script that wrapped copy_build); the install is only read, the run is never OK."""
    import zipfile
    inst = fake_install(tmp_path)
    (inst / "omni.ja").unlink()
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("chrome/toolkit/skin/classic/global/aboutLicense.css", "old")
    fix = tmp_path / "aboutLicense.css.new"
    fix.write_text("fixed", encoding="utf-8")
    seen = []

    def fake_launch(copy, prof, dpr, timeout_s):
        with zipfile.ZipFile(Path(copy) / "omni.ja") as z:
            seen.append(z.read("chrome/toolkit/skin/classic/global/aboutLicense.css"))
        (Path(copy) / "gvisual" / "started.txt").write_text("ran")
        cfg = (Path(copy) / "gvisual.cfg").read_text(encoding="utf-8")
        Path(json.loads(cfg.split("const OUT = ")[1].split(";")[0])).write_text(json.dumps(good_run(dpr)), encoding="utf-8")
        return {"rc": 0, "seconds": 1.0, "timed_out": False, "pid": 1}
    monkeypatch.setattr(runtime, "launch", fake_launch)
    a = {"path": "t", "masters": [], "css_include": [], "css_exclude": [], "accept": [], "problems": []}
    change = {"omni": {"omni.ja:chrome/toolkit/skin/classic/global/aboutLicense.css": str(fix)}}
    res = runtime.run(inst, say=lambda m: None, keep_dir=tmp_path / "evidence", allow=a, change=change)
    assert seen == [b"fixed", b"fixed"]                                          # both DPR runs saw the fix
    assert res["patched"] == ["omni.ja:chrome/toolkit/skin/classic/global/aboutLicense.css"]
    assert not res["ok"] and any("preview of the change" in p for p in res["problems"])
    with zipfile.ZipFile(inst / "omni.ja") as z:                               # the install itself is untouched
        assert z.read("chrome/toolkit/skin/classic/global/aboutLicense.css") == b"old"
    # a member the build does not have: refused, never measured as if it had been applied
    bad = {"omni": {"omni.ja:chrome/nope.css": str(fix)}}
    res = runtime.run(inst, say=lambda m: None, keep_dir=tmp_path / "e2", allow=a, change=bad)
    assert not res["ok"] and res["items"][0]["rule"] == "RT-RUN" and "nope.css" in res["items"][0]["evidence"]
    assert not list(temp_root.glob("gvisual_*"))                               # every throwaway copy is gone


def test_the_visual_command_takes_the_probe_change_options(monkeypatch, tmp_path):
    from fieldkit import cli, visual
    from fieldkit.buildh import task
    got = {}
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    monkeypatch.setattr(visual, "check", lambda t, **kw: got.update(kw) or {"ok": False, "static": None, "runtime": None})
    monkeypatch.setattr(visual, "lines", lambda r: ["VISUAL NOT OK"])
    fix = tmp_path / "x.css"
    fix.write_text("x", encoding="utf-8")
    rc = cli.main(["build-harness", "visual", "t1", f"omni=omni.ja:chrome/x.css={fix}", "sub=old-logo.png=>new-logo.png",
                   "--install-dir", str(tmp_path), "--only", "about:license"])
    assert rc == 3 and got["only"] == ("about:license",)
    assert got["change"] == {"omni": {"omni.ja:chrome/x.css": str(fix)}, "subs": [("old-logo.png", "new-logo.png")]}
    assert cli.main(["build-harness", "visual", "t1", f"omni=omni.ja:chrome/x.css={fix}", "--static"]) == 2
