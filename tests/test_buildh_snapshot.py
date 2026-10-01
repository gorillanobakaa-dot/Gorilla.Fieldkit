"""snapshot: the truth of a build is the built tree; capture it, prove it reproduces, judge the curated set by it."""
import json
import subprocess

import pytest

from fieldkit.buildh import firefox, snapshot, task, upstream, vault


def git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True, capture_output=True)


def _w(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    # pristine 155.0.1 in a private vault
    up = tmp_path / "mozilla"
    _w(up / "app.js", "line one\nline two\nline three\n")
    _w(up / "keep.css", "a {}\n")
    _w(up / "gone/old.cpp", "int x;\n")
    _w(up / "logo.ico", b"\x00\x01OLD")
    git(up, "init", "-q", "-b", "main")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "155.0.1")
    git(up, "tag", "FIREFOX_155_0_1_RELEASE")
    vb = tmp_path / "vault"
    info = upstream.latest_firefox(versions={"LATEST_FIREFOX_VERSION": "155.0.1"}, repo=up.as_uri())
    vault.fetch_firefox(info, base=vb)
    # the live tree: a clone of pristine with the owner's changes in the working tree
    h = tmp_path / "Gorilla.firefox"
    src = h / "src"
    subprocess.run(["git", "clone", "-q", str(up), str(src)], check=True)
    _w(src / "app.js", "line one\nGORILLA line two\nline three\n")           # modified, named by a curated group
    _w(src / "keep.css", "a {}\nb { color: gorilla; }\n")                      # modified, no curated patch
    (src / "gone" / "old.cpp").unlink()                                        # deleted
    _w(src / "logo.ico", b"\x00\x01NEW")                                       # binary modified
    _w(src / "browser/branding/gorilla/new.png", b"PNG gorilla")               # new, curated NEW_FILES group
    _w(src / "browser/modules/Extra.sys.mjs", "export const x = 1;\n")         # new, no curated group
    _w(src / "app.js.gorilla77", "backup junk\n")                             # junk
    _w(src / "user.js", 'user_pref("a", 1);\n')                                # profile file
    _w(src / "tool.py", "print('owner tool at root')\n")                       # root-level, not source
    _w(h / "config" / "patch_policy.json", json.dumps({"patchset_root": "patchset", "groups": {
        "05.PREFS": {"status": "enabled"}, "08.Look": {"status": "enabled"}, "10.OVERRIDES": {"status": "enabled"}}}))
    _w(h / "patchset" / "05.PREFS" / "app.js.patch",
       "--- a/app.js\n+++ b/app.js\n@@ -1,3 +1,3 @@\n line one\n-line two\n+GORILLA line two\n line three\n")
    _w(h / "patchset" / "05.PREFS" / "dead.patch",
       "--- a/keep.css\n+++ b/keep.css\n@@ -1,1 +1,2 @@\n a {}\n+c { never: landed-in-the-build; }\n")
    _w(h / "patchset" / "08.Look" / "NEW_FILES" / "browser" / "branding" / "gorilla" / "other.png", b"x")
    return {"h": h, "src": src, "vb": vb, "tmp": tmp_path}


