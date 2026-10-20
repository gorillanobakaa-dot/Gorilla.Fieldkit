"""export-hand writes only what is its own (2026-10-18, the overnight 158 export): no lists-refresh output, no change
the set already carries, one file per step, another writer's manifest lines and README lines kept, a duplicate found
in either group, the mark found past a long header, and a file a privacy patch edits keeps the step in privacy."""
import json

from fieldkit.buildh import export, task


def _setup(tmp_path, monkeypatch, commits, diffs):
    root = tmp_path / "patches"
    port, priv = root / "21.PORT.FIXES.158", root / "22.EGRESS.LOCKDOWN.158"
    port.mkdir(parents=True)
    priv.mkdir()
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    (tmp_path / "state" / "t").mkdir(parents=True)
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    monkeypatch.setattr(export, "hand_commits", lambda t: commits)

    def git(w, *a):
        if "--name-only" in a or "ls-tree" in a:
            return ""
        c = next(x for x in a if x in diffs)
        return diffs[c]
    monkeypatch.setattr(export, "_git", git)
    return root, port, priv


def test_two_steps_with_one_note_get_two_files(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch,
                              [("c1", ["a.js"], "same note", "now", "port"), ("c2", ["b.js"], "same note", "now", "port")],
                              {"c1": "diff --git a/a.js b/a.js\n+1\n", "c2": "diff --git a/b.js b/b.js\n+2\n"})
    export.export("t", root, "158", say=lambda *_: None)
    assert sorted(p.name for p in port.glob("*.patch")) == ["001-same-note.patch", "002-same-note.patch"]


def test_a_change_already_in_the_other_group_is_not_written(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch, [("c1", ["a.js"], "cut it", "now", "privacy")],
                              {"c1": "diff --git a/a.js b/a.js\n+1\n"})
    (port / "007-someone.patch").write_text("# other\n\ndiff --git a/a.js b/a.js\n+1\n", encoding="utf-8")
    export.export("t", root, "158", say=lambda *_: None)
    assert list(priv.glob("*.patch")) == []


def test_other_writers_manifest_and_readme_lines_stay(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch, [("c1", ["a.js"], "mine", "now", "privacy")],
                              {"c1": "diff --git a/a.js b/a.js\n+1\n"})
    (priv / "DELETED_FILES.manifest.txt").write_text("linux/gone.ftl\n", encoding="utf-8")
    (priv / "005-linux.patch").write_text("# Gorilla 158 privacy cut\n# linux note\n\ndiff --git a/l b/l\n", encoding="utf-8")
    (priv / "README.md").write_text("# x\n\n- `005-linux.patch`: the Linux cut, in words\n", encoding="utf-8")
    (tmp_path / "state" / "t" / "exported-deletions.json").write_text(json.dumps({priv.name: ["old/mine.css"]}))
    export.export("t", root, "158", say=lambda *_: None)
    assert (priv / "DELETED_FILES.manifest.txt").read_text(encoding="utf-8") == "linux/gone.ftl\n"
    readme = (priv / "README.md").read_text(encoding="utf-8")
    assert "- `005-linux.patch`: the Linux cut, in words" in readme and "- `006-mine.patch`: Mine" in readme


def test_the_mark_past_a_long_header_still_owns_the_patch(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch, [], {})
    f = port / "001-long.patch"
    f.write_text("# x\n# Files: " + "f, " * 3000 + "\n" + export.MARK + "t\n\ndiff --git a/a b/a\n", encoding="utf-8")
    assert export.owned(port, "t") == {"long": "001-long.patch"}


def test_a_file_a_privacy_patch_edits_keeps_the_step_in_privacy(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch, [("c1", ["s.mjs"], "theme", "now", "port")],
                              {"c1": "diff --git a/s.mjs b/s.mjs\n+2\n"})
    (priv / "032-lock.patch").write_text("# lock\n\ndiff --git a/s.mjs b/s.mjs\n+1\n", encoding="utf-8")
    export.export("t", root, "158", say=lambda *_: None)
    assert [p.name for p in priv.glob("033-*.patch")] == ["033-theme.patch"]


def test_lists_refresh_and_carried_steps_are_not_hand_changes(tmp_path, monkeypatch):
    import subprocess as sp
    w = tmp_path / "src"
    w.mkdir()
    g = lambda *a: sp.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout
    (w / "a.js").write_text("one\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine")
    for subj, text in (("D-157-42: security lists refreshed before the build from main abc", "two\n"),
                       ("carried change", "three\n"), ("kept change", "four\n")):
        (w / "a.js").write_text(text)
        g("commit", "-qam", subj)
    carried = g("rev-parse", "HEAD~1").strip()
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    (tmp_path / "state" / "t").mkdir(parents=True)
    (tmp_path / "state" / "t" / "journal.jsonl").write_text(
        json.dumps({"event": "hand-carried", "commit": carried, "by": ["028-x.patch"]}) + "\n", encoding="utf-8")
    got = export.hand_commits({"id": "t", "workdir": str(w)})
    assert [why for _, _, why, _, _ in got] == [None, "kept change"]
    assert got[0][4] == {"carried": ["028-x.patch"], "why": ""}


def test_a_file_a_later_group_writes_sends_the_step_to_the_final_group(tmp_path, monkeypatch):
    root, port, priv = _setup(tmp_path, monkeypatch, [("c1", ["m.css"], "theme", "now", "port")],
                              {"c1": "diff --git a/m.css b/m.css\n+2\n"})
    (root / "23.REBASE.158").mkdir()
    (root / "23.REBASE.158" / "0164-m.patch").write_text("# r\n\ndiff --git a/m.css b/m.css\n+1\n", encoding="utf-8")
    pol = {"groups": {g: {"status": "enabled"} for g in ("21.PORT.FIXES.158", "22.EGRESS.LOCKDOWN.158", "23.REBASE.158",
                                                         "24.FINAL.158")}}
    export.export("t", root, "158", say=lambda *_: None, policy=pol)
    assert list(port.glob("*.patch")) == []
    assert [p.name for p in (root / "24.FINAL.158").glob("*.patch")] == ["001-theme.patch"]
