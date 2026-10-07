"""check-change, the check after every source change (2026-10-07): fail closed on an unrecorded edit and a broken
decision, judge prefs on the source tree, and commit the public patch set only under the published identity."""
import json
import subprocess

import pytest

from fieldkit.buildh import buildrun, changecheck, decisions, firefox, proof, task, techniques, uicheck


def _git(w, *a):
    return subprocess.run(["git", "-C", str(w), *a], capture_output=True, text=True, check=True).stdout


# -- prefs from the source tree --------------------------------------------------------------------------------

def _tree(tmp_path, firefox_js):
    p = tmp_path / "browser/app/profile/firefox.js"
    p.parent.mkdir(parents=True)
    p.write_text(firefox_js, encoding="utf-8")
    (tmp_path / "modules/libpref/init").mkdir(parents=True)
    (tmp_path / "modules/libpref/init/all.js").write_text('pref("network.preconnect", true);\n', encoding="utf-8")
    return tmp_path


def test_a_tree_pref_overridden_by_firefox_js_is_read_last_wins(tmp_path):
    w = _tree(tmp_path, 'pref("network.preconnect", false, locked);\n')
    ok, what = decisions._check("pref", {"name": "network.preconnect", "value": False, "locked": True},
                                {"owner": None, "workdir": str(w), "install": None, "source_prefs": True})
    assert ok and "locked" in what


def test_a_tree_pref_with_the_wrong_value_or_no_lock_is_a_violation(tmp_path):
    w = _tree(tmp_path, 'pref("network.preconnect", false);\n')
    ok, _ = decisions._check("pref", {"name": "network.preconnect", "value": False, "locked": True},
                             {"owner": None, "workdir": str(w), "install": None, "source_prefs": True})
    assert not ok


def test_a_pref_under_if_is_left_to_the_install(tmp_path):
    w = _tree(tmp_path, '#ifdef XP_WIN\npref("media.gmp-widevinecdm.enabled", false, locked);\n#endif\n')
    with pytest.raises(decisions.Unreadable):
        decisions._check("pref", {"name": "media.gmp-widevinecdm.enabled", "value": False},
                         {"owner": None, "workdir": str(w), "install": None, "source_prefs": True})
    assert proof.tree_prefs(w)["media.gmp-widevinecdm.enabled"][2] is True


def test_without_the_opt_in_a_tree_pref_is_never_judged(tmp_path):
    w = _tree(tmp_path, 'pref("network.preconnect", false, locked);\n')
    with pytest.raises(decisions.Unreadable):
        decisions._check("pref", {"name": "network.preconnect", "value": False},
                         {"owner": None, "workdir": str(w), "install": None})


# -- the run, with the tree and the owner faked ----------------------------------------------------------------

@pytest.fixture
def world(tmp_path, monkeypatch):
    wd = tmp_path / "tree"
    wd.mkdir()
    _git(wd, "init", "-q")
    (wd / "a.txt").write_text("x\n", encoding="utf-8")
    _git(wd, "add", "a.txt")
    _git(wd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "p")
    t = {"workdir": str(wd), "meta": {"upstream": {"version": "157.0"}}}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "verify_journal", lambda tid: ([], 5, None))
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    monkeypatch.setattr(firefox, "leftovers", lambda w: [])
    monkeypatch.setattr(firefox, "unexplained_deletions", lambda t: [])
    monkeypatch.setattr(uicheck, "gate_rows", lambda w: [("UI rule", True, "fine")])
    monkeypatch.setattr(techniques, "gate_rows", lambda w: [("technique", True, "holds")])
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    verdict = {"v": "ENFORCED"}
    monkeypatch.setattr(decisions, "check", lambda owner, workdir=None, source_prefs=False: {"problems": [], "rows": [
        {"id": "D-1", "verdict": verdict["v"], "evidence": ["FAIL pref: network.preconnect = true (want false locked)"]},
        {"id": "D-2", "verdict": "UNCHECKABLE", "evidence": ["installed_absent: cannot check"]}]})
    captured = []
    monkeypatch.setattr(buildrun, "capture_patches", lambda t, tid, say=print, note=None: captured.append(tid) or
                        {"replay": "OK", "commit": (True, "nothing new to commit")})
    return wd, verdict, captured


def test_a_clean_recorded_tree_passes_and_lists_what_waits_for_the_install(world):
    _, _, captured = world
    r = changecheck.run("task-x", say=lambda m: None)
    assert r["ok"] and captured == ["task-x"] and r["after_install"] == ["D-2"]


def test_an_unrecorded_edit_fails_and_nothing_is_exported(world):
    wd, _, captured = world
    (wd / "a.txt").write_text("edited by hand\n", encoding="utf-8")
    r = changecheck.run("task-x", say=lambda m: None)
    assert not r["ok"] and captured == []
    assert any("unrecorded" in x["evidence"] for x in r["rows"] if not x["ok"])


def test_a_violated_decision_fails_the_check(world):
    _, verdict, _ = world
    verdict["v"] = "VIOLATED"
    r = changecheck.run("task-x", say=lambda m: None)
    bad = [x for x in r["rows"] if not x["ok"]]
    assert not r["ok"] and bad[0]["check"].startswith("decision register") and "D-1" in bad[0]["evidence"]


# -- the public identity ---------------------------------------------------------------------------------------

def test_patches_are_committed_under_the_published_identity_not_the_local_config(tmp_path):
    up = tmp_path / "up.git"
    _git(tmp_path, "init", "-q", "--bare", str(up))
    repo = tmp_path / "owner" / "gorilla-patchset"
    _git(tmp_path, "clone", "-q", str(up), str(repo))
    (repo / "patches").mkdir()
    (repo / "patches" / "README.md").write_text("set\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Public Name", "-c", "user.email=public@example.com", "commit", "-q", "-m", "first")
    _git(repo, "push", "-q", "origin", "HEAD")
    _git(repo, "branch", "-q", "--set-upstream-to", "origin/" + _git(repo, "branch", "--show-current").strip())
    _git(repo, "config", "user.email", "the-owners-private-address")                     # the trap of 2026-10-07
    (tmp_path / "owner" / "config").mkdir()
    (tmp_path / "owner" / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches"}),
                                                                    encoding="utf-8")
    (repo / "patches" / "001-new.patch").write_text("--- a/x\n+++ b/x\n", encoding="utf-8")
    ok, ev = changecheck.commit_patches(tmp_path / "owner", "157: exported", say=lambda m: None)
    assert ok and "public@example.com" in ev
    assert _git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>").strip() == \
        "Public Name <public@example.com>|Public Name <public@example.com>"
    assert changecheck.commit_patches(tmp_path / "owner", "again", say=lambda m: None) == (True, "nothing new to commit")
