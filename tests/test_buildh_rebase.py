# Version: 1.0.0 · updated 26-10-15-23-32
"""rebase-set: a patch that no longer applies to the new release is superseded, the rest of the way to the built
tree becomes 23.REBASE.<major>, and replay then gives exactly the built tree (2026-10-15: 77 failing patches on 158)."""
import json
import shutil
import subprocess
import sys

import pytest

from fieldkit.buildh import rebase, replay

pytestmark = pytest.mark.skipif(shutil.which("patch") is None, reason="GNU patch is not installed")


def _repo(tmp_path):
    w = tmp_path / "src"
    w.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                  capture_output=True, text=True).stdout
    (w / "a.txt").write_text("one\ntwo\nthree\n", newline="\n")          # the NEW release moved line two
    (w / "b.txt").write_text("keep\n", newline="\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine 158")
    (w / "a.txt").write_text("one\nTWO GORILLA\nthree\n", newline="\n")    # the port re-made the change by hand
    (w / "b.txt").write_text("keep\ngorilla\n", newline="\n")
    g("commit", "-qam", "ported")
    return w


def _owner(tmp_path):
    o = tmp_path / "owner"
    (o / "config").mkdir(parents=True)
    ps = o / "patches"
    (ps / "01.OLD").mkdir(parents=True)
    # written for the OLD release: its context no longer matches
    (ps / "01.OLD" / "a.patch").write_text("--- a/a.txt\n+++ b/a.txt\n@@ -1,3 +1,3 @@\n one\n-two old\n+TWO GORILLA\n three\n", newline="\n")
    (ps / "01.OLD" / "b.patch").write_text("--- a/b.txt\n+++ b/b.txt\n@@ -1 +1,2 @@\n keep\n+gorilla\n", newline="\n")
    (o / "config" / "patch_policy.json").write_text(json.dumps(
        {"patchset_root": "patches", "groups": {"01.OLD": {"status": "enabled", "reason": "x"}}}), newline="\n")
    return o


def test_the_set_is_carried_onto_the_new_release_and_replay_proves_it(tmp_path):
    w, o = _repo(tmp_path), _owner(tmp_path)
    assert replay.replay(o, w, say=lambda m: None)["failures"]          # before: the old patch fails
    r = rebase.run(o, w, "158", write=True, say=lambda m: None)
    assert r["ok"] and r["superseded"] == 1 and r["patches"] == 1
    pol = json.loads((o / "config" / "patch_policy.json").read_text())
    assert pol["groups"]["01.OLD"]["exclude"] == ["a.patch"] and "exclude_reason_158" in pol["groups"]["01.OLD"]
    assert list(pol["groups"])[-1] == "23.REBASE.158"
    assert (o / "patches" / "01.OLD" / "a.patch").is_file()            # history stays on disk
    assert replay.replay(o, w, say=lambda m: None)["ok"]


def test_a_dry_run_writes_nothing_and_a_second_run_changes_nothing(tmp_path):
    w, o = _repo(tmp_path), _owner(tmp_path)
    before = (o / "config" / "patch_policy.json").read_text()
    assert rebase.run(o, w, "158", say=lambda m: None)["replay"] == "not run (dry)"
    assert (o / "config" / "patch_policy.json").read_text() == before and not (o / "patches" / "23.REBASE.158").exists()
    rebase.run(o, w, "158", write=True, say=lambda m: None)
    first = sorted(p.name for p in (o / "patches" / "23.REBASE.158").iterdir())
    assert rebase.run(o, w, "158", write=True, say=lambda m: None)["ok"]
    assert sorted(p.name for p in (o / "patches" / "23.REBASE.158").iterdir()) == first


def test_failure_lines_name_the_group_and_the_patch():
    got = rebase.failed_patches(["08.Look/sub/x.patch: 1 out of 2 hunks FAILED", "22.E/y.patch: failed", "noise"])
    assert got == {"08.Look": {"x.patch"}, "22.E": {"y.patch"}}


