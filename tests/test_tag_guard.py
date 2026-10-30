# Version: 1.0.0 · updated 26-10-29
"""A tag without release notes is refused before it is pushed (docs/LESSONS.yaml L-20261029-1: v0.7.0 was pushed with
no releases/notes/v0.7.0/, the release workflow failed, and a pushed tag can never be moved). Real git in a temp
folder (a bare folder as origin); GitHub's CI and the page check are injected, no network."""
import json
import subprocess

import pytest

from fieldkit.method import ship, tagguard

TAG = "v0.8.0"


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _write(w, rel, text):
    p = w / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    _write(w, "pyproject.toml", '[project]\nname = "x"\nversion = "0.8.0"\n')
    _write(w, "x/__init__.py", '__version__ = "0.8.0"\n')
    for n in tagguard.NOTES:
        _write(w, f"releases/notes/{TAG}/{n}.md", f"{n}\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "release")
    _g(w, "push", "-q", "origin", "main")
    return w


GREEN = lambda *a, **k: (True, "CI green")                      # noqa: E731
PAGE_OK = lambda *a, **k: (True, "page ok")                     # noqa: E731


def rows(r):
    return {x["check"]: x for x in r["rows"]}


def test_a_ready_tag_passes_every_row(repo):
    r = tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[])
    assert r["ok"], r
    assert [x["check"] for x in r["rows"]] == ["tag-free", "version", "notes", "page", "ci"]


def test_missing_release_notes_refuse_with_the_fix_command(repo):
    _g(repo, "rm", "-rq", f"releases/notes/{TAG}")
    _g(repo, "commit", "-qm", "no notes")
    r = tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[])
    assert not r["ok"] and not rows(r)["notes"]["ok"]
    assert "ship release v0.8.0 --scaffold" in rows(r)["notes"]["run"] and "page.md" in rows(r)["notes"]["say"]


def test_one_missing_note_file_is_named(repo):
    _g(repo, "rm", "-q", f"releases/notes/{TAG}/page.md")
    _g(repo, "commit", "-qm", "no page")
    r = tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[])
    assert "page.md" in rows(r)["notes"]["say"] and "opening.md" not in rows(r)["notes"]["say"]


def test_a_page_that_fails_release_page_check_refuses(repo):
    r = tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=lambda *a: (False, "layman missing"), ci_os=[])
    assert not r["ok"] and rows(r)["page"]["say"] == "layman missing"


def test_the_page_check_reads_the_committed_files_not_the_working_tree(repo):
    (repo / "releases" / "notes" / TAG / "page.md").unlink()           # deleted only in the working tree
    seen = {}

    def page(repo_, commit, tag):
        seen["text"] = tagguard._show(repo_, commit, f"releases/notes/{tag}/page.md")
        return True, "ok"
    assert tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=page, ci_os=[])["ok"] and seen["text"] == "page\n"


@pytest.mark.parametrize("file,text", [("pyproject.toml", '[project]\nname = "x"\nversion = "0.7.9"\n'),
                                       ("x/__init__.py", '__version__ = "0.7.9"\n')])
def test_a_version_that_is_not_the_tags_refuses(repo, file, text):
    _write(repo, file, text)
    _g(repo, "commit", "-qam", "stale version")
    r = tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[])
    assert not r["ok"] and not rows(r)["version"]["ok"] and file in rows(r)["version"]["say"]
    assert "version-sync . --expect 0.8.0" in rows(r)["version"]["run"]


def test_a_commit_with_no_version_place_refuses(repo):
    _g(repo, "rm", "-q", "pyproject.toml", "x/__init__.py")
    _g(repo, "commit", "-qm", "none")
    assert not rows(tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[]))["version"]["ok"]


def test_red_ci_refuses(repo):
    r = tagguard.check(repo, TAG, dry_run=True, ci=lambda *a, **k: (False, "CI not green: windows"), page=PAGE_OK, ci_os=[])
    assert not r["ok"] and rows(r)["ci"]["say"] == "CI not green: windows"


