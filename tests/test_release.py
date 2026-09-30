"""release gate: tests the TAG not the working tree, fails on every broken promise, never skippable."""
import hashlib
import subprocess
import sys

import pytest
import yaml

from fieldkit import release


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "tool.py").write_text("print('ok')\n")
    (r / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "v1")
    _git(r, "tag", "v1")
    return r


def _spec(tmp_path, repo, **kw):
    s = {"name": "proj", "repo": "owner/proj", "local": str(repo), "tag": "v1",
         "tests": [[sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_ok.py"]],
         "artifacts": [{"path": "tool.py"}], "privacy": True, "claims": []}
    s.update(kw)
    p = tmp_path / "spec.yaml"
    p.write_text(yaml.safe_dump(s))
    return p


@pytest.fixture
def github(monkeypatch, repo):
    """GitHub stand-in: publishes the tag's own bytes unless told otherwise."""
    state = {"notes": "Release v1. It has 1 test.", "tampered": False}

    def pub_sha(r, tag, path):
        data = subprocess.run(["git", "-C", str(state.get("repo", repo)), "show", f"{tag}:{path}"],
                              capture_output=True).stdout
        return hashlib.sha256(data + (b"x" if state["tampered"] else b"")).hexdigest()
    monkeypatch.setattr(release, "published_file_sha", pub_sha)
    monkeypatch.setattr(release, "release_notes", lambda r, t: state["notes"])
    return state


def test_clear_when_everything_holds(tmp_path, repo, github):
    r = release.check(_spec(tmp_path, repo))
    assert r["clear"], release.lines(r)
    assert release.lines(r)[-1] == "NEXT: publish."


def test_tests_run_on_the_tag_not_uncommitted_work(tmp_path, repo, github):
    (repo / "test_ok.py").write_text("def test_ok():\n    assert False\n")      # broken, but not committed
    assert release.check(_spec(tmp_path, repo))["clear"]
    _git(repo, "commit", "-qam", "break")
    _git(repo, "tag", "v2")
    r = release.check(_spec(tmp_path, repo, tag="v2"))
    assert not r["clear"] and any(g["gate"].startswith("tests") and not g["ok"] for g in r["gates"])


def test_published_file_must_be_the_tested_file(tmp_path, repo, github):
    github["tampered"] = True
    r = release.check(_spec(tmp_path, repo))
    g = next(g for g in r["gates"] if g["gate"].startswith("published tool.py"))
    assert not r["clear"] and not g["ok"] and "DIFFERENT" in g["detail"]


def test_private_material_blocks(tmp_path, repo, github):
    (repo / "notes.txt").write_text("token ghp_" + "A1b2C3d4" * 5 + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "oops")
    _git(repo, "tag", "v3")
    r = release.check(_spec(tmp_path, repo, tag="v3"))
    assert not next(g for g in r["gates"] if g["gate"] == "privacy")["ok"]


def test_claims_need_proof_and_proof_must_pass(tmp_path, repo, github):
    github["notes"] = "Now works on Linux. It has 1 test."
    claims = [{"claim": "works on Linux", "find": "Linux"},
              {"claim": "1 test", "find": r"\b1 test\b",
               "proof": {"command": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--collect-only",
                                     "test_ok.py"], "output_contains": "1 test collected"}},
              {"claim": "runs on a toaster", "find": "toaster"}]              # not in the notes: not checked
    r = release.check(_spec(tmp_path, repo, claims=claims))
    got = {g["gate"]: g["ok"] for g in r["gates"] if g["gate"].startswith("claim")}
    assert got == {"claim: works on Linux": False, "claim: 1 test": True}
    assert not r["clear"] and "no --force" in release.lines(r)[-1]


def test_no_tests_declared_is_a_failure(tmp_path, repo, github):
    r = release.check(_spec(tmp_path, repo, tests=[]))
    assert not r["clear"] and "nothing proves the release works" in str(r["gates"])


def test_missing_tag_fails_cleanly(tmp_path, repo, github):
    r = release.check(_spec(tmp_path, repo, tag="v9"))
    assert not r["clear"] and r["gates"][0]["gate"] == "tag exported" and "not found" in r["gates"][0]["detail"]


# -- evidence from another machine ---------------------------------------------------

from fieldkit.core.host import host  # noqa: E402

HERE = host()["system"].lower()
OTHER = "linux" if HERE != "linux" else "windows"


def _ev_spec(tmp_path, repo, platforms):
    return _spec(tmp_path, repo, claims=[{"claim": "runs everywhere", "find": "1 test",
                                          "proof": {"evidence": platforms}}])


def _claim_gate(r):
    return next(g for g in r["gates"] if g["gate"] == "claim: runs everywhere")


def test_prove_records_evidence_that_check_accepts(tmp_path, repo, github):
    spec = _ev_spec(tmp_path, repo, [HERE])
    ev, path = release.prove(spec)
    assert ev["passed"] and ev["platform"] == HERE and path.name == f"proj-v1-{HERE}.json"
    r = release.check(spec)
    assert _claim_gate(r)["ok"] and r["clear"], release.lines(r)


def test_missing_platform_evidence_fails_and_says_where_to_run(tmp_path, repo, github):
    spec = _ev_spec(tmp_path, repo, [HERE, OTHER])
    release.prove(spec)
    g = _claim_gate(release.check(spec))
    assert not g["ok"] and f"no evidence from {OTHER}" in g["detail"] and "release prove" in g["detail"]


def test_evidence_for_a_different_tree_is_refused(tmp_path, repo, github):
    spec = _ev_spec(tmp_path, repo, [HERE])
    release.prove(spec)
    (repo / "tool.py").write_text("print('changed')\n")
    _git(repo, "commit", "-q", "-am", "v1 moved")
    _git(repo, "tag", "-f", "v1")
    g = _claim_gate(release.check(spec))
    assert not g["ok"] and "is not v1" in g["detail"]


def test_evidence_claiming_the_wrong_platform_or_a_failure_is_refused(tmp_path, repo, github):
    import json
    spec = _ev_spec(tmp_path, repo, [HERE])
    ev, path = release.prove(spec)
    path.write_text(json.dumps(ev | {"passed": False}))
    assert "tests failed there" in _claim_gate(release.check(spec))["detail"]
    other = path.with_name(f"proj-v1-{OTHER}.json")
    other.write_text(json.dumps(ev))                               # a copy of this machine's file, renamed
    g = _claim_gate(release.check(_ev_spec(tmp_path, repo, [OTHER])))
    assert not g["ok"] and f"file says {HERE}" in g["detail"]


def test_failing_tests_are_recorded_as_failed(tmp_path, repo, github):
    (repo / "test_ok.py").write_text("def test_ok():\n    assert False\n")
    _git(repo, "commit", "-q", "-am", "broken")
    _git(repo, "tag", "-f", "v1")
    ev, _ = release.prove(_ev_spec(tmp_path, repo, [HERE]))
    assert not ev["passed"] and ev["runs"][0]["exit"] != 0


def test_line_ending_conversion_does_not_fake_a_difference(tmp_path, github):
    """2026-09-30, GitHub's Windows runners: with core.autocrlf=true the stored blob is LF but
    `git archive` wrote CRLF, so identical code was reported as 'published != tested'."""
    r = tmp_path / "crlf"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "core.autocrlf", "true")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "tool.py").write_bytes(b"print('ok')\r\n")
    (r / "test_ok.py").write_bytes(b"def test_ok():\r\n    assert True\r\n")
    _git(r, "add", ".")
    _git(r, "commit", "-q", "-m", "v1")
    _git(r, "tag", "v1")
    github["repo"] = r
    res = release.check(_spec(tmp_path, r))
    assert res["clear"], release.lines(res)


def test_privacy_scans_what_is_published_not_what_the_tests_wrote(tmp_path, repo, github):
    """2026-09-30: the gate scanned the export AFTER running its tests, so test logs (which
    carry home paths) failed a release whose published files were clean."""
    writer = [sys.executable, "-c", "import pathlib; pathlib.Path('state').mkdir(); "
              "pathlib.Path('state/run.log').write_text('/home/' + 'somebody/' + 'x')"]
    r = release.check(_spec(tmp_path, repo, tests=[writer]))
    assert next(g for g in r["gates"] if g["gate"] == "privacy")["ok"], release.lines(r)
