"""A goal never runs a whole suite on the laptop: touched tests locally, CI green for the rest (owner, 2026-10-27)."""
import base64
import json
import subprocess
import sys

import pytest

from fieldkit.method import suitegate as sg


@pytest.mark.parametrize("cmd,kind", [
    ("go test ./...", "go"),
    ("go test -count=1 ./...", "go"),
    ("$env:X='1'; & go test -count=1 -tags toolbox ./...", "go"),
    ("cargo test", "cargo"),
    ("cargo test --workspace --release", "cargo"),
    ("pytest", "python"),
    ("python -m pytest -q", "python"),
    ("python -m pytest tests/", "python"),
    ("python -m pytest -k slow tests", "python"),
])
def test_whole_suites_are_recognized(cmd, kind):
    assert sg.classify(cmd) == kind


@pytest.mark.parametrize("cmd", [
    "go test -count=1 ./internal/lsp/...",
    "go test ./internal/llm/tools/... -run '(?i)files'",
    "cargo test -p pfind",
    "cargo test --manifest-path tools/pfind-rs/Cargo.toml",
    "python -m pytest tests/test_goapp.py",
    "pytest tests/test_x.py -k y",
    "go build ./...",
    "go vet ./internal/x/...",
])
def test_a_named_package_is_untouched(cmd):
    assert sg.classify(cmd) is None
    c = {"id": "one", "run": cmd}
    assert sg.mark(c, env={}) == (c, None)


def test_mark_rewrites_go_suite_and_keeps_the_original():
    c, warn = sg.mark({"id": "go-test", "run": "go test -count=1 ./...", "expect": {"line": "ok"}}, env={})
    assert warn and "whole go suite" in warn
    assert c["replaced"] == "go test -count=1 ./..." and c["suite"] == "go" and "expect" not in c
    assert c["run"].startswith("python -m fieldkit.method.suitegate run --base origin/main --orig-b64 ")
    b64 = c["run"].rsplit(" ", 1)[-1]
    assert base64.urlsafe_b64decode(b64).decode() == "go test -count=1 ./..."
    assert sg.classify(c["run"]) is None                  # the stand-in is no suite itself


def test_full_suite_env_keeps_it():
    c = {"id": "go-test", "run": "go test ./..."}
    assert sg.mark(c, env={"FIELDKIT_FULL_SUITE": "1"}) == (c, None)
    spec = {"id": "g", "criteria": [c]}
    assert sg.mark_goal(spec, env={"FIELDKIT_FULL_SUITE": "1"}) == (spec, [])


def test_mark_goal_criteria_seed_jobs_and_questions():
    spec = {"id": "g", "objective": "o", "criteria": [
        {"id": "a", "run": "go test ./..."},
        {"id": "b", "run": "go test ./internal/x/..."},
        {"id": "q", "judge": "agent", "question": "is `go test -count=1 ./...` green on Windows?"}],
        "seed_jobs": [{"id": "j", "brief": "b", "cwd": ".", "verify": [{"run": "go test -count=1 ./..."},
                                                                       {"run": "go test ./internal/y/..."}]}]}
    out, warns = sg.mark_goal(spec, env={})
    assert out["criteria"][0]["suite"] == "go"
    assert out["criteria"][1] == spec["criteria"][1]
    assert sg.CI_NOTE in out["criteria"][2]["question"]
    assert out["seed_jobs"][0]["verify"][0]["suite"] == "go"
    assert out["seed_jobs"][0]["verify"][1] == {"run": "go test ./internal/y/..."}
    assert len(warns) == 3
    assert spec["criteria"][0] == {"id": "a", "run": "go test ./..."}       # the input is not changed in place


def test_local_commands_go_touched_packages_and_flags():
    files = ["internal/lsp/uri.go", "internal/lsp/uri_test.go", "internal/llm/tools/shell/x.go", "docs/a.md"]
    cmds = sg.local_commands("go", "go test -count=1 -tags toolbox -v ./...", files)
    assert cmds == [["go", "test", "-count=1", "-tags", "toolbox", "-v",
                     "./internal/llm/tools/shell/...", "./internal/lsp/..."]]
    assert "./..." not in cmds[0]
    assert sg.local_commands("go", "go test ./...", ["README.md"]) == []


