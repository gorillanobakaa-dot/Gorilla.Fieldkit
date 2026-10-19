"""fieldkit adopt: every spelling of a Scripts path is repointed, nothing else is; a copy that differs or a failing
test rolls everything back; a folder in use keeps its old copy with a marker."""
import json
import os
from pathlib import Path

import pytest

from conftest import needs_symlinks
from fieldkit.desk import adopt


def test_every_spelling_of_the_old_place_is_repointed():
    text = "\n".join([
        'entry: [python, "${DOCUMENTS}/Scripts/NetTriage/nettriage.py"]',
        r"C:\Users\x\Documents\Scripts\NetTriage\nettriage.py",  # privacy-scan: allow (deliberate fixture)
        "~/Documents/Scripts/NetTriage",
        'p = Path.home() / "Documents" / "Scripts" / "NetTriage" / "a.py"',
        "Scripts/NetTriageX/not-this-one",
    ])
    out, n = adopt.rewrite(text, ["NetTriage"])
    lines = out.splitlines()
    assert n == 4
    assert lines[0] == 'entry: [python, "${FIELDKIT}/local/kits/NetTriage/nettriage.py"]'
    assert lines[1] == r"C:\Users\x\Documents\Scripts\Fieldkit\local\kits\NetTriage\nettriage.py"  # privacy-scan: allow
    assert lines[2] == "~/Documents/Scripts/Fieldkit/local/kits/NetTriage"
    assert lines[3] == 'p = Path.home() / "Documents" / "Scripts" / "Fieldkit" / "local" / "kits" / "NetTriage" / "a.py"'
    assert lines[4] == "Scripts/NetTriageX/not-this-one"


