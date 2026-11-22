"""CI hangs (fieldkit/method/cihang.py, CI-SPEED-PLAN C6): a bounded timeout, one re-run, then quarantine."""
import importlib.util
from pathlib import Path

import pytest

from fieldkit.method import cihang, ciplan

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_tests", ROOT / ".github" / "ci_tests.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

HUNG = """tests/test_a.py::test_ok PASSED
tests/test_b.py::test_slow
+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++
"""
CLEAN = "tests/test_a.py::test_ok PASSED\ntests/test_b.py::test_fast PASSED\n"


def test_timeout_follows_p90_and_is_clamped():
    assert cihang.timeout_for({}) == 300                                        # no durations: the default
    assert cihang.timeout_for({"a": 1, "b": 2}) == 120                          # 10 x p90 = 20, floor 120
    assert cihang.timeout_for({"a": 30}) == 300                                 # 10 x 30
    assert cihang.timeout_for({"a": 600}) == 900                                # cap
    assert cihang.timeout_for({"a": 30}, {"factor": 2, "floor": 10}) == 60      # config wins


def test_hung_node_is_the_last_test_that_never_finished():
    assert cihang.hung_node(HUNG) == "tests/test_b.py::test_slow"
    assert cihang.hung_node(CLEAN) is None
    assert cihang.hung_node("tests/test_b.py::test_x FAILED\n") is None          # a plain failure is not a hang
    assert cihang.hung_node(HUNG.replace("tests\\", "tests/")) == "tests/test_b.py::test_slow"


def test_other_failures_are_not_hidden_by_a_rerun():
    assert cihang.other_failures("tests/test_a.py::test_x FAILED\n" + HUNG)
    assert not cihang.other_failures(HUNG)


def test_quarantine_loads_deselects_and_blocks_release(tmp_path, capsys):
    f = tmp_path / "q.yaml"
    assert cihang.load_quarantine(f) == {}
    assert cihang.main(["--check", "--file", str(f)]) == 0
    f.write_text('tests:\n  "tests/test_b.py::test_slow": "hung twice"\n', encoding="utf-8")
    q = cihang.load_quarantine(f)
    assert cihang.deselect_args(q, ["tests/test_b.py"]) == ["--deselect", "tests/test_b.py::test_slow"]
    assert cihang.deselect_args(q, ["tests/test_a.py"]) == []
    assert cihang.main(["--file", str(f)]) == 0 and cihang.main(["--check", "--file", str(f)]) == 3
    assert "release is blocked" in capsys.readouterr().out
    f.write_text("tests:\n  x.py::t: ''\n", encoding="utf-8")                  # no reason given: fails loud
    assert cihang.main(["--check", "--file", str(f)]) == 3
    assert ciplan.main(["quarantine", "--check", "--file", str(f)]) == 3        # reachable as `fieldkit ci quarantine`


def test_the_repo_quarantine_file_parses_and_ci_config_has_hang():
    assert cihang.load_quarantine(ROOT / ".github" / "quarantine.yaml") is not None
    assert set(ciplan.load_config(ROOT / ".github" / "ci.yaml")["hang"]) == {"factor", "floor", "cap", "default"}


@pytest.fixture
def actions(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    return ci.shard_log("1/2")


def _runner(log, outcomes):
    seen = iter(outcomes)

    def run(cmd, tests, shard):
        rc, text = next(seen)
        log.write_text(text, encoding="utf-8")
        return rc
    return run


def test_a_hang_gets_one_rerun_and_a_pass_is_green(actions, monkeypatch, capsys):
    monkeypatch.setattr(ci, "run_tests", _runner(actions, [(1, HUNG), (0, CLEAN)]))
    assert ci.run_checked(["pytest"], [], "1/2") == 0
    assert "HANG-FLAKE tests/test_b.py::test_slow" in capsys.readouterr().out


def test_the_same_hang_twice_is_red_and_names_the_quarantine_line(actions, monkeypatch, capsys):
    monkeypatch.setattr(ci, "run_tests", _runner(actions, [(1, HUNG), (1, HUNG)]))
    assert ci.run_checked(["pytest"], [], "1/2") == 1
    out = capsys.readouterr().out
    assert "HANG-QUARANTINE tests/test_b.py::test_slow" in out and ".github/quarantine.yaml" in out


def test_a_real_failure_is_never_rerun(actions, monkeypatch):
    calls = []
    monkeypatch.setattr(ci, "run_tests", lambda *a: (calls.append(1), actions.write_text("tests/test_a.py::t FAILED\n"), 1)[2])
    assert ci.run_checked(["pytest"], [], "1/2") == 1 and len(calls) == 1


def test_xdist_worker_crash_names_the_hung_test():
    log = ("+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++\n"
           "[gw2] node down: Not properly terminated\n"
           "worker 'gw2' crashed while running 'tests/test_b.py::test_slow'\n")
    assert cihang.hung_node(log) == "tests/test_b.py::test_slow"
