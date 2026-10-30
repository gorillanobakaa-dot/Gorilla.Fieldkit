"""fieldkit ship push --auto: push, the PR (found or created), GitHub auto-merge on. gh and git are faked."""
import json
import subprocess

import pytest

from fieldkit.method import ship

SHA = "a" * 40
URL = "https://github.com/o/r/pull/7"


class Gh:
    """A fake subprocess.run for gh: answers by the sub-command, records every call."""
    def __init__(self, prs=(), runs=(), fail=None):
        self.prs, self.runs, self.fail, self.calls = list(prs), list(runs), fail, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        key = " ".join(cmd[1:3])
        if self.fail == key:
            return subprocess.CompletedProcess(cmd, 1, "", "gh: boom")
        out = {"pr list": json.dumps(self.prs), "run list": json.dumps(self.runs), "pr create": URL + "\n",
               "pr merge": ""}[key]
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def did(self, key):
        return [c for c in self.calls if " ".join(c[1:3]) == key]


def pusher(repo, base):
    return {"ok": True, "branch": "feat/x", "local": SHA, "pushed": True, "said": "pushed"}


def go(gh, **kw):
    notes = []
    r = ship.auto(".", "main", run=gh, pusher=pusher, inbox=lambda *a: notes.append(a), **kw)
    return r, notes


def test_existing_pr_gets_auto_merge_on_the_pushed_commit():
    gh = Gh(prs=[{"number": 7, "url": URL}])
    r, _ = go(gh)
    assert r["ok"] and r["auto"] and r["pr_url"] == URL and "auto-merge on" in r["said"]
    assert not gh.did("pr create")
    assert gh.did("pr merge")[0][3:] == ["7", "--auto", "--merge", "--match-head-commit", SHA]


def test_missing_pr_is_created_then_merged_on():
    gh = Gh()
    r, _ = go(gh)
    assert r["ok"] and r["pr"] == 7
    assert gh.did("pr create")[0][3:] == ["--head", "feat/x", "--base", "main", "--fill"]
    assert gh.did("pr merge")


def test_title_and_body_file_go_to_pr_create():
    gh = Gh()
    go(gh, title="T", body_file="b.md")
    assert gh.did("pr create")[0][-4:] == ["--title", "T", "--body-file", "b.md"]


@pytest.mark.parametrize("which", ["pr list", "pr create", "run list", "pr merge"])
def test_gh_failure_is_exit_1_text_and_the_push_stands(which):
    r, _ = go(Gh(fail=which))
    assert not r["ok"] and r["gh_failed"] and "gh: boom" in r["error"] and r["branch"] == "feat/x"


def test_two_red_runs_park_and_never_call_merge():
    gh = Gh(prs=[{"number": 7, "url": URL}],
            runs=[{"conclusion": "failure"}, {"conclusion": "success"}, {"conclusion": "failure"}])
    r, notes = go(gh)
    assert not r["ok"] and r["state"] == "parked" and not r.get("gh_failed")
    assert not gh.did("pr merge")
    assert len(notes) == 1 and "#7" in notes[0][1] and "feat/x" in notes[0][1]


def test_one_red_run_still_turns_auto_merge_on():
    r, _ = go(Gh(prs=[{"number": 7, "url": URL}], runs=[{"conclusion": "failure"}]))
    assert r["ok"]


def test_failed_push_stops_before_gh():
    gh = Gh()
    r = ship.auto(".", "main", run=gh, pusher=lambda *a: {"ok": False, "state": "diverged"})
    assert not r["ok"] and not gh.calls


def test_auto_with_tag_is_refused():
    with pytest.raises(SystemExit) as e:
        ship.main(["push", "--auto", "--tag", "v1"])
    assert e.value.code == 2


def test_exit_codes(monkeypatch):
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": False, "state": "parked"})
    assert ship.main(["push", "--auto"]) == 3
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": False, "gh_failed": True, "state": "auto-failed"})
    assert ship.main(["push", "--auto"]) == 1
    monkeypatch.setattr(ship, "auto", lambda *a, **k: {"ok": True, "auto": True, "said": "x"})
    assert ship.main(["push", "--auto"]) == 0
