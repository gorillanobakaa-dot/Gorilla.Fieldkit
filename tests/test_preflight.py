"""The ONE run guard (fieldkit/core/preflight.py): checks, STOP and waiting, the sampler, the end line, the call
sites. Run: python -m pytest -q tests/test_preflight.py"""
import sys
import types

import pytest

from fieldkit.core import preflight

GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _no_outer_guard(monkeypatch):
    # the suite itself runs under the guard (pre-commit, fieldkit mutate): its marker is inherited by this process
    monkeypatch.delenv(preflight.NESTED, raising=False)


@pytest.fixture
def fake_steps():
    mod = types.ModuleType("fake_steps")
    mod.ok = lambda ctx: ("ok", f"fine for {ctx['label']}")
    mod.warn = lambda ctx: ("warn", "RAM high")
    mod.stop = lambda ctx: ("stop", "free RAM under the floor")
    mod.boom = lambda ctx: 1 / 0
    mod.odd = lambda ctx: ("maybe", "an unknown status")
    sys.modules["fake_steps"] = mod
    yield mod
    del sys.modules["fake_steps"]


def test_default_steps_are_the_hog():
    assert preflight.STEPS == ["fieldkit.hog:preflight_check"]


def test_skipped_inside_pytest_without_explicit_steps():
    said = []
    assert preflight.run("t", say=said.append)["skipped"] and not said
    with preflight.guard("t", say=said.append) as g:
        assert g is None


def test_status_is_the_worst_and_only_non_ok_lines_are_said(fake_steps):
    said = []
    r = preflight.run("x", say=said.append, steps=["fake_steps:ok", "fake_steps:warn"])
    assert r["status"] == "warn" and r["lines"] == ["RAM high"]
    assert said[0] == "  preflight: RAM high" and said[-1].startswith("  preflight x: WARN (")


def test_a_broken_check_is_a_warning_never_a_stop(fake_steps):
    r = preflight.run("x", say=lambda s: None, steps=["fake_steps:boom", "fieldkit.nope:x", "fake_steps:odd"])
    assert r["status"] == "warn"
    assert r["lines"][0].startswith("fake_steps:boom: not run (division by zero")
    assert r["lines"][1].startswith("fieldkit.nope:x: not run")