def test_local_commands_cargo_touched_crate(tmp_path):
    crate = tmp_path / "tools" / "pfind-rs"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname='p'\n")
    (crate / "src" / "main.rs").write_text("")
    assert sg.local_commands("cargo", "cargo test", ["tools/pfind-rs/src/main.rs"], tmp_path) == [
        ["cargo", "test", "--manifest-path", "tools/pfind-rs/Cargo.toml"]]


def test_local_commands_python_is_testcost_fast():
    [argv] = sg.local_commands("python", "pytest", ["fieldkit/x.py"], ".", base="origin/dev")
    assert argv[1:6] == ["-m", "fieldkit", "testcost", "run", "--fast"] and "origin/dev" in argv


class Fake:
    def __init__(self, files="internal/x/a.go\n", ci=None, test_rc=0):
        self.calls, self.files, self.test_rc = [], files, test_rc
        self.ci = [{"name": "ci", "status": "completed", "conclusion": "success"}] if ci is None else ci

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out, rc = "", 0
        if argv[:1] == ["git"] and "diff" in argv:
            out = self.files
        elif argv[:1] == ["git"] and "rev-parse" in argv:
            out = "abc123def4567890\n"
        elif argv[:1] == ["gh"]:
            out = json.dumps(self.ci)
        elif argv[:2] == ["go", "test"]:
            rc = self.test_rc
        return subprocess.CompletedProcess(argv, rc, out, "")


def test_run_gate_touched_plus_ci_green(tmp_path):
    f, said = Fake(), []
    assert sg.run_gate("go test -count=1 ./...", tmp_path, run=f, env={}, say=said.append) == 0
    assert ["go", "test", "-count=1", "./internal/x/..."] in f.calls
    assert not any("./..." in c for c in f.calls if isinstance(c, list))
    assert any("CI green on abc123def456" in s for s in said)


def test_run_gate_ci_not_green_fails(tmp_path):
    f = Fake(ci=[{"name": "ci", "status": "completed", "conclusion": "failure"}])
    assert sg.run_gate("go test ./...", tmp_path, run=f, env={}, say=lambda s: None) == 3
    assert sg.run_gate("go test ./...", tmp_path, run=Fake(ci=[]), env={}, say=lambda s: None) == 3


def test_run_gate_touched_failure_fails(tmp_path):
    assert sg.run_gate("go test ./...", tmp_path, run=Fake(test_rc=1), env={}, say=lambda s: None) == 1


def test_run_gate_full_suite_env_runs_the_original(tmp_path):
    f = Fake()
    assert sg.run_gate("go test ./...", tmp_path, run=f, env={"FIELDKIT_FULL_SUITE": "1"}, say=lambda s: None) == 0
    assert f.calls == ["go test ./..."]


def test_asks_full_suite():
    assert sg.asks_full_suite("`go test -count=1 ./...` is green on Windows")
    assert sg.asks_full_suite("the full suite passes")
    assert not sg.asks_full_suite("does `go test ./internal/x/...` pass?")


def test_check_cli_exit_codes():
    r = subprocess.run([sys.executable, "-m", "fieldkit.method.suitegate", "check", "go test ./..."],
                       capture_output=True, text=True)
    assert r.returncode == 3 and r.stdout.strip() == "go"
    r = subprocess.run([sys.executable, "-m", "fieldkit.method.suitegate", "check", "go test ./internal/a/..."],
                       capture_output=True, text=True)
    assert r.returncode == 0


def test_goal_new_warns_and_rewrites(tmp_path, monkeypatch, capsys):
    from fieldkit import cli
    from fieldkit.autopilot import goal
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    monkeypatch.delenv("FIELDKIT_FULL_SUITE", raising=False)
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"objective": "o", "cwd": str(tmp_path), "criteria": [
        {"id": "all", "run": "go test -count=1 ./..."}, {"id": "one", "run": "go test ./internal/a/..."}]}))
    assert cli.main(["goal", "new", "g", "--from", str(spec)]) == 0
    assert "WARNING criterion all:" in capsys.readouterr().out
    g = json.loads((tmp_path / "goals" / "g" / "goal.json").read_text())
    assert g["criteria"][0]["replaced"] == "go test -count=1 ./..." and "suitegate run" in g["criteria"][0]["run"]
    assert g["criteria"][1] == {"id": "one", "run": "go test ./internal/a/..."}
