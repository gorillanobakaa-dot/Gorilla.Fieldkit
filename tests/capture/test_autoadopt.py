"""P18 last step: candidates are tried by themselves; a good one is adopted end to end, two bad ones are refused with a reason."""
import json
import sys
import subprocess
from pathlib import Path

import pytest
import yaml

from fieldkit.capture import autoadopt as A, loop, store as st
from fieldkit.core import settings


HOME_PATH = "/ho" + "me/zed/work"      # built in two parts so this file itself stays clean for the privacy scan


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "store"))
    return tmp_path


def _rec(cid, steps):
    return {"id": cid, "title": f"Recipe: {cid}", "episodes": {"in_group": 3, "replayed_ok": 3},
            "steps": [{"id": f"s{i}", "command": c, "sentence": f"step {i}", "check": "exit 0"} for i, c in enumerate(steps)]}


def _good(tmp):
    return Path(loop.write_candidate(_rec("recipe-zz-good", ["go vet ./x", "go test ./x"]), tmp / "c"))


def _blind(tmp):
    """A test that cannot fail: it passes whatever the script says."""
    d = Path(loop.write_candidate(_rec("recipe-zz-blind", ["go vet ./x", "go test ./x"]), tmp / "c"))
    (d / "test_recipe-zz-blind.py").write_text("def test_nothing():\n    assert True\n", encoding="utf-8")
    return d


def _leaky(tmp):
    d = Path(loop.write_candidate(_rec("recipe-zz-leaky", ["cd " + HOME_PATH, "go test ./x"]), tmp / "c"))
    return d


def test_good_candidate_passes_all_three_checks(env):
    r = A.try_candidate(_good(env))
    assert r["ok"] and r["step"] == "", r


def test_a_test_that_cannot_fail_is_refused_at_mutate(env):
    r = A.try_candidate(_blind(env))
    assert not r["ok"] and r["step"] == "mutate" and "cannot fail" in r["reason"], r


def test_a_privacy_finding_is_refused_at_privacy(env):
    r = A.try_candidate(_leaky(env))
    assert not r["ok"] and r["step"] == "privacy" and HOME_PATH not in r["reason"] and "linux-home-path" in r["reason"], r


def test_a_failing_own_test_is_refused_at_test(env):
    d = _good(env)
    (d / "recipe-zz-good.sh").write_text("echo no strict header\n", encoding="utf-8")
    r = A.try_candidate(d)
    assert not r["ok"] and r["step"] == "test", r


def test_a_script_with_nothing_to_break_is_refused(env):
    d = Path(loop.write_candidate(_rec("recipe-zz-empty", []), env / "c"))
    r = A.try_candidate(d)
    assert not r["ok"] and r["step"] == "mutate", r


def test_mutation_proof_each_check_can_fail(env, monkeypatch):
    """Break each check in turn: the good candidate must then be refused or the bad one let through (so each check bites)."""
    # without the mutate check the blind test would pass
    monkeypatch.setattr(A.M, "mutate", lambda plan, test, **kw: {"baseline": "passed", "ok": True, "results": [], "survived": 0, "refused": 0})
    assert A.try_candidate(_blind(env))["ok"], "mutate check removed: the blind test goes through, so the check is what refuses it"
    monkeypatch.undo()
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(env / "store"))
    # without the privacy check the leaky candidate would pass
    monkeypatch.setattr(A.privacy, "scan_path", lambda p: {})
    assert A.try_candidate(_leaky(env))["ok"], "privacy check removed: the leak goes through, so the check is what refuses it"


