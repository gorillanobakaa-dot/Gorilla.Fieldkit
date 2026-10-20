"""export-hand owns the numbered patches of its two groups: a file it no longer writes is removed (2026-10-03: a
renamed note left 027-...-pref-block.patch beside 027-...-pref-block-maintai.patch)."""
from fieldkit.buildh import export, task


def test_a_stale_numbered_patch_is_removed_and_the_written_ones_stay(tmp_path, monkeypatch):
    root = tmp_path / "patches"
    port, priv = root / "21.PORT.FIXES.157", root / "22.EGRESS.LOCKDOWN.157"
    port.mkdir(parents=True)
    priv.mkdir()
    mark = export.MARK + "t\n"                                           # written by this task's export before
    (priv / "001-old-name.patch").write_text("# x\n" + mark + "\ndiff --git a/o b/o\n", encoding="utf-8")
    (port / "001-cut.patch").write_text("# x\n" + mark + "\ndiff --git a/c b/c\n", encoding="utf-8")  # moved since
    (port / "002-linux.patch").write_text("# from the other machine\n\ndiff --git a/l b/l\n", encoding="utf-8")
    (priv / "README.md").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    monkeypatch.setattr(export, "hand_commits", lambda t: [("c1", ["a.js"], "cut", "now", "privacy")])
    monkeypatch.setattr(export, "_git", lambda w, *a: "" if "--name-only" in a else "diff --git a/a.js b/a.js\n")
    export.export("t", root, "157", say=lambda *_: None)
    assert sorted(p.name for p in priv.glob("*.patch")) == ["003-cut.patch"]       # the next free number (002 is the other machine's)
    assert [p.name for p in port.glob("*.patch")] == ["002-linux.patch"]           # never another writer's patch
    assert (priv / "README.md").exists()


def test_a_change_committed_directly_is_exported_and_harness_commits_are_not(tmp_path, monkeypatch):
    # 2026-10-15: 23 commits of the Linux 158 port were made with git commit, not `record`; export saw none of them
    import subprocess as sp
    w = tmp_path / "src"
    w.mkdir()
    g = lambda *a: sp.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout
    (w / "a.js").write_text("one\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine")
    for subj, text in (("checkpoint: apply-05.PREFS", "two\n"), ("binary-patches: pictures restored", "three\n"),
                       ("D-157-05: refused installs say why\n\nKind: privacy", "four\n"), ("build fix: an API moved", "five\n")):
        (w / "a.js").write_text(text)
        g("commit", "-qam", subj)
    jd = tmp_path / "state" / "t"
    jd.mkdir(parents=True)
    (jd / "journal.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    got = export.hand_commits({"id": "t", "workdir": str(w)})
    assert [(why, kind) for _, _, why, _, kind in got] == [("D-157-05: refused installs say why", "privacy"),
                                                          ("build fix: an API moved", None)]
    assert export.is_hand_change("checkpoint: hand edit (hand): a.js")
    assert not export.is_hand_change("checkpoint: replace-files-08.Look")
