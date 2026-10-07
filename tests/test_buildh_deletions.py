"""Deletions through record and export-hand (2026-10-07, D-157-35), on a real git tree:
  - a file removed with `git rm` is recorded like an unstaged deletion (it used to be taken for a new file)
  - export writes deletions to the group's DELETED_FILES.manifest.txt and keeps the patch free of the old content
  - the manifest is owned by export: rewritten from the record, removed when nothing is deleted any more"""
import subprocess

from fieldkit.buildh import export, task


def _git(w, *a):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(w), *a], check=True, capture_output=True,
                          text=True).stdout


def _tree(tmp_path):
    w = tmp_path / "tree"
    (w / "branding").mkdir(parents=True)
    (w / "branding" / "logo.svg").write_bytes(b"<svg>" + b"A" * 50000 + b"</svg>\n")
    (w / "branding" / "logo-2x.png").write_bytes(bytes(range(256)) * 200)
    (w / "page.css").write_bytes(b'body { background: url("logo.svg"); }\n')
    _git(w, "init", "-q")
    _git(w, "add", ".")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pristine")
    return w


def test_export_puts_deletions_in_the_manifest_not_in_the_patch(tmp_path, monkeypatch):
    w = _tree(tmp_path)
    _git(w, "rm", "-q", "branding/logo.svg", "branding/logo-2x.png")
    (w / "page.css").write_bytes(b'body { background: url("logo.png"); }\n')
    _git(w, "add", ".")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one logo")
    c = _git(w, "rev-parse", "HEAD").strip()
    root = tmp_path / "patches"
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(w)})
    monkeypatch.setattr(export, "hand_commits",
                        lambda t: [(c, ["branding/logo.svg", "branding/logo-2x.png", "page.css"], "one logo", "now", "port")])
    export.export("t", root, "157", say=lambda *_: None)
    port = root / "21.PORT.FIXES.157"
    patch = next(port.glob("001-*.patch")).read_text(encoding="utf-8")
    body = patch.split("\n\n", 1)[1]                                 # the header's "# Files:" still names them all
    assert "diff --git a/page.css" in body and "diff --git a/branding" not in body and "AAAA" not in body
    assert (port / "DELETED_FILES.manifest.txt").read_text(encoding="utf-8") == "branding/logo-2x.png\nbranding/logo.svg\n"
    assert "DELETED_FILES.manifest.txt (2)" in (port / "README.md").read_text(encoding="utf-8")
    # nothing deleted any more: the manifest goes
    monkeypatch.setattr(export, "hand_commits", lambda t: [])
    export.export("t", root, "157", say=lambda *_: None)
    assert not (port / "DELETED_FILES.manifest.txt").exists()


def test_a_file_deleted_then_created_again_keeps_its_deletion_in_the_patch(tmp_path, monkeypatch):
    # replay, 2026-10-07: patch 045 deleted a module, 046 re-created it empty; a manifest entry (applied after the
    # group's patches) removed it again
    w = _tree(tmp_path)
    commits = []
    for label, change in (("delete", lambda: _git(w, "rm", "-q", "page.css")),
                          ("again", lambda: ((w / "page.css").write_bytes(b"/* empty */\n"), _git(w, "add", "page.css")))):
        change()
        _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", label)
        commits.append(_git(w, "rev-parse", "HEAD").strip())
    root = tmp_path / "patches"
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(w)})
    monkeypatch.setattr(export, "hand_commits", lambda t: [(commits[0], ["page.css"], "delete", "now", "port"),
                                                           (commits[1], ["page.css"], "again", "now", "port")])
    export.export("t", root, "157", say=lambda *_: None)
    port = root / "21.PORT.FIXES.157"
    first = next(port.glob("001-*.patch")).read_text(encoding="utf-8")
    assert "deleted file mode" in first and "diff --git a/page.css" in first
    assert not (port / "DELETED_FILES.manifest.txt").exists()


def test_record_takes_a_git_rm_as_a_deletion(tmp_path, monkeypatch):
    from fieldkit.buildh import handedit
    w = _tree(tmp_path)
    _git(w, "rm", "-q", "branding/logo-2x.png")                  # staged: gone from the index and the disk
    t = {"id": "t", "workdir": str(w), "steps": [], "meta": {}}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "save", lambda t: None)
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    monkeypatch.setattr(task, "checkpoint", lambda t, label: _git(w, "add", "-A") or
                        _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", label))
    ids = handedit.record("t", ["branding/logo-2x.png"], "one logo", kind="port")
    assert ids and all("logo-2x.png" in i for i in ids)
    assert "logo-2x.png" not in _git(w, "ls-files")                # the checkpoint committed the deletion
