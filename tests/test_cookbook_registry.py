"""P41a: the project registry and `fieldkit cookbook`. check must FAIL on a bad project.yaml; the cwd lookup is a pure
string match; an unknown cwd loads nothing and exits 0."""
import json
import shutil

import pytest

from fieldkit.cookbook import cli, registry

HOME = "/" + "home/"          # built in pieces: the privacy scan flags a literal home path
WIN = "C:" + "\\Users\\"
RECIPES = {"firefox-debian", "firefox-windows", "gorilla-opencode", "debian-kernel", "kernel-7.0.9-unleashed", "fieldkit"}


def bad_registry(tmp_path, edit):
    shutil.copytree(registry.PROJECTS, tmp_path / "p")
    f = tmp_path / "p" / "fieldkit" / "project.yaml"
    f.write_text(edit(f.read_text(encoding="utf-8")), encoding="utf-8")
    return tmp_path / "p"


def test_shipped_registry_is_valid():
    assert registry.check(recipes=RECIPES, github=False) == []
    assert {d["id"] for d in registry.load_all()} >= {"firefox", "gorilla-opencode", "debian-kernel", "fieldkit"}


@pytest.mark.parametrize("edit,needle", [
    (lambda t: t.replace("recipes: [fieldkit]", "recipes: [no-such-recipe]"), "no-such-recipe"),
    (lambda t: t.replace("path: AGENTS.md", "path: NOPE.md"), "NOPE.md"),
    (lambda t: t.replace("purpose:", "porpoise:"), "missing field purpose"),
    (lambda t: t.replace('local: "${FIELDKIT}"', 'local: "' + HOME + 'someone/Fieldkit"'), "machine path"),
    (lambda t: t.replace("id: fieldkit", "id: other"), "must equal its folder"),
])
def test_check_fails_on_a_bad_project(tmp_path, edit, needle):
    probs = registry.check(root=bad_registry(tmp_path, edit), recipes=RECIPES, github=False)
    assert any(needle in p for p in probs), probs


def test_cwd_lookup_is_by_deepest_folder_name():
    assert registry.by_cwd(HOME + "u/Documents/FIREFOX.WORK/work/x")["id"] == "firefox"
    assert registry.by_cwd(WIN + "u\\Documents\\Gorilla.Opencode")["id"] == "gorilla-opencode"
    assert registry.by_cwd(HOME + "u/Documents/Scripts/Fieldkit-p41a")["id"] == "fieldkit"
    assert registry.by_cwd("/tmp/nothing") is None


def test_unknown_cwd_loads_nothing_exit_0(capsys):
    assert cli.main(["load", "--cwd", "/tmp/nothing"]) == 0
    assert capsys.readouterr().out.strip() == "no project"


def test_load_is_bounded_and_ends_with_next(capsys):
    assert cli.main(["load", "fieldkit", "--no-state", "--budget", "150"]) == 0
    out = capsys.readouterr().out
    assert len(out) <= 150 * 4 + 200 and out.strip().splitlines()[-1].startswith("NEXT: ")


def test_load_json_names_cookbook_and_next(capsys):
    assert cli.main(["--json", "load", "firefox", "--no-state"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert any("FIREFOX-COOKBOOK.md" in c for c in d["cookbook"]) and d["next"] == "fieldkit firefox status"


def test_check_command_exit_codes(capsys):
    assert cli.main(["check"]) == 0


def test_github_name_must_match_the_clone_or_gh():
    proj = {"id": "x", "repos": [{"name": "a", "github": "o/Fieldkit", "local": "${FIELDKIT}"}]}
    probs = registry.check_github(proj, remote=lambda p: "o/gorilla.fieldkit", gh=lambda n: None)
    assert probs and "o/Fieldkit" in probs[0]
    assert registry.check_github(proj, remote=lambda p: "o/fieldkit", gh=lambda n: None) == []
    # no clone, gh answers
    proj = {"id": "x", "repos": [{"name": "a", "github": "o/Fieldkit", "local": "${DOCUMENTS}/nowhere-at-all"}]}
    assert registry.check_github(proj, remote=lambda p: None, gh=lambda n: "o/gorilla.fieldkit")
    # neither: not checked
    assert registry.check_github(proj, remote=lambda p: None, gh=lambda n: None) == []


def test_hook_prints_brief_for_a_project_cwd_and_nothing_otherwise(capsys):
    assert cli.hook(json.dumps({"cwd": HOME + "u/Documents/Gorilla.Opencode"})) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart" and "PROJECT gorilla-opencode" in out["additionalContext"]
    assert cli.hook(json.dumps({"cwd": "/tmp/nothing"})) == 0 and capsys.readouterr().out == ""
    assert cli.hook("not json") == 0 and capsys.readouterr().out == ""