def test_stop_waits_then_gives_up(fake_steps):
    t = [0.0]
    said = []
    r = preflight.run("x", say=said.append, steps=["fake_steps:stop"], wait_s=60, poll_s=30,
                      clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert r["status"] == "stop" and t[0] == 60
    assert sum("waiting" in s for s in said) == 2


def test_stop_clears_while_waiting(fake_steps):
    calls = []

    def flaky(ctx):
        calls.append(1)
        return ("stop", "low") if len(calls) < 2 else ("ok", "fine")
    fake_steps.flaky = flaky
    t = [0.0]
    r = preflight.run("x", say=lambda s: None, steps=["fake_steps:flaky"], wait_s=600, poll_s=30,
                      clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert r["status"] == "ok" and len(calls) == 2


def test_guard_refuses_on_stop_and_runs_on_warn(fake_steps):
    with pytest.raises(preflight.Refused, match="under the floor"):
        with preflight.guard("x", say=lambda s: None, steps=["fake_steps:stop"]):
            pytest.fail("the block must not run")
    ran = []
    s = preflight.Sampler(ram=lambda: (10 * GB, 64 * GB))
    with preflight.guard("x", say=lambda s: None, steps=["fake_steps:warn"], sampler=s):
        ran.append(1)
    assert ran == [1]


def test_register_adds_a_check_once(fake_steps):
    try:
        preflight.register("testcost", fake_steps.warn)
        preflight.register("testcost", fake_steps.ok)
        names = [n for n, _ in preflight._checks()]
        assert names.count("testcost") == 1 and names[0] == "fieldkit.hog:preflight_check"
    finally:
        preflight._REGISTERED.clear()


def test_sampler_peaks_reclaim_and_capped_seconds():
    ram = iter([(10 * GB, 64 * GB), (30 * GB, 64 * GB), (20 * GB, 64 * GB)])
    temps = iter([60.0, 88.0, 70.0])
    t = [0.0]
    s = preflight.Sampler(sample_s=15, act_s=20, ram=lambda: next(ram), temp=lambda: next(temps),
                          act=lambda: 3 * GB, clock=lambda: t[0])
    s.governor = types.SimpleNamespace(cap=80, start_cap=100)
    for dt in (0, 15, 15):
        t[0] += dt
        s.tick(dt)
    assert s.peak_ram == 30 * GB and s.peak_temp == 88.0 and s.capped_s == 30 and s.reclaimed == 3 * GB
    t[0] = 120
    assert s.line("cargo") == ("  run guard cargo: 2.0 min, peak RAM 30.0/64.0 GB, peak CPU 88 C, reclaimed 3.0 GB, "
                               "30 s capped")


def test_sampler_counts_no_cap_at_the_start_cap():
    s = preflight.Sampler(ram=lambda: (1, 2))
    s.governor = types.SimpleNamespace(cap=99, start_cap=99)
    s.tick(15)
    assert s.capped_s == 0
    s2 = preflight.Sampler(ram=lambda: (1, 2))
    assert s2.line("x").endswith("peak CPU not readable, reclaimed 0.0 GB, no governor")


def test_nested_guard_is_one_guard(fake_steps, monkeypatch):
    monkeypatch.setenv(preflight.NESTED, "outer")
    with preflight.guard("inner", say=lambda s: pytest.fail(s), steps=["fake_steps:stop"]) as g:
        assert g is None


def test_call_returns_or_refuses(fake_steps):
    said = []
    assert preflight.call("x", "test", lambda a, b=0: a + b, 2, b=3, say=said.append, refused=-1) == 5
    preflight.STEPS.append("fake_steps:stop")
    try:
        import os
        os.environ.pop("PYTEST_CURRENT_TEST")         # make the default steps run, for this check only
        try:
            assert preflight.call("x", "test", lambda: 5, say=said.append, refused=-1) == -1
            with pytest.raises(KeyError):
                preflight.call("x", "test", lambda: 5, say=said.append, refused=lambda why: {}[why])
        finally:
            os.environ["PYTEST_CURRENT_TEST"] = "test_preflight.py::test_call_returns_or_refuses"
    finally:
        preflight.STEPS.remove("fake_steps:stop")
    assert any(s.startswith("REFUSED by the preflight (x): ") for s in said)


@pytest.mark.real_build_guard
def test_buildguard_runs_inside_the_run_guard(monkeypatch):
    from fieldkit.thermal import buildguard
    seen = []

    class G:
        def __init__(self, label, kind, say):
            seen.append((label, kind))

        def __enter__(self):
            return None

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(preflight, "guard", G)
    monkeypatch.setattr(buildguard, "fan_check", lambda: (False, "fan check refused (test)"))
    with pytest.raises(buildguard.BuildRefused, match="fan check refused"):
        with buildguard.guard(say=lambda s: None, label="cargo", check_fan=True):
            pass
    assert seen == [("cargo", "build")]


@pytest.mark.real_build_guard
def test_buildguard_turns_a_preflight_stop_into_build_refused(monkeypatch):
    from contextlib import contextmanager
    from fieldkit.thermal import buildguard

    @contextmanager
    def stop(label, kind, say):
        raise preflight.Refused("free RAM under the floor")
        yield
    monkeypatch.setattr(preflight, "guard", stop)
    with pytest.raises(buildguard.BuildRefused, match="preflight: free RAM under the floor"):
        with buildguard.guard(say=lambda s: None, label="cargo"):
            pass


def test_call_sites_are_guarded(monkeypatch, tmp_path):
    """mutate, pipeline, autopilot jobs and the leak gate go through preflight.call; the pre-commit hook through the
    module. A STOP comes back as each site's own refusal."""
    seen = []

    def fake_call(label, kind, fn, *a, say=print, wait_s=0, refused=None, **kw):
        seen.append((label.split()[0], kind, wait_s))
        return refused(f"stop at {label}") if callable(refused) else refused
    monkeypatch.setattr(preflight, "call", fake_call)
    from fieldkit.autopilot import job
    from fieldkit.leakgate import gate
    from fieldkit.method import mutate
    r = job.run_job(tmp_path / "j1", "BRIEF", str(tmp_path), say=lambda s: None)
    assert r == {"status": "failed", "rc": 2, "why": "preflight: stop at job j1"}
    with pytest.raises(RuntimeError, match="preflight refused the run"):
        gate.run("z", "o", "t", "u", "w", say=lambda s: None)
    assert mutate.main(["fieldkit/hog/rules.py", "--swap", "a", "b", "--", "python", "-c", "0"]) == 2
    assert seen == [("job", "job", 600), ("leak", "gate", 600), ("fieldkit", "test", 0)]
    from pathlib import Path
    hook = (Path(__file__).resolve().parents[1] / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert '-m fieldkit.core.preflight --label pre-commit --kind test -- "$@"' in hook
    assert 'if ! guarded "$PY" -m pytest' in hook