def test_the_policy_chooses_the_patches_of_the_platform_it_runs_on():
    from fieldkit.buildh import firefox
    groups = {"01.MEDIA": {"status": "enabled", "platforms": ["linux"]},
              "02.GPU": {"status": "enabled", "exclude_on": {"windows": ["gtk.patch"]}, "exclude": ["old.patch"]},
              "05.PREFS": {"status": "enabled"}}
    win = firefox.for_platform(groups, "windows")
    lin = firefox.for_platform(groups, "linux")
    assert win["01.MEDIA"]["status"].startswith("disabled on windows") and lin["01.MEDIA"]["status"] == "enabled"
    assert win["02.GPU"]["exclude"] == ["gtk.patch", "old.patch"] and lin["02.GPU"]["exclude"] == ["old.patch"]
    assert win["05.PREFS"] == lin["05.PREFS"] == {"status": "enabled"}
    assert groups["01.MEDIA"]["status"] == "enabled"                     # reading never changes the policy
    assert firefox.this_platform() in ("linux", "windows")


@pytest.mark.skipif(sys.platform == "win32", reason="the case is a Linux-only group: on Windows for_platform leaves it "
                    "out by design and the rebase group is .windows (that choice is tested on its own)")
def test_a_file_a_linux_only_group_touches_gets_a_linux_only_rebase_patch(tmp_path):
    w, o = _repo(tmp_path), _owner(tmp_path)
    pol = json.loads((o / "config" / "patch_policy.json").read_text())
    (o / "patches" / "02.LINUX").mkdir()
    (o / "patches" / "02.LINUX" / "b.patch").write_text((o / "patches" / "01.OLD" / "b.patch").read_text(), newline="\n")
    (o / "patches" / "01.OLD" / "b.patch").unlink()
    pol["groups"]["02.LINUX"] = {"status": "enabled", "platforms": ["linux"]}
    (o / "config" / "patch_policy.json").write_text(json.dumps(pol), newline="\n")
    (w / "b.txt").write_text("keep\ngorilla\nmore on 158\n", newline="\n")              # the port changed b.txt further
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "more"], check=True)
    r = rebase.run(o, w, "158", write=True, say=lambda m: None)
    assert r["ok"] and r["patches"] == 1 and r["platform_patches"] == 1           # a.txt shared, b.txt linux only
    pol = json.loads((o / "config" / "patch_policy.json").read_text())
    assert list(pol["groups"])[-2:] == ["23.REBASE.158", "23.REBASE.158.linux"]
    assert pol["groups"]["23.REBASE.158.linux"]["platforms"] == ["linux"]
    assert list((o / "patches" / "23.REBASE.158.linux").glob("*-b_txt.patch"))
    assert list((o / "patches" / "23.REBASE.158").glob("*-a_txt.patch"))


def test_a_renamed_fluent_message_s_gorilla_wording_is_found_on_its_successor(tmp_path):
    w = tmp_path / "src"
    w.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                  capture_output=True, text=True).stdout
    (w / "b.ftl").write_text("tabs4 = Tabs\nkeep = Same\n", newline="\n")             # 158 renamed tabs3 to tabs4
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine")
    o = tmp_path / "owner"
    (o / "config").mkdir(parents=True)
    (o / "patches" / "08.Look").mkdir(parents=True)
    (o / "patches" / "08.Look" / "b.patch").write_text(
        "--- a/b.ftl\n+++ b/b.ftl\n@@ -1,2 +1,2 @@\n-tabs3 = Tabs\n+tabs3 = Gorilla Tabs\n keep = Same\n", newline="\n")
    (o / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "patches", "groups": {
        "08.Look": {"status": "enabled"}}}), newline="\n")
    moves = rebase.fluent_moves(o, w, {"08.Look": {"b.patch"}}, "158")
    assert [(m["id"], m["to"]) for m in moves] == [("tabs3", "tabs4")]
    (w / "b.ftl").write_text("tabs4 = Gorilla Tabs\nkeep = Same\n", newline="\n")
    g("commit", "-qam", "carried")
    assert rebase.fluent_moves(o, w, {"08.Look": {"b.patch"}}, "158") == []
    assert rebase.fluent_moves(o, w, {"21.PORT.FIXES.158": {"b.patch"}}, "158") == []   # own release: never judged
