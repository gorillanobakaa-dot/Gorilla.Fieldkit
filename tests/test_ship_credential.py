"""Inside an autopilot run only fieldkit ship gets the GitHub credential back (owner, 2026-10-28, inbox #145: "Yes,
ship only"). Both ways: ship's own git and gh calls find the credential where git and gh keep it; the run's own
environment and every other command stay without one. Fixture environments only."""
import os

import pytest

from fieldkit.autopilot import hardlines as hl
from fieldkit.autopilot import run
from fieldkit.method import ship, train


@pytest.fixture
def in_run(tmp_path, monkeypatch):
    """os.environ as an autopilot run has it, built by run.isolated_env from an owner's environment."""
    monkeypatch.setattr(run, "state_dir", lambda: tmp_path)
    owner = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "APPDATA": str(tmp_path / "AppData"),
             "GH_CONFIG_DIR": str(tmp_path / "owner-gh")}
    iso = run.isolated_env(owner)
    monkeypatch.setattr(os, "environ", dict(iso))
    return iso


def test_the_run_itself_still_has_no_credential(in_run, tmp_path):
    assert in_run["GH_TOKEN"] == "" and in_run["GIT_CONFIG_PARAMETERS"] == "'credential.helper='"
    assert "GIT_CONFIG_VALUE_0" not in in_run and in_run["GH_CONFIG_DIR"] == str(tmp_path / "no-credentials")
    assert in_run["FIELDKIT_REAL_GH_CONFIG_DIR"] == str(tmp_path / "owner-gh")      # a folder name, not a secret


def test_ship_gets_git_and_gh_their_own_credential_places_back(in_run, tmp_path):
    env = ship.ship_env()
    assert env["GH_CONFIG_DIR"] == str(tmp_path / "owner-gh")
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0",
              "GIT_CONFIG_VALUE_0", "GIT_CONFIG_PARAMETERS", "FIELDKIT_REAL_GH_CONFIG_DIR"):
        assert k not in env, k
    assert env["FIELDKIT_SHIP_PUSH"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"     # still never prompts
    os.environ["GITHUB_TOKEN"] = "set-before-ship"                                   # not a blank of the run's
    assert ship.ship_env()["GITHUB_TOKEN"] == "set-before-ship"


def test_without_a_recorded_folder_gh_uses_its_default(in_run, monkeypatch, tmp_path):
    os.environ["FIELDKIT_REAL_GH_CONFIG_DIR"] = ""
    want = (os.path.join(str(tmp_path / "AppData"), "GitHub CLI") if os.name == "nt"
            else os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh"))
    assert ship.ship_env()["GH_CONFIG_DIR"] == want


def test_outside_a_run_and_for_settings_the_run_did_not_blank_nothing_changes(monkeypatch):
    mine = {"PATH": "p", "GH_TOKEN": "", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
            "GIT_CONFIG_VALUE_0": ""}
    monkeypatch.setattr(os, "environ", dict(mine))
    env = ship.ship_env()
    assert {k: env[k] for k in mine} == mine and "GH_CONFIG_DIR" not in env           # not a run: untouched
    monkeypatch.setattr(os, "environ", dict(mine, FIELDKIT_AUTOPILOT="1", GIT_CONFIG_KEY_0="core.editor",
                                            GIT_CONFIG_VALUE_0="vi"))
    env = ship.ship_env()
    assert env["GIT_CONFIG_KEY_0"] == "core.editor" and env["GIT_CONFIG_VALUE_0"] == "vi"


def test_ships_gh_calls_carry_ships_environment(in_run, monkeypatch, tmp_path):
    seen = []

    class R:
        returncode, stdout, stderr = 0, "https://x/release", ""

    def fake(argv, **kw):
        seen.append((argv[:3], (kw.get("env") or {}).get("GH_CONFIG_DIR")))
        return R()
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    ship.gh_release(str(tmp_path), "v1", mandate=lambda *a, **k: ("allow", "test", None), run=fake)
    gh = train.GH.__new__(train.GH)
    gh.repo, gh.run, gh.slug = str(tmp_path), fake, "o/r"
    gh.merge(7, "abc")
    assert seen == [(["gh", "release", "create"], str(tmp_path / "owner-gh")),
                    (["gh", "pr", "merge"], str(tmp_path / "owner-gh"))]


@pytest.mark.parametrize("cmd", [
    "GH_CONFIG_DIR=~/.config/gh gh pr merge 7 --merge",
    "export GH_TOKEN=abc && gh pr view 7",
    "$env:GH_CONFIG_DIR = 'D:\\gh'; gh release create v1",
    "git -c credential.helper=store fetch origin",
    "gh auth token",
    "gh auth setup-git",
    "unset GIT_CONFIG_COUNT GIT_CONFIG_KEY_0; git fetch origin",
    "GIT_CONFIG_PARAMETERS= git fetch origin",
    "unset GIT_CONFIG_PARAMETERS; git fetch origin",
    "FIELDKIT_REAL_GH_CONFIG_DIR=x python -m fieldkit ship push",
])
def test_any_other_way_to_the_credential_stays_refused(cmd):
    d, why = hl.decide("Bash", {"command": cmd})
    assert d == "deny" and why.startswith("HARD LINE"), (cmd, why)


@pytest.mark.parametrize("cmd", ["echo $GH_CONFIG_DIR", "gh auth status", "gh pr view 7 --json state",
                                 "git status --short"])
def test_reading_about_the_credential_is_not_rearming_it(cmd):
    assert hl.decide("Bash", {"command": cmd})[0] == "allow", cmd


def test_b1_git_works_in_a_run_even_where_empty_variables_vanish(in_run, tmp_path):
    """B1 (2026-10-31): Windows drops a variable set to "", so GIT_CONFIG_VALUE_0="" left git failing on every call
    and the hook's mandate refused each headless `fieldkit ship push`. Drop every empty value, as Windows does: git
    still reads origin, and the credential helpers stay reset (the last helper value is empty)."""
    import subprocess
    env = {k: v for k, v in in_run.items() if v != ""}
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://example.invalid/o/R.git"], check=True,
                   env=env)
    r = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"], capture_output=True, text=True, env=env)
    assert r.returncode == 0 and r.stdout.strip().endswith("/o/R.git"), r.stderr
    h = subprocess.run(["git", "-C", str(repo), "config", "--get-all", "credential.helper"], capture_output=True,
                       text=True, env=env)
    assert h.stdout.splitlines()[-1] == "", h.stdout


@pytest.mark.parametrize("cmd", ["fieldkit ship push", "fieldkit ship push --auto",
                                 'fieldkit ship push --auto --title "rules: lint" --body-file C:/x/b.md'])
def test_b1_a_headless_run_pushes_like_a_session(cmd):
    class Door:
        def decide(self, action, **kw):
            return "allow", "owner-wide: test", None

        def log(self, *a):
            pass

        def in_force(self):
            return True
    assert hl.decide("Bash", {"command": cmd}, cwd=".", door=Door())[0] == "allow", cmd
    assert hl.decide("Bash", {"command": cmd + "; git push"}, cwd=".", door=Door())[0] == "deny"
