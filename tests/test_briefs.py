"""Decision briefs: a complete brief or none, every producer fills it from the data, only the maintainer records."""
import copy
import json
import subprocess

import pytest
import yaml

from fieldkit import mcp
from fieldkit.briefs import producers, record, schema
from fieldkit.buildh import task

REGISTER_HEAD = """# the maintainer's register (a comment that must survive every append)
release: "157.0"

decisions:

  - id: D-157-01
    title: No automatic updates
    decided: 2026-10-02
    by: maintainer
    provenance: chat 2026-10-02 ("no updates, ever")
    status: enforced
    why: >
      The browser never downloads anything by itself.
    verify:
      - mozconfig_has: {file: config/mozconfig, text: "ac_add_options --disable-updater"}
"""

PATCH = """# GORILLA: stop the search suggestions from leaving the machine
--- a/browser/app/profile/firefox.js
+++ b/browser/app/profile/firefox.js
@@ -1,3 +1,3 @@
 // context line one
-pref("browser.search.suggest.enabled", true);
+pref("browser.search.suggest.enabled", false, locked);
 // context line two
@@ -10,2 +10,3 @@
 // context line ten
+pref("browser.urlbar.quicksuggest.enabled", false, locked);
"""


def good_brief(**over):
    b = {"id": "B-DECISION-X", "topic": "Something needs deciding", "kind": "decision", "task": "t1",
         "source": {"check": "decisions.check", "verdict": "VIOLATED"},
         "affected": {"count": 1, "counts": {"checks failing": 1},
                      "items": [{"what": "a check", "excerpt": "FAIL pref: x = true", "location": "r.yaml:3"}]},
         "options": [{"id": "make-true", "label": "fix it", "what_changes": "a", "user_impact": "b", "credibility_impact": "c",
                      "cost": "d", "reversible": True, "records": "register"},
                     {"id": "hold", "label": "wait", "what_changes": "nothing", "user_impact": "none", "credibility_impact": "none",
                      "cost": "none", "reversible": True, "records": "journal"}],
         "recommendation": {"option": "make-true", "why": "because"}, "what_gets_recorded": "an entry",
         "blocking": {"blocks": ["the release"], "deadline": None}, "target": {"decision": "D-157-01"}}
    b.update(over)
    return b


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    o = tmp_path / "owner"
    for d in ("decisions", "claims", "config", "leakgate", "gorilla-patchset/patches/05.PREFS"):
        (o / d).mkdir(parents=True)
    (o / "decisions" / "PRODUCT-DECISIONS.yaml").write_bytes(REGISTER_HEAD.encode())
    (o / "config" / "mozconfig").write_text("ac_add_options --enable-updater\n", encoding="utf-8")
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches"}), encoding="utf-8")
    (o / "gorilla-patchset" / "patches" / "05.PREFS" / "firefox.js.patch").write_text(PATCH, encoding="utf-8")
    (o / "claims" / "CLAIMS.yaml").write_text("claims:\n- id: C-0001\n  text: x\n  evidence:\n  - decision: D-157-01\n"
                                              "- id: C-0002\n  text: y\n", encoding="utf-8")
    w = tmp_path / "work"
    w.mkdir()
    task.start("t1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    return o


def claims_res():
    contra = {"id": "C-0001", "text": "Gorilla never sends search suggestions anywhere.", "source": "README.md", "line": 12,
              "verdict": "CONTRADICTED", "why": "evidence fails",
              "results": [("pref", {"name": "browser.search.suggest.enabled"}, "FAIL", "browser.search.suggest.enabled = true")]}
    contra2 = dict(contra, id="C-0003", text="Search suggestions are switched off and locked.", line=30)
    unproven = {"id": "C-0002", "text": "No telemetry is ever collected.", "source": "docs/PRIVACY.md", "line": 4,
                "verdict": "UNPROVEN", "why": "no evidence: nothing proves this claim", "results": []}
    cited = dict(contra, id="C-0004", text="The 05.PREFS firefox.js patch locks the urlbar suggestions.", line=40,
                 results=[("patch", "05.PREFS/firefox.js.patch", "FAIL", "PARTIAL")])
    patch = {"patch": "05.PREFS/firefox.js.patch", "group": "05.PREFS", "purpose": "GORILLA: stop the search suggestions from leaving the machine",
             "purpose_source": "patch header", "verdict": "PARTIAL", "implemented_pct": 50.0, "gaps": 1, "explained": False,
             "decision": None, "fail": True,
             "hunks": [{"file": "browser/app/profile/firefox.js", "n": 1, "status": "APPLIED", "detail": "", "explained": False},
                       {"file": "browser/app/profile/firefox.js", "n": 2, "status": "NOT-APPLIED", "detail": "added text absent",
                        "explained": False}]}
    return {"version": "157.0", "claims": [contra, contra2, unproven, cited],
            "patch_audit": {"patches": [patch, dict(patch, patch="05.PREFS/ok.patch", fail=False)]}}


def ctx_for(o, **kw):
    return producers.Context("t1", owner=o, workdir=str(o.parent / "work"), install_dir=None, steps=[],
                             find_install=False, **kw)


# -- validation -------------------------------------------------------------------------------------------------------
def test_a_complete_brief_is_valid():
    assert schema.problems(good_brief()) == []


@pytest.mark.parametrize("mutate, why", [
    (lambda b: b.pop("what_gets_recorded"), "missing what_gets_recorded"),
    (lambda b: b.pop("recommendation"), "missing recommendation"),
    (lambda b: b.update(options=b["options"][:1]), "at least 2 options"),
    (lambda b: b["options"][0].pop("user_impact"), "missing user_impact"),
    (lambda b: b["options"][0].update(credibility_impact=" "), "missing credibility_impact"),
    (lambda b: b["options"][0].pop("cost"), "missing cost"),
    (lambda b: b["options"][0].update(reversible="maybe"), "reversible must be"),
    (lambda b: b["affected"]["items"][0].update(excerpt=""), "verbatim excerpt"),
    (lambda b: b["affected"].update(items=[]), "lists none"),
    (lambda b: b.update(topic="Should I remove the claims?"), "never asks a bare question"),
    (lambda b: b.update(recommendation={"option": "nope", "why": "x"}), "an option that exists"),
    (lambda b: b["source"].pop("check"), "source.check"),
])
def test_validation_refuses_an_incomplete_brief(mutate, why):
    b = good_brief()
    mutate(b)
    p = schema.problems(b)
    assert any(why in x for x in p), p
    with pytest.raises(schema.Invalid):
        schema.validate(b)
    with pytest.raises(schema.Invalid):
        schema.render(b)


def test_recommending_the_deletion_of_a_claim_is_refused():
    b = good_brief()
    b["options"][0]["removes_claim"] = True
    assert any("never to delete it" in x for x in schema.problems(b))


def test_the_fingerprint_is_deterministic_and_changes_with_the_content():
    a, b = good_brief(), good_brief()
    assert schema.sha256(a) == schema.sha256(b)
    b["affected"]["items"][0]["excerpt"] = "FAIL pref: x = false"
    assert schema.sha256(a) != schema.sha256(b)


# -- producers --------------------------------------------------------------------------------------------------------
def test_a_violated_decision_brief_quotes_the_register_and_the_failing_check(owner):
    got = producers.collect(ctx_for(owner), kinds={"decision"})
    assert not got["problems"]
    [b] = got["briefs"]
    assert b["id"] == "B-DECISION-D-157-01" and b["source"]["verdict"] == "VIOLATED"
    text = "\n".join(schema.render(b))
    assert 'chat 2026-10-02 ("no updates, ever")' in text                      # the maintainer's words, verbatim
    assert "FAIL mozconfig_has: config/mozconfig: ac_add_options --disable-updater" in text
    assert "decisions/PRODUCT-DECISIONS.yaml:6" in text                        # where it lives
    assert "claims/CLAIMS.yaml:5" in text                                      # where it is linked from
    assert b["recommendation"]["option"] == "make-true"
    assert next(o for o in b["options"] if o["id"] == "make-true")["verify"]   # the answer carries the checks


def test_a_partial_patch_brief_shows_the_missing_hunk_verbatim(owner):
    [b] = producers.collect(ctx_for(owner, claims_res=claims_res()), kinds={"patch"})["briefs"]
    assert b["id"] == "B-PATCH-05-PREFS-firefox-js-patch"
    assert b["affected"]["counts"] == {"hunks missing": 1, "hunks in the patch": 2, "public claims citing it": 1}
    text = "\n".join(schema.render(b))
    assert '+pref("browser.urlbar.quicksuggest.enabled", false, locked);' in text
    assert "gorilla-patchset/patches/05.PREFS/firefox.js.patch:9" in text       # the second @@ header
    assert "The 05.PREFS firefox.js patch locks the urlbar suggestions." in text
    assert b["recommendation"]["option"] == "make-true"


def test_claim_clusters_quote_every_claim_and_never_recommend_deleting(owner):
    briefs = producers.collect(ctx_for(owner, claims_res=claims_res()), kinds={"claims"})["briefs"]
    x = [b for b in briefs if b["affected"]["counts"]["CONTRADICTED"]]
    u = [b for b in briefs if not b["affected"]["counts"]["CONTRADICTED"]]
    assert len(x) == 2 and len(u) == 1
    assert x[0]["affected"]["count"] == 2                                     # two claims fail the same check: one cluster
    text = "\n".join(schema.render(x[0]))
    assert "Gorilla never sends search suggestions anywhere." in text and "Search suggestions are switched off and locked." in text
    assert "README.md:12" in text
    for b in briefs:
        assert b["recommendation"]["option"] == "make-true"
        assert next(o for o in b["options"] if o["id"] == "withdraw")["removes_claim"]
    assert x[0]["id"] == producers.collect(ctx_for(owner, claims_res=claims_res()), kinds={"claims"})["briefs"][0]["id"]


def test_the_backlog_clusters_are_used_when_present_and_tolerated_when_absent(owner):
    (owner / "claims" / "backlog-157.yaml").write_text(yaml.safe_dump(
        {"clusters": [{"id": "search", "title": "Search suggestions", "claims": ["C-0001", {"id": "C-0004"}]}]}), encoding="utf-8")
    briefs = producers.collect(ctx_for(owner, claims_res=claims_res()), kinds={"claims"})["briefs"]
    b = next(b for b in briefs if b["id"] == "B-CLAIMS-search")
    assert b["affected"]["count"] == 2 and "backlog-157.yaml" in b["source"]["check"]
    rest = {c for x in briefs if x is not b for c in x["target"]["claims"]}
    assert rest == {"C-0002", "C-0003"}                                       # the others are still clustered
    (owner / "claims" / "backlog-157.yaml").write_text("{not yaml: [", encoding="utf-8")
    assert producers.collect(ctx_for(owner, claims_res=claims_res()), kinds={"claims"})["briefs"]


def test_an_owner_decision_disposition_and_an_unapproved_proposal_become_briefs(owner):
    (owner / "leakgate" / "dispositions.json").write_text(json.dumps({
        "security/manager/ssl/nsNSSCallbacks.cpp": {"component": "OCSP", "apis": ["nsIHttpChannel"], "disposition": "OWNER-DECISION",
                                                    "evidence": "OCSP tells the CA which site was visited. security.OCSP.enabled is a pref decision.",
                                                    "approval": None},
        "a/b.mjs": {"disposition": "cut-in-source", "evidence": "x", "approval": {"by": "owner"}}}, indent=1), encoding="utf-8")
    (owner / "leakgate" / "allow.json").write_text(json.dumps({"version": 1, "entries": [
        {"id": "video-drm-request-dest-r1", "kind": "dest", "values": ["redirector.gvt1.com"], "scenarios": ["drm-request"],
         "component": "GMP plugin download", "purpose": "the video compromise", "privacy_impact": "Google sees the IP once",
         "security_impact": "x", "approval": None},
        {"id": "approved-one", "kind": "dest", "values": ["x"], "approval": {"by": "owner"}}]}, indent=1), encoding="utf-8")
    got = producers.collect(ctx_for(owner), kinds={"disposition", "allowlist"})
    assert not got["problems"]
    d, a = got["briefs"]
    assert d["kind"] == "disposition" and d["recommendation"]["option"] == "cut"
    assert "OCSP tells the CA which site was visited." in "\n".join(schema.render(d))
    assert a["id"] == "B-ALLOW-video-drm-request-dest-r1" and a["recommendation"]["option"] == "approve"
    assert "redirector.gvt1.com" in "\n".join(schema.render(a))


def test_a_deferred_step_becomes_a_brief_with_the_parked_lines(owner, monkeypatch):
    from fieldkit.buildh import deferred
    old = {"kind": "deferred", "what": "05.PREFS h1 cannot be applied because the lines it changes are gone from Firefox",
           "evidence": ["the hunk changes 1 line(s) to 1 line(s)"], "options": [
               {"key": "hold", "label": "leave it parked", "consequence": "nothing changes", "reversible": True},
               {"key": "drop", "label": "drop this one change", "consequence": "recorded as dropped", "reversible": True}],
           "recommended": "drop", "why": "it only changes a comment", "confirm": "DROP THIS CHANGE: x h1", "nearby": ["new line"],
           "removed": ["// old comment"], "added": ["// new comment"]}
    monkeypatch.setattr(deferred, "build", lambda tid, sid: old)
    c = ctx_for(owner)
    c.steps = [{"id": "port-05.PREFS-x-h1", "status": "deferred", "args": {"patch": "05.PREFS/x.patch", "file": "x.js"}}]
    [b] = producers.collect(c, kinds={"deferred"})["briefs"]
    text = "\n".join(schema.render(b))
    assert "-// old comment" in text and "+// new comment" in text
    drop = next(o for o in b["options"] if o["id"] == "drop")
    assert "--do \"DROP THIS CHANGE: x h1\"" in drop["carried_out_by"]


def test_an_uncommitted_owner_edit_becomes_a_brief(owner):
    def git(*a):
        subprocess.run(["git", "-C", str(owner), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True,
                       capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    (owner / "config" / "mozconfig").write_text("ac_add_options --enable-updater\nac_add_options --enable-telemetry\n", encoding="utf-8")
    [b] = producers.collect(ctx_for(owner), kinds={"owner-edit"})["briefs"]
    assert b["id"] == "B-EDIT-config-mozconfig"
    assert "+ac_add_options --enable-telemetry" in "\n".join(schema.render(b))


def test_a_producer_that_cannot_run_is_reported_not_skipped(owner):
    (owner / "decisions" / "PRODUCT-DECISIONS.yaml").unlink()
    got = producers.collect(ctx_for(owner), kinds={"decision"})
    assert not got["briefs"] and "decision: could not produce briefs" in got["problems"][0]
    assert "PROBLEM" in "\n".join(producers.listing_lines(got, "t1"))


def test_two_needs_under_one_id_are_both_refused(owner, monkeypatch):
    monkeypatch.setitem(producers.PRODUCERS, "decision", lambda ctx: [good_brief(), good_brief(topic="Another need")])
    got = producers.collect(ctx_for(owner), kinds={"decision"})
    assert not got["briefs"] and "duplicate brief ids" in got["problems"][0]


def test_long_names_get_unique_ids():
    a = producers._ident("07.TOOLKIT/browser_extensions_newtab_content-src_components_TopSites_TopSites.jsx.patch")
    b = producers._ident("07.TOOLKIT/browser_extensions_newtab_content-src_components_Base_Base.jsx.patch")
    assert a != b and len(a) <= 67


def test_a_gate_gets_lines_even_when_briefs_cannot_be_made(monkeypatch):
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(producers, "Context", boom)
    [line] = producers.gate_lines("t1")
    assert "could not be produced" in line and "briefs t1" in line


# -- recording --------------------------------------------------------------------------------------------------------
def test_recording_refuses_without_a_real_terminal(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    before = (owner / "decisions" / "PRODUCT-DECISIONS.yaml").read_bytes()
    with pytest.raises(task.Refused, match="real terminal"):
        record.decide("t1", "B-DECISION-D-157-01", "make-true", "fix it", ctx=ctx_for(owner))
    assert (owner / "decisions" / "PRODUCT-DECISIONS.yaml").read_bytes() == before


def test_recording_refuses_a_brief_that_was_not_shown_as_it_is_now(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    c = ctx_for(owner)
    b = producers.find(c, "B-DECISION-D-157-01")
    with pytest.raises(task.Refused, match="read the brief first"):
        record.decide("t1", b["id"], "make-true", "fix it", ctx=c)
    stale = copy.deepcopy(b)
    stale["topic"] += " (an older text)"
    record.mark_shown("t1", stale)                                            # shown, but not this content
    with pytest.raises(task.Refused, match="read the brief first"):
        record.decide("t1", b["id"], "make-true", "fix it", ctx=c)
    with pytest.raises(task.Refused, match="own words"):
        record.decide("t1", b["id"], "make-true", "  ", ctx=c)


def test_recording_appends_to_the_register_with_the_words_and_the_sha(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    reg = owner / "decisions" / "PRODUCT-DECISIONS.yaml"
    before = reg.read_bytes()
    c = ctx_for(owner)
    b = producers.find(c, "B-DECISION-D-157-01")
    digest = record.mark_shown("t1", b)
    r = record.decide("t1", b["id"], "trade-off", "ship without it, I will say so on the front page", ctx=c)
    after = reg.read_bytes()
    assert after.startswith(before) and b"\r\n" not in after                 # append-only, LF
    new = yaml.safe_load(after)["decisions"][-1]
    assert new["id"] == "D-157-02" and new["status"] == "pending" and new["amends"] == "D-157-01"
    assert "ship without it, I will say so on the front page" in new["provenance"] and digest in new["provenance"]
    assert new["answers_brief"] == {"id": b["id"], "sha256": digest, "option": "trade-off"}
    j = [json.loads(l) for l in (task.STATE / "t1" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert j[-1]["event"] == "decide" and j[-1]["sha256"] == digest and j[-1]["words"].startswith("ship without")
    assert r["recorded"].endswith("D-157-02")
    from fieldkit.buildh import decisions as dec
    assert not dec.load(owner)["problems"]


def test_an_answer_with_checks_is_enforced_by_the_checks(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    c = ctx_for(owner)
    b = producers.find(c, "B-DECISION-D-157-01")
    record.mark_shown("t1", b)
    record.decide("t1", b["id"], "make-true", "keep it, fix the build", ctx=c)
    new = yaml.safe_load((owner / "decisions" / "PRODUCT-DECISIONS.yaml").read_text(encoding="utf-8"))["decisions"][-1]
    assert new["status"] == "enforced" and new["verify"] == [{"mozconfig_has": {"file": "config/mozconfig",
                                                                               "text": "ac_add_options --disable-updater"}}]


def test_recording_into_the_allowlist_and_the_dispositions(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    test_an_owner_decision_disposition_and_an_unapproved_proposal_become_briefs(owner)
    c = ctx_for(owner)
    for bid, opt in (("B-ALLOW-video-drm-request-dest-r1", "approve"), (None, "accept-trade-off")):
        b = producers.find(c, bid) if bid else producers.collect(c, kinds={"disposition"})["briefs"][0]
        digest = record.mark_shown("t1", b)
        record.decide("t1", b["id"], opt, "yes, as the brief says", ctx=c)
    e = json.loads((owner / "leakgate" / "allow.json").read_text(encoding="utf-8"))["entries"][0]
    assert e["approval"]["how"] == "terminal" and e["approval"]["quote"] == "yes, as the brief says"
    d = json.loads((owner / "leakgate" / "dispositions.json").read_text(encoding="utf-8"))["security/manager/ssl/nsNSSCallbacks.cpp"]
    assert d["approval"]["brief_sha256"] == digest and d["approval"]["choice"].startswith("Keep it")
    assert not producers.collect(c, kinds={"disposition", "allowlist"})["briefs"]   # settled: no brief any more


def test_an_option_that_changes_files_keeps_its_own_door(owner, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    test_a_deferred_step_becomes_a_brief_with_the_parked_lines(owner, monkeypatch)
    c = ctx_for(owner)
    c.steps = [{"id": "port-05.PREFS-x-h1", "status": "deferred", "args": {"patch": "05.PREFS/x.patch", "file": "x.js"}}]
    b = producers.collect(c, kinds={"deferred"})["briefs"][0]
    record.mark_shown("t1", b)
    with pytest.raises(task.Refused, match="own guarded door"):
        record.decide("t1", b["id"], "drop", "drop it", ctx=c)


# -- the agent door ---------------------------------------------------------------------------------------------------
def test_the_mcp_door_lists_briefs_but_cannot_decide(owner, monkeypatch):
    names = {t["name"] for t in mcp.TOOLS}
    assert "build_harness_briefs" in names
    assert not any(w in n for n in names for w in ("decide", "record", "approve"))
    schema_props = next(t for t in mcp.TOOLS if t["name"] == "build_harness_briefs")["inputSchema"]["properties"]
    assert set(schema_props) == {"id"}
    text, err = mcp.call_tool("build_harness_decide", {"id": "B-DECISION-D-157-01", "option": "make-true"})
    assert err and "no tool" in text
    task.STATE.mkdir(parents=True, exist_ok=True)
    (task.STATE / "CURRENT").write_text("t1", encoding="utf-8")
    fixed = ctx_for(owner)
    monkeypatch.setattr(producers, "Context", lambda tid, **kw: fixed)
    reg_before = (owner / "decisions" / "PRODUCT-DECISIONS.yaml").read_bytes()
    text, err = mcp.call_tool("build_harness_briefs", {})
    assert not err and "B-DECISION-D-157-01" in text and 'chat 2026-10-02 ("no updates, ever")' in text
    assert "never ask a bare question" in text
    text, err = mcp.call_tool("build_harness_briefs", {"id": "B-DECISION-D-157-01"})
    assert not err and "DECISION NEEDED" in text
    text, err = mcp.call_tool("build_harness_briefs", {"id": "B-NOPE-1"})
    assert not err and "not a brief id" in text
    assert (owner / "decisions" / "PRODUCT-DECISIONS.yaml").read_bytes() == reg_before


def test_the_command_line_decide_refuses_without_a_terminal(owner, monkeypatch, capsys):
    from fieldkit import cli
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    rc = cli.main(["build-harness", "decide", "B-DECISION-D-157-01", "make-true", "--words", "fix it", "--task", "t1"])
    assert rc == 2 and "REFUSED" in capsys.readouterr().err
