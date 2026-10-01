"""Owner checks that can only pass once an objdir exists (package manifest, l10n resources, bundled extensions,
objdir-vs-CLOBBER) are measured AFTER the build. Before it they are deferred steps that do not hold the gate; the
build loop passes --force to the owner's stage for them and for nothing else; build-verify re-runs the owner's
preflight and closes them when it passes (live run 16: four such steps held the gate for an objdir that did not exist).
"""
from fieldkit.buildh import ownercheck

TEXT = """[-] Bundled extensions are visible (BLOCKER): bundled extension misconfigured: ublock-origin: not in the build output
[*]       fix : build first
[-] Lazy imports resolve (BLOCKER): 4 undeclared lazy symbol use(s)
[*]       fix : run the checker
"""


def test_post_build_blockers_are_deferred_not_blocking(monkeypatch):
    monkeypatch.setattr(ownercheck, "run_preflight", lambda root, python=None: (1, TEXT))
    t = {"steps": [{"id": "owner-preflight-objdir-vs-clobber", "kind": "owner", "status": "blocked", "title": "old"}],
         "meta": {"harness_root": "h"}}
    r = ownercheck.step_owner_preflight(t, owner_root="x")
    by = {s["id"]: s for s in r["add_steps"]}
    assert by["owner-preflight-bundled-extensions-are-visible"]["status"] == "deferred"
    assert by["owner-preflight-bundled-extensions-are-visible"]["post_build"] is True
    assert "after the build" in by["owner-preflight-bundled-extensions-are-visible"]["title"]
    assert "status" not in by["owner-preflight-lazy-imports-resolve"]           # a hard blocker: the owner's, now
    # the earlier objdir-vs-CLOBBER step, no longer reported, closes as obsolete
    assert t["steps"][0]["status"] == "obsolete" and r["cleared"] == ["owner-preflight-objdir-vs-clobber"]
    # an earlier blocked step for a post-build check becomes deferred when reported again
    t = {"steps": [{"id": "owner-preflight-bundled-extensions-are-visible", "kind": "owner", "status": "blocked", "title": "old"}],
         "meta": {"harness_root": "h"}}
    r = ownercheck.step_owner_preflight(t, owner_root="x")
    assert r["add_steps"] == [] or all(s["id"] != "owner-preflight-bundled-extensions-are-visible" for s in r["add_steps"])
    assert t["steps"][0]["status"] == "deferred" and t["steps"][0]["post_build"] is True


def test_the_gate_lists_post_build_steps_instead_of_failing(tmp_path, monkeypatch):
    from fieldkit.buildh import compile as cg, task
    import json
    t = {"id": "pb", "workflow": "firefox-upgrade", "workdir": str(tmp_path), "approved": True, "checkpoints": [],
         "meta": {"upstream": {"version": "1"}},
         "steps": [{"id": "a", "kind": "script", "status": "done"},
                   {"id": "owner-preflight-bundled-extensions-are-visible", "kind": "owner", "status": "deferred", "post_build": True}]}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "verify_journal", lambda tid: ([], 0, None))
    monkeypatch.setattr(cg, "_git", lambda wd, *a: "")
    from fieldkit.buildh import audit, verify as vf, firefox
    monkeypatch.setattr(audit, "journal_checks", lambda ev: [])
    monkeypatch.setattr(vf, "verify", lambda tid: {"false_completions": [], "old_tree_copies": [], "stray_edits": [],
                                                   "missing_new_files": [], "unexplained_deletions": [], "problems": []})
    monkeypatch.setattr(firefox, "leftovers", lambda wd: [])
    monkeypatch.setattr(firefox, "unexplained_deletions", lambda t: [])
    rows = {r["check"]: r for r in cg.gate("pb", harness_root=str(tmp_path), write=False)}
    assert rows["every step is done"]["ok"]
    assert "bundled-extensions-are-visible" in rows["owner checks that need an objdir are listed for build-verify (not a reason to hold the gate)"]["evidence"]
