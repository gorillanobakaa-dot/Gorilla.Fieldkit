"""gather --check is an offline, read-only check; a failed git pull or clone is an error, never a silent
fall-back to the old copy. git and gh are faked: no network, no real repository is touched."""
import json
from types import SimpleNamespace

import pytest

from fieldkit import gather


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(gather, "TOOLBOX", tmp_path / "toolbox")
    monkeypatch.setattr(gather, "CLONES", tmp_path / "_sources" / "repos")
    calls = []
    answers = {}

    def run(cmd, **kw):
        calls.append(list(cmd))
        key = "pull" if "pull" in cmd else "clone" if "clone" in cmd else "rev-parse" if "rev-parse" in cmd else "?"
        rc, out, err = answers.get(key, (0, "abc1234\n" if key == "rev-parse" else "", ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    monkeypatch.setattr(gather.subprocess, "run", run)
    src = {"name": "demo", "github": "owner/demo", "include": ["**/*"], "exclude": []}
    return SimpleNamespace(tmp=tmp_path, calls=calls, answers=answers, src=src,
                           clone=tmp_path / "_sources" / "repos" / "demo")


def _network(calls):
    return [c for c in calls if "pull" in c or "clone" in c or "fetch" in c]


def _make_clone(world, text="print(1)\n"):
    (world.clone / ".git").mkdir(parents=True)
    (world.clone / "tool.py").write_text(text)


def test_check_never_pulls_even_when_online(world):
    _make_clone(world)
    gather.gather_one(dict(world.src), offline=True)            # an earlier gather, from the clone on disk
    world.calls.clear()
    (world.clone / "tool.py").write_text("print(2)\n")          # the clone moved on since
    r = gather.check_one(dict(world.src), offline=False)       # CLI passes offline=False when online
    assert _network(world.calls) == []
    assert r["mode"] == "offline check" and r["status"] == "drift" and r["changed_at_source"] == ["tool.py"]


def test_check_without_a_clone_creates_nothing(world, monkeypatch):
    world.src["name"] = "never"
    (world.tmp / "toolbox" / "never").mkdir(parents=True)
    (world.tmp / "toolbox" / "never" / "PROVENANCE.json").write_text(json.dumps({"files": {}}))
    r = gather.check_one(dict(world.src), offline=False)
    assert r["status"] == "source-unavailable" and r["mode"] == "offline check"
    assert not (world.tmp / "_sources").exists() and _network(world.calls) == []


def test_gather_check_through_the_front_door_is_offline(world, monkeypatch):
    _make_clone(world)
    gather.gather_one(dict(world.src), offline=True)
    world.calls.clear()
    monkeypatch.setattr(gather, "load_manifest", lambda: [dict(world.src)])
    rows = gather.gather(check=True, offline=False)
    assert rows[0]["status"] == "in-sync" and _network(world.calls) == []


def test_failed_pull_is_an_error_not_the_old_copy(world, monkeypatch):
    _make_clone(world)
    gather.gather_one(dict(world.src), offline=True)
    before = (world.tmp / "toolbox" / "demo" / "PROVENANCE.json").read_text()
    world.answers["pull"] = (1, "", "fatal: unable to access 'https://github.com/owner/demo/': Could not resolve host")
    with pytest.raises(RuntimeError, match="git pull failed"):
        gather.gather_one(dict(world.src), offline=False)
    assert (world.tmp / "toolbox" / "demo" / "PROVENANCE.json").read_text() == before   # left as it was
    monkeypatch.setattr(gather, "load_manifest", lambda: [dict(world.src)])
    rows = gather.gather(offline=False)
    assert "git pull failed" in rows[0]["error"]               # cli turns an "error" row into exit 3


def test_failed_clone_is_an_error(world, monkeypatch):
    world.answers["clone"] = (1, "", "could not resolve host")
    monkeypatch.setattr(gather, "load_manifest", lambda: [dict(world.src)])
    rows = gather.gather(offline=False)
    assert "clone failed" in rows[0]["error"]


def test_successful_pull_is_used(world):
    _make_clone(world)
    r = gather.gather_one(dict(world.src), offline=False)
    assert any("pull" in c for c in world.calls) and r["files"] == 1 and r["commit"] == "abc1234"