def test_capture_lays_the_truth_out_in_the_owners_layout(world):
    m = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "snap", vault_base=world["vb"])
    ps = world["tmp"] / "snap" / "patchset"
    assert (ps / "05.PREFS" / "app.js.patch").is_file()                                  # stays in its curated group
    assert (ps / "05.PREFS" / "keep.css.patch").is_file()        # a file a curated group names stays in that group, even if its patch is dead
    assert (ps / "20.SNAPSHOT.DELTA.155.0.1" / "NEW_FILES").is_dir()                       # unnamed things -> snapshot group
    assert (ps / "20.SNAPSHOT.DELTA.155.0.1" / "DELETED_FILES.manifest.txt").read_text() == "gone/old.cpp\n"
    assert (ps / "20.SNAPSHOT.DELTA.155.0.1" / "REPLACE_FILES" / "logo.ico").read_bytes() == b"\x00\x01NEW"
    assert (ps / "08.Look" / "NEW_FILES" / "browser" / "branding" / "gorilla" / "new.png").is_file()   # curated NEW_FILES dir
    assert (ps / "20.SNAPSHOT.DELTA.155.0.1" / "NEW_FILES" / "browser" / "modules" / "Extra.sys.mjs").is_file()
    assert (ps / "10.OVERRIDES" / "NEW_FILES" / "user.js").is_file()
    assert m["counts"] == {"patches": 2, "new_files": 3, "replaced": 1, "deleted": 1,
                           "excluded": ["app.js.gorilla77", "tool.py  (root-level file, not source)"]}
    assert all(len(v) == 64 for v in m["files"].values())
    pol = json.loads((world["tmp"] / "snap" / "harness" / "config" / "patch_policy.json").read_text())
    assert pol["patchset_root"] == "../patchset" and pol["groups"]["20.SNAPSHOT.DELTA.155.0.1"]["status"] == "enabled"
    assert (world["tmp"] / "snap" / "harness" / "src" / "app.js").is_file()                # junction to the live tree


def test_capture_is_deterministic(world):
    a = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "s1", vault_base=world["vb"])["files"]
    b = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "s2", vault_base=world["vb"])["files"]
    assert a == b


def test_the_proof_rebuilds_the_live_tree_exactly(world):
    m = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "snap", vault_base=world["vb"])
    r = snapshot.prove(m, world["tmp"] / "rebuilt", vault_base=world["vb"])
    assert r["ok"], r
    assert not (world["tmp"] / "rebuilt" / "gone" / "old.cpp").exists()
    assert (world["tmp"] / "rebuilt" / "logo.ico").read_bytes() == b"\x00\x01NEW"
    assert not (world["tmp"] / "rebuilt" / "app.js.gorilla77").exists()


def test_the_proof_fails_when_the_set_is_incomplete(world):
    m = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "snap", vault_base=world["vb"])
    (world["tmp"] / "snap" / "patchset" / "05.PREFS" / "keep.css.patch").unlink()
    r = snapshot.prove(m, world["tmp"] / "rebuilt", vault_base=world["vb"])
    assert not r["ok"] and r["differ"] == ["keep.css"]


def test_a_live_tree_on_another_base_is_refused(world):
    git(world["src"], "commit", "-q", "-am", "the owner committed something, HEAD moved")
    with pytest.raises(task.Refused, match="not the same base"):
        snapshot.capture(world["h"], "155.0.1", world["tmp"] / "snap", vault_base=world["vb"])


def test_the_curated_set_is_judged_by_the_build(world):
    counts, dead = snapshot.compare_curated(world["h"])
    assert counts["05.PREFS"]["APPLIED"] == 1 and counts["05.PREFS"]["NOT-APPLIED"] == 1
    assert len(dead) == 1 and "dead.patch keep.css #1" in dead[0]


def test_the_workflow_plans_delete_and_replace_steps_and_runs_them(world, tmp_path):
    m = snapshot.capture(world["h"], "155.0.1", world["tmp"] / "snap", vault_base=world["vb"])
    hr = m["harness_root"]
    w = tmp_path / "work"
    vault.restore("firefox", w, "155.0.1", base=world["vb"])
    task.start("sn", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "155.0.1"}, "harness_root": hr})
    t = task.load("sn")
    ids = [s["id"] for s in firefox.plan_groups(t, hr)["add_steps"]]
    assert "delete-files-20.SNAPSHOT.DELTA.155.0.1" in ids and "replace-files-20.SNAPSHOT.DELTA.155.0.1" in ids
    g = "20.SNAPSHOT.DELTA.155.0.1"
    assert firefox.step_delete_files(t, hr, g)["removed"] == ["gone/old.cpp"]
    assert firefox.step_delete_files(t, hr, g)["already_gone"] == ["gone/old.cpp"]
    assert firefox.step_replace_files(t, hr, g)["replaced"] == ["logo.ico"]
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "checkpoint: x")
    assert firefox.unexplained_deletions(task.load("sn")) == []          # the manifest explains the deletion