def test_ci_is_asked_for_every_os_of_the_mandate_and_the_gh_runner_is_injected(repo):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = json.dumps([{"name": "tests (ubuntu)", "status": "completed", "conclusion": "success"}])
        return subprocess.CompletedProcess(cmd, 0, out, "")
    r = tagguard.check(repo, TAG, dry_run=True, page=PAGE_OK, ci_os=["windows", "ubuntu"], run=run)
    assert not r["ok"] and "windows" in rows(r)["ci"]["say"] and calls and calls[0][0] == "gh"
    assert tagguard.check(repo, TAG, dry_run=True, page=PAGE_OK, ci_os=["ubuntu"], run=run)["ok"]


def test_a_tag_already_on_origin_is_burned(repo):
    _g(repo, "tag", "-a", TAG, "-m", "r")
    _g(repo, "push", "-q", "origin", f"refs/tags/{TAG}")
    for dry in (True, False):
        r = tagguard.check(repo, TAG, dry_run=dry, ci=GREEN, page=PAGE_OK, ci_os=[])
        assert not r["ok"] and "never moved" in rows(r)["tag-free"]["say"]


def test_a_dry_run_refuses_an_existing_local_tag_but_a_push_check_wants_it(repo):
    _g(repo, "tag", "-a", TAG, "-m", "r")
    assert not tagguard.check(repo, TAG, dry_run=True, ci=GREEN, page=PAGE_OK, ci_os=[])["ok"]
    assert tagguard.check(repo, TAG, ci=GREEN, page=PAGE_OK, ci_os=[])["ok"]


def test_the_checks_read_the_tagged_commit_not_head(repo):
    _g(repo, "tag", "-a", TAG, "-m", "r")
    _g(repo, "rm", "-rq", f"releases/notes/{TAG}")
    _g(repo, "commit", "-qm", "later")                                  # HEAD lost the notes; the tag still has them
    assert tagguard.check(repo, TAG, ci=GREEN, page=PAGE_OK, ci_os=[])["ok"]


def test_a_bad_tag_name_is_refused(repo):
    assert not tagguard.check(repo, "0.8.0", dry_run=True)["ok"]


def _allow(a, **k):
    return "allow", "ok", None


def test_push_tag_pushes_nothing_when_the_guard_refuses(repo):
    _g(repo, "tag", "-a", TAG, "-m", "r")
    guard = lambda r, t: tagguard.check(r, t, ci=GREEN, page=lambda *a: (False, "bad page"), ci_os=[])   # noqa: E731
    r = ship.push_tag(repo, TAG, mandate=_allow, guard=guard)
    assert not r["ok"] and r["state"] == "not-release-ready" and "page: bad page" in r["error"]
    assert _g(repo, "ls-remote", "origin", f"refs/tags/{TAG}") == ""


def test_push_tag_pushes_when_the_guard_passes(repo):
    _g(repo, "tag", "-a", TAG, "-m", "r")
    guard = lambda r, t: tagguard.check(r, t, ci=GREEN, page=PAGE_OK, ci_os=[])                          # noqa: E731
    r = ship.push_tag(repo, TAG, mandate=_allow, guard=guard)
    assert r["ok"] and _g(repo, "ls-remote", "origin", f"refs/tags/{TAG}") != ""


def test_the_cli_dry_run_exits_3_and_prints_the_fix(repo, capsys):
    rc = ship.main(["release-ready", TAG, "--repo", str(repo), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and not out["ok"] and any(x["run"] for x in out["rows"] if not x["ok"])


def test_gh_release_does_nothing_when_a_tag_workflow_publishes(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "release.yml").write_text('on:\n  push:\n    tags: ["v*"]\n', encoding="utf-8")
    called = []
    r = ship.gh_release(tmp_path, TAG, mandate=lambda *a, **k: called.append(1) or ("allow", "ok", None),
                        run=lambda *a, **k: called.append(2))
    assert r["ok"] and r["state"] == "tag-workflow-publishes" and "do not run gh-release" in r["next"] and not called


def test_gh_release_still_works_without_a_tag_workflow(tmp_path):
    called = []
    r = ship.gh_release(tmp_path, TAG, mandate=lambda *a, **k: ("ask", "no", None), run=lambda *a, **k: called.append(2))
    assert not r["ok"] and r["state"] == "not-mandated"