def test_a_loose_file_goes_into_its_group_folder():
    out, n = adopt.rewrite(r"Scripts\organize.py and ${DOCUMENTS}/Scripts/organize.py", ["organize.py"],
                           {"organize.py": "ScriptsTidy/organize.py"})
    assert n == 2
    assert out == r"Scripts\Fieldkit\local\kits\ScriptsTidy\organize.py and ${FIELDKIT}/local/kits/ScriptsTidy/organize.py"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A Scripts dumping ground, a Fieldkit with a local card, a skill and a memory file that name the tool."""
    scripts, fk = tmp_path / "Scripts", tmp_path / "Fieldkit"
    (scripts / "Tool").mkdir(parents=True)
    (scripts / "Tool" / "tool.py").write_text("print('hi')\n", encoding="utf-8")
    (fk / "local" / "kits").mkdir(parents=True)
    (fk / "local" / "tools.yaml").write_text(
        "tools:\n- id: tool\n  path: ${DOCUMENTS}/Scripts/Tool/tool.py\n", encoding="utf-8")
    skill = tmp_path / "skills" / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"Run Documents\\Scripts\\Tool\\tool.py\r\n")       # CRLF must survive
    monkeypatch.setattr(adopt, "scripts_root", lambda: scripts)
    monkeypatch.setattr(adopt, "kits_root", lambda: fk / "local" / "kits")
    monkeypatch.setattr(adopt, "text_roots", lambda: [skill.parent.parent])
    monkeypatch.setattr(adopt, "shortcut_hits", lambda olds: [])
    monkeypatch.setattr(adopt, "running_from", lambda paths: [])
    monkeypatch.setattr(adopt, "public_hits", lambda names: [])
    monkeypatch.setattr(adopt.settings, "ROOT", fk)
    # a stand-in Recycle Bin: tests never send anything to the real one
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    def recycle(path):
        import shutil as sh
        sh.move(str(path), str(fake_bin / Path(path).name))
        return True, "found in the bin's own records (test bin)"
    monkeypatch.setattr(adopt, "_recycle", recycle)
    return {"scripts": scripts, "fk": fk, "skill": skill, "bin": fake_bin}


def test_plan_then_apply_moves_and_repoints(world):
    p = adopt.plan(["Tool"])
    assert p["ok"] and p["cards"] == 1 and str(world["skill"]) in p["text"]
    r = adopt.apply(p, run_tests=False, log=lambda *a: None)
    assert r["ok"], r
    assert (world["fk"] / "local" / "kits" / "Tool" / "tool.py").is_file()
    assert (world["scripts"] / "Tool" / adopt.MARKER).is_file(), "the old copy stays, marked, until --finish"
    assert "${FIELDKIT}/local/kits/Tool/tool.py" in (world["fk"] / "local" / "tools.yaml").read_text(encoding="utf-8")
    assert world["skill"].read_bytes() == b"Run Documents\\Scripts\\Fieldkit\\local\\kits\\Tool\\tool.py\r\n"
    assert any(Path(r["backup"]).iterdir())


def test_a_file_inside_the_tool_that_names_its_own_old_place_is_fixed_in_the_copy(world):
    cmd = world["scripts"] / "Tool" / "run.cmd"
    cmd.write_bytes(b"@python C:\\Users\\x\\Documents\\Scripts\\Tool\\tool.py %*\r\n")  # privacy-scan: allow
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda *a: None)
    assert r["ok"], r
    moved = world["fk"] / "local" / "kits" / "Tool" / "run.cmd"
    assert moved.read_bytes() == b"@python C:\\Users\\x\\Documents\\Scripts\\Fieldkit\\local\\kits\\Tool\\tool.py %*\r\n"  # privacy-scan: allow


def test_a_tool_in_use_keeps_its_old_copy_with_a_marker(world, monkeypatch):
    monkeypatch.setattr(adopt, "running_from", lambda paths: [(1234, "pythonw tool.py")])
    p = adopt.plan(["Tool"])
    r = adopt.apply(p, run_tests=False, log=lambda *a: None)
    assert r["ok"] and r["running"] == [1234]
    assert (world["scripts"] / "Tool" / adopt.MARKER).is_file()
    assert (world["fk"] / "local" / "kits" / "Tool" / "tool.py").is_file()


def test_a_bad_copy_rolls_everything_back(world, monkeypatch):
    monkeypatch.setattr(adopt, "_same_tree", lambda a, b: False)
    before = (world["fk"] / "local" / "tools.yaml").read_bytes()
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda *a: None)
    assert not r["ok"] and "differs" in r["error"]
    assert (world["scripts"] / "Tool" / "tool.py").is_file()
    assert not (world["fk"] / "local" / "kits" / "Tool").exists()
    assert (world["fk"] / "local" / "tools.yaml").read_bytes() == before


def test_a_copy_that_fails_part_way_leaves_no_destination_and_a_retry_succeeds(world, monkeypatch):
    """2026-10-14: a root-owned log and a dangling symlink each made the copy raise part-way; the partial copy
    stayed in local/kits and the retry refused with 'already in local/kits'."""
    import shutil as _shutil
    real = _shutil.copytree

    def half_copy(a, b, **kw):
        Path(b).mkdir(parents=True)
        (Path(b) / "tool.py").write_text("partial", encoding="utf-8")
        raise PermissionError(13, "Permission denied", str(Path(a) / "secret.log"))

    monkeypatch.setattr(adopt.shutil, "copytree", half_copy)
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda *a: None)
    assert not r["ok"] and "Permission denied" in r["error"]
    assert not (world["fk"] / "local" / "kits" / "Tool").exists(), "the partial copy must be gone"
    assert (world["scripts"] / "Tool" / "tool.py").is_file()
    monkeypatch.setattr(adopt.shutil, "copytree", real)
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda *a: None)
    assert r["ok"], r


@needs_symlinks
def test_a_dangling_symlink_inside_the_tool_is_copied_as_a_link(world):
    link = world["scripts"] / "Tool" / "symlink-broken"
    link.symlink_to("does-not-exist")
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda *a: None)
    assert r["ok"], r
    moved = world["fk"] / "local" / "kits" / "Tool" / "symlink-broken"
    assert moved.is_symlink() and os.readlink(moved) == "does-not-exist"


def test_refusals(world):
    assert not adopt.plan(["Nope"])["ok"]
    (world["scripts"] / "loose.py").write_text("x = 1\n", encoding="utf-8")
    assert "--into" in adopt.plan(["loose.py"])["error"]
    assert adopt.plan(["loose.py"], into="Loose")["ok"]


def test_another_git_working_tree_under_scripts_is_listed_never_edited(world, monkeypatch):
    # 2026-10-19: a Fieldkit worktree under Documents\Scripts was walked as plain text and --apply rewrote the fixture
    # in its tests/test_adopt.py; code in any repository changes by a reviewed commit, so it is listed, not edited
    import subprocess
    wt = world["scripts"] / "Fieldkit-wave0"
    wt.mkdir()
    fixture = wt / "test_x.py"
    fixture.write_text('OLD = "${DOCUMENTS}/Scripts/Tool/tool.py"\n', encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t"}
    import os
    g = lambda *a: subprocess.run(["git", "-C", str(wt), *a], check=True, capture_output=True, env={**os.environ, **env})
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "x")
    monkeypatch.setattr(adopt, "text_roots", lambda: [world["skill"].parent.parent, world["scripts"]])
    monkeypatch.setattr(adopt, "public_hits", lambda names: [])
    p = adopt.plan(["Tool"])
    assert p["ok"], p
    assert not any("Fieldkit-wave0" in t for t in p["text"]), p["text"]
    assert any(str(fixture) == h for h in p["public"]), p["public"]
    assert adopt.apply(p, run_tests=False, log=lambda m: None)["ok"]
    assert fixture.read_text(encoding="utf-8") == 'OLD = "${DOCUMENTS}/Scripts/Tool/tool.py"\n'


def test_apply_only_copies_and_finish_recycles_never_deletes(world, monkeypatch):
    # 2026-10-19: apply recycled the old Elevate at once while the leak gate's code still named it. Now apply copies
    # and marks; --finish recycles (never rmtree/unlink: the bin, verified).
    import shutil as sh
    monkeypatch.setattr(sh, "rmtree", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rmtree called")))
    r = adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda m: None)
    assert r["ok"] and r["old_copy_marked"] == [str(world["scripts"] / "Tool")], r
    assert (world["scripts"] / "Tool" / "tool.py").is_file() and not (world["bin"] / "Tool").exists()
    out = adopt.finish(do_apply=True)
    assert out[0]["removed"], out
    assert not (world["scripts"] / "Tool").exists() and (world["bin"] / "Tool" / "tool.py").is_file()


def test_finish_keeps_an_old_copy_that_code_still_names(world, monkeypatch):
    adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda m: None)
    monkeypatch.setattr(adopt, "public_hits", lambda names: ["fieldkit/leakgate/sensors.py"])
    out = adopt.finish(do_apply=True)
    assert not out[0]["removed"] and out[0]["still_named_by"] == ["fieldkit/leakgate/sensors.py"], out
    assert (world["scripts"] / "Tool" / "tool.py").is_file()


def test_finish_keeps_a_copy_in_use_and_one_the_bin_refused(world, monkeypatch):
    adopt.apply(adopt.plan(["Tool"]), run_tests=False, log=lambda m: None)
    monkeypatch.setattr(adopt, "running_from", lambda paths: [(1, "busy")])
    assert not adopt.finish(do_apply=True)[0]["removed"]
    monkeypatch.setattr(adopt, "running_from", lambda paths: [])
    monkeypatch.setattr(adopt, "_recycle", lambda path: (False, "no bin here"))
    assert not adopt.finish(do_apply=True)[0]["removed"]
    assert (world["scripts"] / "Tool" / "tool.py").is_file()


def test_a_loose_file_is_marked_beside_itself_and_finish_takes_both(world):
    loose = world["scripts"] / "loose.py"
    loose.write_text("x = 1\n", encoding="utf-8")
    r = adopt.apply(adopt.plan(["loose.py"], into="Loose"), run_tests=False, log=lambda m: None)
    assert r["ok"], r
    assert loose.is_file() and (world["scripts"] / ("loose.py" + adopt.MARKER)).is_file()
    out = adopt.finish(do_apply=True)
    assert [o["removed"] for o in out] == [True], out
    assert not loose.exists() and not (world["scripts"] / ("loose.py" + adopt.MARKER)).exists()

def test_without_gorilla_size_nothing_is_removed(monkeypatch, tmp_path):
    from fieldkit.desk import size
    monkeypatch.setattr(size, "locate", lambda: None)
    d = tmp_path / "old"
    d.mkdir()
    real = adopt.__dict__.get("_recycle")
    ok, why = real(d) if real.__module__ == adopt.__name__ else (False, "")
    assert not ok and "nothing removed" in why and d.is_dir()
