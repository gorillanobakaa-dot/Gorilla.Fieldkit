# Version: 1.0.0 · updated 26-10-16-02-50
"""ship audit / ship release: work reaches main and main is released (2026-10-16: main 135 commits past v0.3.0 and no
tool said so). Real git repositories in a temp folder; GitHub is a stand-in."""
import json
import subprocess

from fieldkit.method import shiprelease as sr


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _origin_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    (w / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n', newline="\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "first")
    _g(w, "tag", "-a", "v0.1.0", "-m", "r")
    for i in range(3):
        (w / f"f{i}").write_text(str(i))
        _g(w, "add", "-A")
        _g(w, "commit", "-qm", f"work {i}")
    _g(w, "push", "-q", "origin", "main", "--tags")
    return w


def test_audit_says_a_release_is_due_and_which_branch_never_reached_main(tmp_path):
    w = _origin_and_clone(tmp_path)
    _g(w, "checkout", "-q", "-b", "lost")
    (w / "lost.txt").write_text("x")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "never merged")
    _g(w, "push", "-q", "origin", "lost")
    _g(w, "checkout", "-q", "main")
    r = sr.audit(w, offline=True, max_commits=2)
    rows = {x["check"]: x for x in r["rows"]}
    assert rows["checkout"]["state"] == "OK"
    assert rows["branches"]["state"] == "DO" and "lost (1)" in rows["branches"]["say"]
    assert rows["release"]["state"] == "DO" and "3 commit(s)" in rows["release"]["say"] and "v0.1.0" in rows["release"]["say"]
    assert sr.audit(w, offline=True, max_commits=5)["rows"][-1]["state"] == "OK"


def test_release_refuses_until_ci_version_and_notes_agree_then_tags(tmp_path):
    w = _origin_and_clone(tmp_path)
    runs = {"ci": "completed\tfailure"}

    def gh(args, timeout=60):
        if args[0] == "api":
            return f"test\t{runs['ci']}\n", ""
        if args[:2] == ["release", "view"]:
            return (w / "releases/notes/v0.2.0/page.md").read_text(), ""
        return "[]", ""
    r = sr.release("v0.2.0", w, gh=gh, wait=0)
    assert not r["ok"] and r["rows"][-1]["check"] == "ci"
    runs["ci"] = "completed\tsuccess"
    assert sr.release("v0.3.0", w, gh=gh, wait=0)["rows"][-1]["check"] == "version"
    r = sr.release("v0.2.0", w, gh=gh, wait=0)
    assert r["rows"][-1]["check"] == "notes"
    made = sr.scaffold(w, "v0.2.0")
    assert len(made) == 3 and "work 2" in (w / "releases/notes/v0.2.0/developer.md").read_text()
    assert "v0.2.0" in json.dumps(sr.release("v0.2.0", w, gh=gh, wait=0, scaffold_notes=True))


def test_slug_keeps_dots_in_the_repository_name(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _g(tmp_path, "remote", "add", "origin", "https://github.com/someone/Gorilla.Fieldkit.git")
    assert sr.repo_slug(tmp_path) == "someone/Gorilla.Fieldkit"
