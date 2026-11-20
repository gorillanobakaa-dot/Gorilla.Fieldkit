import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.method import releasecandidate as rc, ship


def test_older_candidate_policy_requires_the_signed_publisher(monkeypatch, tmp_path):
    from fieldkit.core import sync

    monkeypatch.setattr(sync, "_slug", lambda _: "owner/repo")
    monkeypatch.setattr(rc, "effective_policy", lambda *_: {"publication": "local"})
    monkeypatch.setattr(rc, "publish", lambda *a, **k: pytest.fail("a non-publisher attempted promotion"))

    def authorize(action, **kwargs):
        assert action == "release_tag" and kwargs["single_publisher"] is True
        return "deny", "only the signed publisher may promote", None

    result = ship.gh_release(tmp_path, "v1.2", ["--candidate", "candidate.yaml"], mandate=authorize)
    assert result["state"] == "candidate-refused" and "only the signed publisher" in result["error"]


def test_github_retries_explicit_short_cooldown_and_keeps_long_cooldown_for_resume():
    waits = []
    responses = iter([
        SimpleNamespace(returncode=1, stderr="rate limited", stdout="HTTP/2 429\r\nRetry-After: 3\r\n\r\n{}"),
        SimpleNamespace(returncode=0, stderr="", stdout='HTTP/2 200\n\n{"id": 42}'),
        SimpleNamespace(returncode=1, stderr="rate limited", stdout=("HTTP/2 403\nX-RateLimit-Remaining: 0\n"
                                                                    "X-RateLimit-Reset: 1199\n\n{}")),
    ])

    def run(args, **kwargs):
        assert args[:3] == ["gh", "api", "--include"]
        return next(responses)

    gh = rc.GitHub("owner/repo", run=run, now=lambda: 1000, pause=waits.append)
    assert gh.api("releases/42") == {"id": 42}
    with pytest.raises(rc.Refused, match="resume the same candidate after 200 seconds"):
        gh.api("releases/42")
    assert waits == [3]


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


def test_replacing_one_installer_reuses_only_independent_proof():
    installers = {"windows": "windows.exe", "debian": "debian.deb", "mac": "mac.pkg"}
    specs = {target: {"workflow": ".github/workflows/release-candidate.yml", "platform": platform, "arch": "x86_64"}
             for target, platform in (("windows", "windows"), ("debian", "debian"), ("mac", "macos"))}
    candidate = {"version": "1.0", "runs": {"windows": 4, "debian": 2, "mac": 3}}
    assets = {"windows.exe": "win-new", "debian.deb": "deb", "mac.pkg": "mac", "shared.zip": "shared"}

    class Proofs:
        def proof(self, target, run, workflow, commit):
            assert run == candidate["runs"][target]
            observed = {installers[target]: assets[installers[target]], "shared.zip": "shared"}
            if target != "windows":
                observed["windows.exe"] = "win-old"
            return {"target": target, "version": "1.0", "commit": commit, "installed": True,
                    "installer": installers[target], "platform": specs[target]["platform"], "arch": "x86_64",
                    "assets": observed}

    rc.check_proofs(Proofs(), {"targets": specs}, candidate, "commit", assets, installers)
    for spec in specs.values():
        spec["install_inputs"] = [r"shared\.zip"]
    with pytest.raises(rc.Refused, match="windows: package hashes differ"):
        rc.check_proofs(Proofs(), {"targets": specs}, candidate, "commit",
                        dict(assets, **{"shared.zip": "replacement"}), installers)


def test_draft_recovers_an_interrupted_upload_without_changing_latest(tmp_path, record_property):
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
    kept = len(gh.uploads)
    gh.fail = False
    with pytest.raises(rc.Refused, match="release window"):
        rc.promote(gh, "v1.0", candidate, tmp_path, assets, authorize,
                   gate=lambda *_: {"ok": False, "error": "release window"})
    assert gh.row["draft"] and gh.latest == "old"
    assert rc.promote(gh, "v1.0", candidate, tmp_path, assets, authorize, gate=gate) == "new"
    assert gh.uploads == ["a.deb", "b.exe"]
    assert approvals == [True, True]
    record_property("fieldkit_fixture_assets_reused", kept)
    record_property("fieldkit_fixture_uploads_completed", len(gh.uploads))


def test_independent_repo_uses_its_tagged_policy_in_cli_and_ci(tmp_path, monkeypatch):
    from fieldkit.core import sync

    def git(*args):
        return subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    git("init")
    git("config", "user.email", "test@example.org")
    git("config", "user.name", "Test")
    git("commit", "--allow-empty", "-m", "Before enrollment")
    git("tag", "v1.0")
    path = tmp_path / rc.POLICY_PATH
    path.parent.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "release-policy" / path.name
    path.write_bytes(fixture.read_bytes())
    git("add", rc.POLICY_PATH)
    git("commit", "-m", "Enroll in candidate releases")
    git("tag", "v2.0")
    commit = git("rev-parse", "HEAD")
    path.write_text("schema: 999", encoding="utf-8")
    assert rc.policy_at(tmp_path, "refs/tags/v1.0") is None
    assert rc.policy_at(tmp_path, "refs/tags/v2.0")["schema"] == 1

    name = "pocketclock-2.0-amd64.deb"
    (tmp_path / name).write_bytes(b"package")
    digest = rc.file_digest(tmp_path / name)
    (tmp_path / "page.md").write_text(
        f"| File | Bytes | SHA-256 |\n|---|---|---|\n| `{name}` | 7 | `{digest}` |\n", encoding="utf-8")
    (tmp_path / "candidate.yaml").write_text(
        f'version: "2.0"\ntitle: Pocketclock\nnotes: page.md\nassets: [{name}]\nruns: {{linux-amd64: 42}}\n',
        encoding="utf-8")

    class ReadOnlyGitHub:
        def __init__(self, slug):
            self.slug = slug

        def api(self, endpoint):
            if not endpoint:
                return {"default_branch": "main"}
            assert endpoint in ("commits/v2.0", "commits/main")
            return {"sha": commit}

        def proof(self, target, run_id, workflow, sha):
            assert (target, run_id, workflow, sha) == ("linux-amd64", 42, ".github/workflows/install.yml", commit)
            return {"target": target, "installer": name, "version": "2.0", "commit": sha, "installed": True,
                    "platform": "linux", "arch": "amd64", "assets": {name: digest}}

    def unexpected(*args, **kwargs):
        pytest.fail("an enrolled repo must not use legacy publication, the pilot registry or a write mandate for CI")

    monkeypatch.setattr(sync, "_slug", lambda _: "independent/pocketclock")
    monkeypatch.setattr(rc, "GitHub", ReadOnlyGitHub)
    monkeypatch.setattr(rc, "registered_policy", unexpected)
    assert ship.gh_release(tmp_path, "v1.0", mandate=unexpected, run=unexpected)["state"] == "candidate-required"
    assert ship.gh_release_add(tmp_path, "v2.0", [tmp_path / name], mandate=unexpected,
                               run=unexpected)["state"] == "candidate-required"
    result = ship.gh_release(tmp_path, "v2.0", ["--check-candidate", "candidate.yaml"], mandate=unexpected)
    assert result == {"ok": True, "state": "candidate-verified", "tag": "v2.0"}

    git("add", rc.POLICY_PATH)
    git("commit", "-m", "Invalid policy")
    result = ship.gh_release(tmp_path, "v1.0", mandate=unexpected, run=unexpected)
    assert result["state"] == "candidate-required"
    assert "schema: 1" in result["error"]
