"""Every green build-run turns its hand steps into public patches and proves the set (owner, 2026-10-07). Export and
replay are faked; what is tested is the order, the inputs, and that a failed replay makes the build NOT OK."""
import json

import pytest

from fieldkit.buildh import buildrun, changecheck, export, replay


@pytest.fixture
def owner(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches"}),
                                                          encoding="utf-8")
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    return tmp_path


T = {"meta": {"upstream": {"version": "157.0"}}}


def test_export_runs_before_replay_with_the_patch_set_and_major(owner, monkeypatch):
    calls = []
    monkeypatch.setattr(export, "export", lambda tid, root, major, say=print: calls.append(("export", tid, root, major)))
    monkeypatch.setattr(replay, "run", lambda tid, say=print: calls.append(("replay", tid)) or
                        {"ok": True, "failures": [], "differ": []})
    monkeypatch.setattr(changecheck, "commit_patches", lambda owner, msg, say=print: calls.append(("commit",)) or (True, "committed"))
    r = buildrun.capture_patches(T, "task-x", say=lambda m: None)
    assert r == {"replay": "OK", "commit": (True, "committed")}
    assert calls == [("export", "task-x", owner / "gorilla-patchset/patches", "157"), ("replay", "task-x"), ("commit",)]


def test_a_set_that_does_not_reproduce_the_tree_makes_the_build_not_ok(owner, monkeypatch):
    monkeypatch.setattr(export, "export", lambda *a, **k: None)
    monkeypatch.setattr(replay, "run", lambda tid, say=print: {"ok": False, "failures": ["x.patch: hunk FAILED"],
                                                                "differ": ["M\tbrowser/app/profile/firefox.js"]})
    r = buildrun.capture_patches(T, "task-x", say=lambda m: None)
    assert r["ok"] is False and r["replay"] == "FAILED"
    assert "1 failure(s), 1 differing path(s)" in r["why"]


def test_an_export_crash_is_not_swallowed(owner, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("stale copy of a renamed patch")
    monkeypatch.setattr(export, "export", boom)
    monkeypatch.setattr(replay, "run", lambda *a, **k: pytest.fail("replay must not run after a failed export"))
    with pytest.raises(RuntimeError):
        buildrun.capture_patches(T, "task-x", say=lambda m: None)
