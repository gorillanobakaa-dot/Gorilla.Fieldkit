"""release-check row 7 finds the release documents manifest (2026-10-08): the private copy beside the owner's repo first
(its source list names internal files), then the public patch set's releases/<version>/ (where the 157 documents
really are), then release/<version>/; with none, the private path is named so the row can say where to put it."""
import json

from fieldkit.buildh import buildrun, releasecheck

T = {"meta": {"upstream": {"version": "157.0"}}}


def _owner(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "gorilla-patchset/patches"}),
                                                          encoding="utf-8")
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(tmp_path))
    return tmp_path


def _put(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("sources: []\n", encoding="utf-8")
    return p


def test_the_private_manifest_wins(tmp_path, monkeypatch):
    o = _owner(tmp_path, monkeypatch)
    _put(o / "gorilla-patchset" / "releases" / "157.0" / "release-docs.yaml")
    private = _put(o / "release-docs" / "157.0" / "release-docs.yaml")
    assert releasecheck.release_docs_manifest(T) == private


def test_the_public_releases_folder_is_found(tmp_path, monkeypatch):
    o = _owner(tmp_path, monkeypatch)
    public = _put(o / "gorilla-patchset" / "releases" / "157.0" / "release-docs.yaml")
    assert releasecheck.release_docs_manifest(T) == public


def test_with_none_the_private_path_is_named(tmp_path, monkeypatch):
    o = _owner(tmp_path, monkeypatch)
    assert releasecheck.release_docs_manifest(T) == o / "release-docs" / "157.0" / "release-docs.yaml"
