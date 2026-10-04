"""The verifier reads the tree and the patch set, never the journal; the new-files step copies what the patch set
carries. Both built from the independent audit of 2026-10-01."""
import json
import subprocess

import pytest

from fieldkit.buildh import compile as cg, firefox, task, verify

PATCH = ('--- a/app.js\n+++ b/app.js\n@@ -1,3 +1,3 @@\n // a context line that is long enough\n'
         '-pref("telemetry.enabled.setting", true);\n+pref("telemetry.enabled.setting", false, locked);\n pref("x.y", 1);\n')


def git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True, capture_output=True)


def _w(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text, encoding="utf-8", newline="")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    _w(w / "app.js", '// a context line that is long enough\npref("telemetry.enabled.setting", true);\npref("x.y", 1);\n')
    _w(w / "browser" / "shipped.png", b"upstream png")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "pristine")
    h = tmp_path / "Gorilla.firefox"
    _w(h / "config" / "patch_policy.json", json.dumps({"patchset_root": "patchset", "groups": {
        "05.PREFS": {"status": "enabled"}, "08.Look": {"status": "enabled"}, "01.MEDIA": {"status": "disabled"}}}))
    _w(h / "patchset" / "05.PREFS" / "prefs.patch", PATCH)
    _w(h / "patchset" / "05.PREFS" / "NEW_FILES" / "mozconfig", "not source\n")
    _w(h / "patchset" / "08.Look" / "NEW_FILES" / "browser" / "branding" / "gorilla" / "logo.png", b"gorilla png")
    _w(h / "patchset" / "08.Look" / "NEW_FILES" / "browser" / "shipped.png", b"gorilla version")   # exists upstream too
    _w(h / "patchset" / "01.MEDIA" / "NEW_FILES" / "linux-only.txt", "x\n")
    _w(h / "src" / "app.js", "the owner's OLD tree version of app.js\n")
    hunk = firefox.parse_patch(PATCH)[0]["hunks"][0]
    steps = [{"id": "port-05.PREFS-prefs-app.js-h1", "kind": "model", "title": "x", "allowed": ["app.js"],
              "packet": "fieldkit.buildh.firefox:packet_port", "check": "fieldkit.buildh.firefox:check_port",
              "args": {"patch": "05.PREFS/prefs.patch", "file": "app.js", "hunk": hunk}}]
    task.start("v1", "demo", w, steps, budget_tokens=1000, meta={"upstream": {"version": "157.0"}, "harness_root": str(h)})
    task.approve("v1", "owner")
    return {"w": w, "h": h}


# -- scoring --------------------------------------------------------------------------------------

def test_scores_from_the_text_alone():
    hunk = firefox.parse_patch(PATCH)[0]["hunks"][0]
    assert verify.score_hunk(['pref("telemetry.enabled.setting", false, locked);'], hunk)[0] == "APPLIED"
    assert verify.score_hunk(['pref("telemetry.enabled.setting", true);'], hunk)[0] == "NOT-APPLIED"
    assert verify.score_hunk(['pref("telemetry.enabled.setting", true);', 'pref("telemetry.enabled.setting", false, locked);'], hunk)[0] == "PARTIAL"
    assert verify.score_hunk(["something entirely different here"], hunk)[0] == "TARGET-GONE"
    assert verify.score_hunk(None, hunk)[0] == "TARGET-GONE"
    assert verify.score_hunk(["x"], {"lines": ["-}", "+{"]})[0] == "NO-SIGNAL"


# -- the whole tree -------------------------------------------------------------------------------

def test_an_untouched_tree_reports_the_hunk_as_not_applied_and_the_new_files_missing(world):
    rep = verify.verify("v1")
    assert rep["groups"]["05.PREFS"]["NOT-APPLIED"] == 1 and rep["false_completions"] == []
    assert "08.Look/NEW_FILES/browser/branding/gorilla/logo.png" in rep["missing_new_files"]
    assert not any("mozconfig" in m or "linux-only" in m for m in rep["missing_new_files"])


