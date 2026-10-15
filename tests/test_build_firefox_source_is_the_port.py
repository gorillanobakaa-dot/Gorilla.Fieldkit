# Version: 1.0.0 · updated 26-10-15-17-56
"""The firefox-linux pipeline builds the PORT's work tree: preflight refuses the pristine copy when a harness task
carries the port, and the patch stages never re-apply the public set over a ported tree (2026-10-15)."""
import json
import subprocess
from types import SimpleNamespace

from fieldkit.build import firefox_linux as fl, firefox_preflight as fp


def _tree(path, commits):
    (path / "browser" / "config").mkdir(parents=True)
    (path / "browser" / "config" / "version.txt").write_text("158.0\n")
    g = lambda *a: subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True)
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine release")
    for i in range(commits):
        (path / f"port{i}.txt").write_text("gorilla")
        g("add", "-A")
        g("commit", "-qm", f"port {i}")
    return path


def _state(tmp_path, work):
    st = tmp_path / "state" / "firefox-158-linux"
    st.mkdir(parents=True)
    (st / "task.json").write_text(json.dumps({"workdir": str(work)}))
    return tmp_path / "state"


def test_task_work_trees_finds_only_ported_trees_of_the_version(tmp_path):
    work, pristine = _tree(tmp_path / "work", 2), _tree(tmp_path / "pristine", 0)
    st = _state(tmp_path, work)
    (st / "other").mkdir()
    (st / "other" / "task.json").write_text(json.dumps({"workdir": str(pristine)}))
    assert fl.task_work_trees("158.0", st) == [("firefox-158-linux", work)]
    assert fl.task_work_trees("159.0", st) == []
    assert fl.ported_commits(work) == 2 and fl.ported_commits(pristine) == 0


def test_preflight_refuses_the_pristine_copy_and_accepts_the_port(tmp_path, monkeypatch):
    work, pristine = _tree(tmp_path / "work", 1), _tree(tmp_path / "pristine", 0)
    st = _state(tmp_path, work)
    monkeypatch.setattr(fl, "task_work_trees", lambda v, state=None, _f=fl.task_work_trees: _f(v, st))
    ok, why = fp._c_source_is_the_port(SimpleNamespace(src=pristine))
    assert not ok and "carries the 158.0 port" in why and str(work) in why
    ok, why = fp._c_source_is_the_port(SimpleNamespace(src=work))
    assert ok and "firefox-158-linux" in why


def test_patch_stages_do_not_reapply_the_set_over_a_ported_tree(tmp_path):
    work = _tree(tmp_path / "work", 3)
    ctx = SimpleNamespace(vars={"harness": str(tmp_path / "h"), "src": str(work)})
    r = fl.stage_patches(ctx, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("apply.sh must not run")))
    assert r["ok"] and r["mode"] == "ported" and "3 commit(s)" in r["detail"]
