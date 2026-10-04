"""export-hand owns the numbered patches of its two groups: a file it no longer writes is removed (2026-10-03: a
renamed note left 027-...-pref-block.patch beside 027-...-pref-block-maintai.patch)."""
from fieldkit.buildh import export, task


def test_a_stale_numbered_patch_is_removed_and_the_written_ones_stay(tmp_path, monkeypatch):
    root = tmp_path / "patches"
    port, priv = root / "21.PORT.FIXES.157", root / "22.EGRESS.LOCKDOWN.157"
    port.mkdir(parents=True)
    priv.mkdir()
    (priv / "001-old-name.patch").write_text("x", encoding="utf-8")
    (port / "001-cut.patch").write_text("x", encoding="utf-8")          # moved to the other group since
    (priv / "README.md").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    monkeypatch.setattr(export, "hand_commits", lambda t: [("c1", ["a.js"], "cut", "now", "privacy")])
    monkeypatch.setattr(export, "_git", lambda w, *a: "diff --git a/a.js b/a.js\n")
    export.export("t", root, "157", say=lambda *_: None)
    assert sorted(p.name for p in priv.glob("*.patch")) == ["001-cut.patch"]
    assert not list(port.glob("*.patch"))
    assert (priv / "README.md").exists()
