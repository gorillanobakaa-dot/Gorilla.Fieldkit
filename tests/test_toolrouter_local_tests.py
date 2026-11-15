"""TESTS ONCE (owner, 2026-11-15): a test runner typed in a shell is refused on every system; GitHub CI runs the tests.
The owner's switch FIELDKIT_LOCAL_TESTS=1 opens it; `# toolrouter-ok:` does not."""
import pytest

from fieldkit.autopilot import toolrouter as tr


@pytest.fixture(autouse=True)
def _no_switch(monkeypatch):
    monkeypatch.delenv(tr.LOCAL_TESTS_ENV, raising=False)


@pytest.mark.parametrize("cmd", [
    "python -m pytest -q tests/test_x.py",
    ".venv/bin/python -m pytest tests",
    "cd x && pytest -q",
    "python3 -m unittest discover",
    "go test ./...",
    "cargo test --all",
    "npm test",
    "pnpm run test",
    "npx vitest run",
    'bash -c "pytest -q"',
    "python -m pytest -q  # toolrouter-ok: the override does not open the tests rule",
])
def test_a_test_runner_is_refused_on_every_system(cmd, monkeypatch):
    for plat in ("linux", "win32", "darwin"):
        monkeypatch.setattr(tr.sys, "platform", plat)
        assert tr.route(cmd)["route"] == "tests-run-in-github-ci", (cmd, plat)


@pytest.mark.parametrize("cmd", [
    "grep -n pytest tests/x.py",
    'echo "then go test ./... in CI" >> notes.md',
    'git commit -m "run pytest in CI"',
    "python -m pytest_plugins_list",
    "fieldkit testcost staged",
    "cargo build --release",
    "fieldkit mutate --plan tests/x_mutations.yaml -- python -m pytest -q tests/test_x.py",
])
def test_words_and_other_commands_are_not_test_runs(cmd, monkeypatch):
    monkeypatch.setattr(tr.sys, "platform", "linux")
    r = tr.route(cmd)
    assert not r or r["route"] != "tests-run-in-github-ci", cmd


def test_the_owner_switch_opens_it(monkeypatch):
    monkeypatch.setattr(tr.sys, "platform", "linux")
    assert tr.route("FIELDKIT_LOCAL_TESTS=1 python -m pytest tests/test_a.py") is None
    monkeypatch.setenv(tr.LOCAL_TESTS_ENV, "1")
    assert tr.route("python -m pytest tests/test_a.py") is None


def test_the_refusal_names_ci_and_the_switch():
    r = tr.local_tests_refusal("pytest", environ={})
    assert "GitHub CI" in r["use"] and tr.LOCAL_TESTS_ENV in r["use"]
