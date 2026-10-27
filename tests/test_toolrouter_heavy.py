"""Heavy work outside the thermal guard is refused on Windows (2026-10-27: the laptop lost power at ~04:14 while a
session ran a full suite and a mutate at once, both unguarded). Owner: "guard obligatoriu prin tool router"."""
import pytest

from fieldkit.autopilot import toolrouter as tr


@pytest.mark.parametrize("cmd", [
    "python -m pytest -q",
    "python -m pytest -q tests",
    "cd /c/x && .venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/",
    "pytest tests/test_a.py tests/test_b.py tests/test_c.py tests/test_d.py",
    "python -m pytest -n 8 tests/test_a.py",
    "fieldkit mutate tests/x_mutations.yaml",
    "python -m fieldkit mutate --all",
    "fieldkit admit suite",
    "cargo build --release",
    "cargo +nightly test",
    "go test -count=1 ./...",
    "cd opencode && go build ./cmd/opencode",
    "npm run build",
    "npx vite build",
    "cmake --build build -j 8",
    "make -j4",
    "FOO=1 go test ./internal/...",
    "timeout 600 go test ./...",
    "bash -c 'cargo build'",
    "fieldkit admit -- python -m pytest -q tests",
])
def test_heavy_commands_are_refused_on_windows(cmd):
    assert tr.heavy_refusal(cmd, platform="win32") is tr.HEAVY_GUARD


@pytest.mark.parametrize("cmd", [
    "python -m pytest -q tests/test_toolrouter_heavy.py",
    "python -m pytest tests/test_a.py::test_one -k fast",
    "pytest tests/test_a.py tests/test_b.py tests/test_c.py",
    "python -m pytest -q -p no:cacheprovider --basetemp t tests/test_a.py",
    "fieldkit guard -- python -m pytest -q tests",
    "python -m fieldkit guard -- cargo build --release",
    "fieldkit guard -- fieldkit mutate tests/x.yaml",
    "fieldkit task start brief.md --on peer",
    "ssh vaio 'cd fk && python -m pytest -q'",
    "go version",
    "cargo --version",
    "git status && git log -1",
    "echo cargo build is heavy",
    "grep -n 'go test' notes.md",
    'grep -n "telemetry\\|mutate\\|go test\\|cargo" COMMON.md',
    "echo 'then go test ./... in CI' >> notes.md",
    "python -m pytest -q  # toolrouter-ok: none, just a test of the override",
])
def test_light_guarded_or_remote_commands_pass(cmd):
    r = tr.route(cmd) if "toolrouter-ok" in cmd else tr.heavy_refusal(cmd, platform="win32")
    assert r is None, cmd


def test_linux_and_macos_are_not_judged():
    for p in ("linux", "darwin"):
        assert tr.heavy_refusal("python -m pytest -q", platform=p) is None


def test_a_commit_in_a_fieldkit_checkout_runs_the_suite_and_is_refused(tmp_path):
    fk = tmp_path / "Fieldkit"
    (fk / "hooks").mkdir(parents=True)
    (fk / "fieldkit").mkdir()
    (fk / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    other = tmp_path / "other"
    other.mkdir()
    assert tr.heavy_refusal('git commit -m "x"', cwd=str(fk), platform="win32") is tr.HEAVY_GUARD
    assert tr.heavy_refusal(f'git -C "{fk}" commit -m x', cwd=str(other), platform="win32") is tr.HEAVY_GUARD
    assert tr.heavy_refusal(f'cd "{fk}" && git commit -m x', cwd=str(other), platform="win32") is tr.HEAVY_GUARD
    assert tr.heavy_refusal('git commit -m x', cwd=str(other), platform="win32") is None
    assert tr.heavy_refusal('fieldkit guard -- git commit -m x', cwd=str(fk), platform="win32") is None


def test_the_hook_route_refuses_heavy_work_on_windows(monkeypatch):
    monkeypatch.setattr(tr.sys, "platform", "win32")
    assert tr.route("python -m pytest -q tests")["route"] == "heavy-work-outside-the-guard"


def test_a_goal_criterion_on_windows_runs_under_the_guard(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fieldkit.autopilot import goal
    from fieldkit.core import contain
    seen = {}

    def fake(shell, **kw):
        seen["shell"] = shell
        return SimpleNamespace(out="", err="", rc=0, status="ok", killed=[], leftovers=[], why="")
    monkeypatch.setattr(contain, "run", fake)
    from fieldkit.thermal import buildguard
    status = tmp_path / "status.txt"
    status.write_text("mode=5")
    monkeypatch.setattr(buildguard, "STATUS", status)
    monkeypatch.setattr(goal.os, "name", "nt")
    monkeypatch.delenv("FIELDKIT_ADMITTED", raising=False)
    assert goal.run_check({"id": "c", "run": "go test ./x"}, str(tmp_path))["verdict"] == "PASS"
    assert seen["shell"][1:5] == ["-m", "fieldkit", "guard", "--"] and seen["shell"][5] == "powershell"
    monkeypatch.setenv("FIELDKIT_ADMITTED", "1")                     # inside a guard already: no second one
    goal.run_check({"id": "c", "run": "go test ./x"}, str(tmp_path))
    assert seen["shell"][0] == "powershell"
    status.unlink()                                                  # no fan control (CI, the VAIO): bare
    monkeypatch.delenv("FIELDKIT_ADMITTED")
    goal.run_check({"id": "c", "run": "go test ./x"}, str(tmp_path))
    assert seen["shell"][0] == "powershell"


def test_the_refusal_names_the_guarded_form():
    m = tr.message(tr.HEAVY_GUARD)
    assert "fieldkit guard --" in m and "--on peer" in m
