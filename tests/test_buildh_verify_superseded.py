"""verify after later hand edits (2026-10-08, build 28's gate refused 7 false completions that were not): restoring the
155-era design-token files to pristine 157 replaced older steps on purpose. A step is SUPERSEDED when later hand steps
on its file together undo it, or when it held just before the first later hand edit of its file; a hand step's removals
are judged against the file just before that edit as well as pristine; record keeps each hand step's commit."""
import os
import subprocess

from fieldkit.buildh import firefox, verify


def _git(w, *a, env=None):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(w), *a], check=True, capture_output=True,
                          text=True, env=env).stdout


def _commit(w, msg, when):
    env = {**os.environ, "GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000"}
    _git(w, "add", "-A")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg, env=env)
    return _git(w, "rev-parse", "HEAD").strip()


def _hunk(lines):
    return {"lines": lines, "header": "@@ -1,3 +1,3 @@"}


def test_later_hand_steps_together_supersede_a_step():
    step = {"id": "port-a-h1", "status": "done", "args": {"file": "t.css", "hunk": _hunk(["+--a: 1;", "+--b: 2;", "-old: x;"])}}
    later1 = {"id": "hand-x-h1", "status": "done", "done_by": "hand", "args": {"file": "t.css", "hunk": _hunk(["---a: 1;"])}}
    later2 = {"id": "hand-x-h2", "status": "done", "done_by": "hand", "args": {"file": "t.css", "hunk": _hunk(["---b: 2;", "+old: x;"])}}
    now = ["old: x;", "--c: 3;"]
    assert verify.superseded_by_hand([step, later1, later2], step, now) == "hand-x-h2"
    assert verify.superseded_by_hand([step, later1], step, now) is None           # --b and old: x not explained


def test_the_commit_of_an_older_hand_step_is_found_by_its_time(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    (w / "t.css").write_bytes(b"a\n")
    _git(w, "init", "-q")
    _commit(w, "pristine", 1_791_300_000)
    (w / "t.css").write_bytes(b"b\n")
    c = _commit(w, "checkpoint: hand edit (hand): t.css", 1_791_300_100)
    import datetime
    stamp = datetime.datetime.fromtimestamp(1_791_300_099).strftime("%Y%m%d-%H%M%S")
    step = {"id": f"hand-hand-t.css-{stamp}-abcdef-h1", "args": {"file": "t.css"}}
    assert verify.hand_step_commit(w, step, {}) == c
    assert verify.hand_step_commit(w, {"id": "x", "commit": "deadbeef", "args": {}}, {}) == "deadbeef"


def test_a_step_that_held_until_a_later_hand_edit_is_superseded(tmp_path):
    w = tmp_path / "tree"
    w.mkdir()
    (w / "t.css").write_bytes(b"one\ntwo\nthree\n")
    _git(w, "init", "-q")
    root = _commit(w, "pristine", 1_791_300_000)
    (w / "t.css").write_bytes(b"one\ntwo\nadded-by-port: distinctive_token_here;\nthree\n")
    _commit(w, "checkpoint: port", 1_791_300_050)
    hunk = firefox.parse_patch(_git(w, "diff", root, "HEAD"))[0]["hunks"][0]
    port = {"id": "port-g-t.css-h1", "status": "done", "args": {"file": "t.css", "hunk": hunk}}
    (w / "t.css").write_bytes(b"one\ntwo\nthree\n")                               # a later hand edit restores pristine
    c = _commit(w, "checkpoint: hand edit (hand): t.css", 1_791_300_100)
    hand = {"id": "hand-hand-t.css-x-h1", "status": "done", "done_by": "hand", "commit": c,
            "args": {"file": "t.css", "hunk": _hunk(["-added-by-port: distinctive_token_here;"])}}
    assert verify.held_until_later_hand_edit(w, [port, hand], port, root) == "hand-hand-t.css-x-h1"
    assert verify.held_until_later_hand_edit(w, [port], port, root) is None        # no later hand edit: still false
