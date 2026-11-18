import pytest

from fieldkit.method import releasecandidate as rc


@pytest.mark.parametrize("missing, substitute", [
    ("Windows", None), ("Windows", "app.exe"), ("Debian", None), ("Mac", "app-157.0.pkg"),
])
def test_wrong_or_missing_installer_blocks_candidate(tmp_path, missing, substitute):
    expected = {"Windows": "app-160.0-setup.exe", "Debian": "app-160.0.deb", "Mac": "app-160.0.pkg"}
    policy = {"distribution": "installers", "targets": {
        target.lower(): {"installer": name.replace("160.0", "{version}")}
        for target, name in expected.items()
    }}
    names = [name for target, name in expected.items() if target != missing]
    if substitute:
        names.append(substitute)
    for name in names:
        (tmp_path / name).write_bytes(b"package")
    with pytest.raises(rc.Refused, match=f"{missing.lower()}: expected exactly one installer"):
        rc.check_assets(policy, {"version": "160.0", "assets": names}, tmp_path, "v160.0")


def test_installed_version_and_hash_must_match():
    class ProofSource:
        def proof(self, *_):
            return proof

    policy = {"targets": {"mac": {"platform": "macos", "arch": "x86_64", "minimum_os": "10.13",
                                  "workflow": ".github/workflows/release-candidate.yml"}}}
    candidate = {"version": "160.0", "runs": {"mac": 5}}
    assets = {"browser.pkg": "hash"}
    proof = {"target": "mac", "installer": "browser.pkg", "version": "157.0", "commit": "commit",
             "installed": True, "platform": "macos", "arch": "x86_64", "os_version": "10.13.6",
             "assets": {"browser.pkg": "hash"}}
    with pytest.raises(rc.Refused, match="does not match"):
        rc.check_proofs(ProofSource(), policy, candidate, "commit", assets, {"mac": "browser.pkg"})
    proof["version"] = "160.0"
    rc.check_proofs(ProofSource(), policy, candidate, "commit", assets, {"mac": "browser.pkg"})
    proof["assets"]["browser.pkg"] = "wrong"
    with pytest.raises(rc.Refused, match="hashes differ"):
        rc.check_proofs(ProofSource(), policy, candidate, "commit", assets, {"mac": "browser.pkg"})


def test_online_digest_must_match():
    draft = {"assets": [{"name": "test.deb", "state": "uploaded", "digest": "sha256:other"}]}
    with pytest.raises(rc.Refused, match="hash cannot be confirmed"):
        rc.online_assets(draft, {"test.deb": "expected"})


def test_draft_recovers_an_interrupted_upload_without_changing_latest(tmp_path):
    for name in ("a.deb", "b.exe"):
        (tmp_path / name).write_bytes(name.encode())
    assets = {name: rc.file_digest(tmp_path / name) for name in ("a.deb", "b.exe")}
    notes = "| File | Bytes | SHA-256 |\n|---|---|---|\n" + "\n".join(
        f"| `{name}` | {len(name)} | `{digest}` |" for name, digest in assets.items())
    (tmp_path / "page.md").write_text(notes, encoding="utf-8")
    candidate = {"title": "Example 1.0", "notes": "page.md"}

    class DraftGitHub:
        slug = "example/repo"
        row = {"id": 5, "tag_name": "v1.0", "name": "Example 1.0", "body": notes,
               "draft": True, "prerelease": False, "assets": [], "html_url": "new"}
        latest = "old"
        uploads = []
        fail = True

        def release(self, tag):
            return self.row

        def gh(self, *args):
            assert args[:2] == ("release", "upload")
            from pathlib import Path

            name = Path(args[3]).name
            if name == "b.exe" and self.fail:
                raise rc.Refused("connection interrupted")
            self.uploads.append(name)
            self.row["assets"].append({"name": name, "digest": f"sha256:{assets[name]}", "state": "uploaded"})

        def api(self, endpoint, *args):
            if args:
                assert args == ("-X", "PATCH", "-F", "draft=false", "-f", "make_latest=true")
                self.latest = "new"
                self.row["draft"] = False
            if endpoint == "releases/latest":
                assert self.latest == "new"
            return self.row

    gh = DraftGitHub()
    approvals = []
    def authorize():
        approvals.append(True)

    def gate(slug, tag, hotfix):
        assert (slug, tag, hotfix) == ("example/repo", "v1.0", "")
        return {"ok": True}

    with pytest.raises(rc.Refused, match="connection interrupted"):
        rc.promote(gh, "v1.0", candidate, tmp_path, assets, authorize, gate=gate)
    assert gh.row["draft"] and gh.latest == "old" and not approvals
    gh.fail = False
    with pytest.raises(rc.Refused, match="release window"):
        rc.promote(gh, "v1.0", candidate, tmp_path, assets, authorize,
                   gate=lambda *_: {"ok": False, "error": "release window"})
    assert gh.row["draft"] and gh.latest == "old"
    assert rc.promote(gh, "v1.0", candidate, tmp_path, assets, authorize, gate=gate) == "new"
    assert gh.uploads == ["a.deb", "b.exe"]
    assert approvals == [True, True]