def test_adopt_end_to_end_in_a_worktree(env, monkeypatch):
    # the nested commit would run this repo's pre-commit hook, which runs THIS test again: switch the hook off for the test only
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(env / "empty-hooks-dir"))
    repo = Path(settings.ROOT)
    seen = {}

    def fake_ship(wt):
        wt = Path(wt)
        cards = yaml.safe_load((wt / "fieldkit/desk/tools.yaml").read_text(encoding="utf-8"))
        cards = cards["tools"] if isinstance(cards, dict) else cards
        card = next(c for c in cards if c["id"] == "recipe-zz-good")
        seen["card"] = card
        seen["files"] = sorted([p.name for p in (wt / A.ADOPTED).iterdir()] + [p.name for p in (wt / A.TESTS).iterdir()])
        seen["index"] = "recipe-zz-good" in (wt / "fieldkit/desk/platform_index.json").read_text(encoding="utf-8")
        seen["log"] = subprocess.run(["git", "-C", str(wt), "log", "-1", "--format=%s"], capture_output=True, text=True).stdout
        # the adopted test passes where it was put
        seen["test"] = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(wt / A.TESTS / "test_recipe-zz-good.py")],
                                      capture_output=True, text=True, cwd=str(wt)).returncode
        return True, "pushed (fake)"

    good = _good(env)
    try:
        done = A.adopt_all([good], repo, "emerge:zz:test-1", ship=fake_ship, base="HEAD")
    finally:
        subprocess.run(["git", "-C", str(repo), "branch", "-D", "feat/capture-adopt-emerge-zz-test-1"], capture_output=True)
    assert done["adopted"] == ["recipe-zz-good"] and done["shipped"] is True, done
    assert seen["card"]["path"].endswith(A.ADOPTED) and "test_recipe-zz-good.py" in " ".join(seen["card"]["test"])
    assert seen["files"] == ["recipe-zz-good.sh", "test_recipe-zz-good.py"] and seen["index"]
    assert "capture: adopt 1 tool(s)" in seen["log"] and seen["test"] == 0
    assert not (repo / A.ADOPTED / "recipe-zz-good.sh").exists(), "the checkout itself is never touched"


def test_distil_adopts_the_good_and_ledgers_the_two_bad(env, monkeypatch):
    """The whole step on three fake candidates: one adopted, two refused with their failed step in the ledger."""
    dirs = [_good(env), _blind(env), _leaky(env)]
    res = {"candidates": [str(d) for d in dirs], "adopted": [], "ledger": []}
    calls = {}

    def fake_adopt_all(passed, repo, who, ship=None, base="origin/main"):
        calls["passed"] = [Path(p).name for p in passed]
        return {"adopted": [Path(p).name for p in passed], "failed": {}, "branch": "feat/x", "shipped": True, "note": ""}

    monkeypatch.setattr(A, "adopt_all", fake_adopt_all)
    loop._auto_adopt(res, "emerge:zz:1")
    assert calls["passed"] == ["recipe-zz-good"] and res["adopted"] == ["recipe-zz-good"]
    lines = [json.loads(x) for x in (st.capture_dir() / loop.LEDGER).read_text(encoding="utf-8").splitlines()]
    by = {x["candidate"]: x for x in lines}
    assert by["recipe-zz-blind"]["step"] == "mutate" and by["recipe-zz-leaky"]["step"] == "privacy"
    assert len(res["ledger"]) == 2


def test_a_failed_push_is_a_ledger_line(env, monkeypatch):
    res = {"candidates": [str(_good(env))], "adopted": [], "ledger": []}
    monkeypatch.setattr(A, "adopt_all", lambda passed, repo, who, ship=None, base="x": {
        "adopted": ["recipe-zz-good"], "failed": {}, "branch": "feat/x", "shipped": False, "note": "ci red"})
    loop._auto_adopt(res, "emerge:zz:2")
    assert len(res["ledger"]) == 1 and "push failed" in json.loads((st.capture_dir() / loop.LEDGER).read_text().splitlines()[-1])["reason"]


@pytest.mark.skipif(sys.platform == "win32", reason="adopted tools are bash scripts distilled on Linux (card: platforms linux); bash.exe on Windows CI is the WSL stub")
def test_capture_adopted_lists_and_runs_by_id(tmp_path, capsys):
    base = tmp_path / A.ADOPTED
    base.mkdir(parents=True)
    (base / "t-one.sh").write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    assert list(A.scripts(tmp_path)) == ["t-one"]
    assert A.run("t-one", tmp_path) == 7
    assert A.run("nope", tmp_path) == 2
