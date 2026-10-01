"""A hunk whose target is gone from the new source is parked for the owner, never handed to a model.

The real case: hunk h23 of browser/app/profile/firefox.js (live run 7, 2026-10-01). Gorilla's comment block and
`#if` around browser.preonboarding.enabled do not exist in Firefox 157; Gemma was asked to port it anyway, invented
edits and was refused twice. The file text below is the real 157 region.
"""
import subprocess

import pytest

from fieldkit.buildh import compile as cg, firefox, task
from tests.test_buildh_task import chk, pkt  # noqa: F401

REAL_157 = """pref("browser.partnerlink.attributionURL", "https://topsites.services.mozilla.com/cid/");
pref("browser.partnerlink.campaign.topsites", "amzn_2020_a1");

// Activates preloading of the new tab url.
pref("browser.newtab.preload", true);

// For further detail on the TOU prefs below, see the `preonboarding` feature in
// FeatureManifest.yaml
pref("browser.preonboarding.currentPolicyVersion", 5);
""".splitlines()

H23 = {"lines": [
    " // Activates preloading of the new tab url.",
    ' pref("browser.newtab.preload", true);',
    " ",
    "-// Preonboarding is disabled by default on platforms other than Windows and",
    "-// macOS. For official Mozilla distributions (only for Linux), enabled at",
    "-// runtime in TelemetryReportingPolicy.",
    "-#if !defined(XP_WIN) && !defined(XP_MACOSX)",
    "+// Preonboarding is disabled by default on Linux.",
    "+// For official Mozilla distributions, enable at runtime through",
    "+// Policy.isEligibleOnLinux() in TelemetryReportingPolicy.",
    "+#ifdef XP_LINUX",
    '   pref("browser.preonboarding.enabled", false);',
    " #endif",
    " "]}


def test_the_real_h23_is_recognised_as_gone_upstream():
    why = firefox.obsolete_upstream(REAL_157, H23)
    assert why and "upstream removed or replaced" in why and "owner decides" in why


def test_a_hunk_whose_old_text_is_still_there_is_an_ordinary_job():
    body = REAL_157 + ["// Preonboarding is disabled by default on platforms other than Windows and"]
    assert firefox.obsolete_upstream(body, H23) is None


def test_a_hunk_already_applied_upstream_is_not_called_obsolete():
    body = REAL_157 + ["// Preonboarding is disabled by default on Linux."]
    assert firefox.obsolete_upstream(body, H23) is None


def test_without_an_anchor_in_the_file_nothing_is_concluded():
    assert firefox.obsolete_upstream(["completely", "different", "file"], H23) is None


def test_only_short_or_trivial_lines_prove_nothing():
    hunk = {"lines": [" // a long enough context line to anchor", "-}", "-#endif", "+  x"]}
    assert firefox.obsolete_upstream(["// a long enough context line to anchor"], hunk) is None


# -- the engine parks it, visibly, and the gate refuses ------------------------------------------------

def auto_defer(t, s, **kw):
    return {"ok": False, "defer": True, "why": ["upstream removed this"]}


@pytest.fixture
def parked(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "base.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "b"], check=True)
    steps = [{"id": "port-x", "kind": "model", "title": "x", "packet": "tests.test_buildh_task:pkt",
              "check": "tests.test_buildh_task:chk", "auto": "tests.test_buildh_obsolete:auto_defer",
              "allowed": ["alpha.txt"], "args": {"word": "alpha"}}]
    task.start("o1", "demo", w, steps, budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    task.approve("o1", "owner")
    return w


def test_a_deferred_step_is_parked_not_done_and_not_given_to_a_model(parked):
    r = task.packet("o1")
    assert r["state"] == "DEFERRED" and "waiting for the owner" in r["why"][0]
    t = task.load("o1")
    assert t["steps"][0]["status"] == "deferred" and t["steps"][0]["attempts"] == 0
    events = [e["event"] for e in map(__import__("json").loads, (task.STATE / "o1" / "journal.jsonl").read_text(encoding="utf-8").splitlines())]
    assert "deferred" in events and "done-with-deferred" in events and "done" not in events


def test_the_build_gate_refuses_while_a_step_is_deferred(parked, tmp_path, monkeypatch):
    task.packet("o1")
    h = tmp_path / "harness" / "config"
    h.mkdir(parents=True)
    (h / "mozconfig.win64").write_text("mk_add_options MOZ_OBJDIR=C:/x\n")
    monkeypatch.setattr(cg, "mozconfig_path", lambda root=None: h / "mozconfig.win64")
    rows = {r["check"]: r for r in cg.gate("o1")}
    assert not rows["every step is done"]["ok"]


def test_a_retry_from_the_owner_gives_the_harness_its_own_go_again(parked):
    task.packet("o1")
    task.unblock("o1", "port-x", "retry")
    t = task.load("o1")
    assert t["steps"][0]["status"] == "pending" and t["steps"][0]["auto_tried"] is False