def test_a_step_the_record_calls_done_but_the_tree_does_not_back_is_a_false_completion_and_is_reopened(world):
    t = task.load("v1")
    t["steps"][0]["status"], t["steps"][0]["done_by"] = "done", "model"
    task.save(t)
    rep = verify.verify("v1")
    assert [f["step"] for f in rep["false_completions"]] == ["port-05.PREFS-prefs-app.js-h1"]
    assert verify.reopen("v1", rep) == ["port-05.PREFS-prefs-app.js-h1"]
    s = task.load("v1")["steps"][0]
    assert s["status"] == "pending" and s["attempts"] == 0 and "done_by" not in s
    ev = [json.loads(l)["event"] for l in (task.STATE / "v1" / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "reopened" in ev


def test_a_really_applied_step_is_not_reopened(world):
    w = world["w"]
    _w(w / "app.js", '// a context line that is long enough\npref("telemetry.enabled.setting", false, locked);\npref("x.y", 1);\n')
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "checkpoint: port")
    t = task.load("v1")
    t["steps"][0]["status"] = "done"
    task.save(t)
    rep = verify.verify("v1")
    assert rep["false_completions"] == [] and rep["groups"]["05.PREFS"]["APPLIED"] == 1 and verify.reopen("v1", rep) == []


def test_a_dropped_or_skipped_step_is_the_owners_and_is_never_reopened(world):
    t = task.load("v1")
    t["steps"][0]["status"], t["steps"][0]["dropped_by_owner"] = "done", True
    task.save(t)
    assert verify.verify("v1")["false_completions"] == []


def test_a_file_copied_from_the_owners_old_tree_is_caught(world, monkeypatch):
    monkeypatch.setattr(verify, "older_pristine", lambda version: None)      # tests never read the real vault
    w = world["w"]
    _w(w / "app.js", "the owner's OLD tree version of app.js\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "checkpoint: copied")
    rep = verify.verify("v1")
    assert rep["old_tree_copies_undetermined"] == ["app.js"]       # a copy, and no older pristine to judge it by


def test_an_edit_no_patch_asked_for_is_a_stray(world):
    w = world["w"]
    _w(w / "browser" / "other.js", "nobody asked for this\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "checkpoint: stray")
    assert verify.verify("v1")["stray_edits"] == ["browser/other.js"]


def test_the_gate_carries_the_verifier_rows(world, monkeypatch):
    t = task.load("v1")
    t["steps"][0]["status"] = "done"
    task.save(t)
    _w(world["h"] / "config" / "mozconfig.win64", "mk_add_options MOZ_OBJDIR=C:/x\n")
    monkeypatch.setattr(cg, "mozconfig_path", lambda root=None: world["h"] / "config" / "mozconfig.win64")
    rows = {r["check"]: r for r in cg.gate("v1", write=False)}
    assert not rows["tree: every step the record calls done is really in the tree"]["ok"]
    assert not rows["tree: every new file of the patch set is in place"]["ok"]


# -- the new-files step ---------------------------------------------------------------------------

def test_new_files_are_copied_non_source_skipped_and_an_upstream_clash_goes_to_the_owner(world):
    w, h = world["w"], world["h"]
    t = task.load("v1")
    res = firefox.step_new_files(t, str(h), "08.Look")
    assert res["copied"] == ["browser/branding/gorilla/logo.png"]
    assert (w / "browser" / "branding" / "gorilla" / "logo.png").read_bytes() == b"gorilla png"
    assert res["conflicts"] == ["browser/shipped.png"] and (w / "browser" / "shipped.png").read_bytes() == b"upstream png"
    assert res["add_steps"][0]["kind"] == "owner" and "shipped.png" in res["add_steps"][0]["title"]
    res2 = firefox.step_new_files(t, str(h), "08.Look")
    assert res2["copied"] == [] and res2["already"] == ["browser/branding/gorilla/logo.png"]     # idempotent
    res3 = firefox.step_new_files(t, str(h), "05.PREFS")
    assert res3["copied"] == [] and res3["not_source"] == ["mozconfig"]


def test_the_plan_has_a_new_files_step_only_for_groups_that_carry_new_files(world):
    t = task.load("v1")
    ids = [s["id"] for s in firefox.plan_groups(t, str(world["h"]))["add_steps"]]
    assert "new-files-08.Look" in ids and "new-files-05.PREFS" in ids and "new-files-01.MEDIA" not in ids
    assert ids.index("apply-08.Look") < ids.index("new-files-08.Look") < ids.index("export-08.Look")


def test_a_copy_of_the_old_tree_is_harmless_only_when_upstream_did_not_touch_the_file(world, tmp_path, monkeypatch):
    w = world["w"]
    old = tmp_path / "vault-old"
    _w(old / "app.js", "ANOTHER older upstream text\n")                       # upstream changed app.js since: lossy
    _w(old / "browser" / "shipped.png", b"upstream png")                        # unchanged since: harmless
    _w(world["h"] / "src" / "browser" / "shipped.png", b"upstream png")
    monkeypatch.setattr(verify, "older_pristine", lambda version: old)
    _w(w / "app.js", "the owner's OLD tree version of app.js\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "checkpoint: copied")
    rep = verify.verify("v1")
    assert rep["old_tree_copies"] == ["app.js"] and rep["old_tree_copies_undetermined"] == []
    monkeypatch.setattr(verify, "older_pristine", lambda version: None)
    rep = verify.verify("v1")
    assert rep["old_tree_copies"] == [] and rep["old_tree_copies_undetermined"] == ["app.js"]
    assert not dict((n, ok) for n, ok, _ in verify.problems(rep))["tree: no file is a lossy copy of the owner's old tree"]


def test_an_older_task_gets_the_new_files_steps_added_once(world):
    t = task.load("v1")
    t["steps"] += [{"id": "apply-08.Look", "kind": "script", "status": "done", "title": "", "run": "x"},
                   {"id": "export-08.Look", "kind": "script", "status": "pending", "title": "", "run": "x"}]
    task.save(t)
    assert verify.add_missing_new_file_steps("v1") == ["new-files-05.PREFS", "new-files-08.Look"]
    ids = [s["id"] for s in task.load("v1")["steps"]]
    assert ids.index("apply-08.Look") < ids.index("new-files-08.Look") < ids.index("export-08.Look")
    assert verify.add_missing_new_file_steps("v1") == []


def test_a_line_removed_and_re_added_indented_is_not_a_removal_to_check():
    """Real h39: `pref("media.contextmenu.video-overlay-detection", true);` moved into an #ifdef, re-indented."""
    hunk = {"lines": ['-pref("media.contextmenu.video-overlay-detection", true);', "+#ifdef NIGHTLY_BUILD",
                      '+  pref("media.contextmenu.video-overlay-detection", true);', "+#else",
                      '+  pref("media.contextmenu.video-overlay-detection", false);', "+#endif"]}
    body = ["#ifdef NIGHTLY_BUILD", '  pref("media.contextmenu.video-overlay-detection", true);', "#else",
            '  pref("media.contextmenu.video-overlay-detection", false);', "#endif"]
    assert verify.score_hunk(body, hunk)[0] == "APPLIED"


def test_a_pure_removal_hunk_is_applied_when_its_lines_are_gone_from_the_span_even_if_they_live_elsewhere():
    """Live run 14: h15 removed 25 CSS lines; `color: inherit;` and friends exist in other rules, and the
    verifier called the merge 'not in the tree' and reopened it."""
    hunk = {"header": "@@", "lines": [" .toolbox-top-unique-selector {", "-  color: inherit;", "-  background-color: var(--toolbox-background-color);",
                                      "-  padding-inline-end: var(--toolbar-padding-inline);", " }", " ", " .another-unique-selector-after {"]}
    body = [".toolbox-top-unique-selector {", "}", "", ".another-unique-selector-after {", "}", "", ".other-rule {", "  color: inherit;",
            "  background-color: var(--toolbox-background-color);", "  padding-inline-end: var(--toolbar-padding-inline);", "}"]
    assert verify.score_hunk(body, hunk, "browser-shared.css")[0] == "APPLIED"
    not_done = body[:1] + ["  color: inherit;", "  background-color: var(--toolbox-background-color);",
                           "  padding-inline-end: var(--toolbar-padding-inline);"] + body[1:]
    assert verify.score_hunk(not_done, hunk, "browser-shared.css")[0] == "NOT-APPLIED"


def test_a_removed_block_with_only_trivial_context_counts_as_present_only_when_its_lines_still_stand_together():
    """Real moz.build h2: the hunk removes a whole GeneratedFile(...) block between `)` and a blank; its lines
    `script="process_tokens.py",` and `entry_point="process_tokens",` live in every other such block."""
    hunk = {"header": "@@", "lines": [" )", " ", "-GeneratedFile(", '-    "aiwindow-nova/manifest.json",', '-    script="process_tokens.py",',
                                      '-    entry_point="process_tokens",', "-)", " ", " GeneratedFile("]}
    done = [")", "", "GeneratedFile(", '    "other-thing/manifest.json",', '    script="process_tokens.py",',
            '    entry_point="process_tokens",', ")", "", "GeneratedFile(", '    "third/manifest.json",', '    script="process_tokens.py",', ")"]
    assert verify.score_hunk(done, hunk, "moz.build")[0] == "APPLIED"
    not_done = [")", "", "GeneratedFile(", '    "aiwindow-nova/manifest.json",', '    script="process_tokens.py",',
                '    entry_point="process_tokens",', ")", ""] + done[2:]
    assert verify.score_hunk(not_done, hunk, "moz.build")[0] == "NOT-APPLIED"


def test_a_pref_line_with_other_spacing_or_now_locked_is_present():
    from fieldkit.buildh.verify import _locked_variant
    have = {'pref("network.prefetch-next", false, locked);', 'pref("a.b", 3);'}
    assert _locked_variant('pref("network.prefetch-next",                       false);', have)
    assert _locked_variant('pref("a.b",    3);', have)
    assert not _locked_variant('pref("a.b", 4);', have)                    # a different value is not present
    assert not _locked_variant('pref("a.b", 3, locked);', have)            # a lost lock is not accepted


def test_a_port_step_is_superseded_only_by_a_later_hand_step_that_removes_exactly_its_missing_lines():
    from fieldkit.buildh import verify as vf
    port = {"id": "port-a-h1", "status": "done", "args": {"file": "x.css", "hunk": {"lines": ["+keep", "+dead"]}}}
    hand = {"id": "hand-x-h1", "status": "done", "done_by": "hand", "args": {"file": "x.css", "hunk": {"lines": ["-dead"]}}}
    other = {"id": "hand-y-h1", "status": "done", "done_by": "hand", "args": {"file": "x.css", "hunk": {"lines": ["-else"]}}}
    assert vf.superseded_by_hand([port, hand], port, ["keep"]) == "hand-x-h1"
    assert vf.superseded_by_hand([port, other], port, ["keep"]) is None          # a missing line nobody removed
    assert vf.superseded_by_hand([hand, port], port, ["keep"]) is None           # an EARLIER hand step does not count
    assert vf.superseded_by_hand([port, hand], port, ["keep", "dead"]) is None   # nothing missing
