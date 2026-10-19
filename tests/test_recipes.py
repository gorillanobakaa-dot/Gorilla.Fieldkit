"""Recipes: found with the tools, read in full, never run, and held to their contract (fieldkit/desk/recipes.py)."""
import sys

import pytest

from fieldkit import agent, mcp
from fieldkit.core import settings
from fieldkit.desk import cards as cardmod
from fieldkit.desk import readiness
from fieldkit.desk import recipes as recipemod

PY = sys.executable

GOOD = {"id": "old-mac-build", "title": "Build a program for an old Mac running High Sierra",
        "problem": "The binary stops at once on macOS 10.13.", "technique": ["patch the toolchain", "build", "check"],
        "key_facts": ["10.13 lacks a 10.14 function"], "outcome": "ran on the Mac", "learned": "2026-10-19",
        "platforms": ["macos"], "tags": ["sierra"], "pointer": None}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    pub, loc = tmp_path / "recipes.yaml", tmp_path / "local" / "recipes.yaml"
    monkeypatch.setattr(recipemod, "RECIPES", pub)
    monkeypatch.setattr(recipemod, "LOCAL_RECIPES", loc)
    monkeypatch.setattr(recipemod, "LOCAL_DIR", tmp_path / "local" / "recipes")
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(cardmod, "test_results", lambda: {})
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(agent, "BACKUPS", tmp_path / "backups")
    return tmp_path


def _write(path, recipes):
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"recipes": recipes}), encoding="utf-8")   # JSON is YAML


def _tool(**kw):
    c = {"id": "hdmi-status", "title": "Is the TV connected over HDMI right now", "path": None,
         "entry": [PY, "-c", "print('x')"], "inputs": [], "effects": [], "safety": "read-only", "modes": {},
         "tests": ["x"], "platforms": [], "draft": False, "reviewed": True, "portable": True, "probe": "safe",
         "scope": []}
    c.update(kw)
    return c


def test_a_stranger_with_no_local_folder_gets_the_published_recipes_and_nothing_breaks(_isolated):
    # A fresh download of Fieldkit has no local/ at all (the orchestrator's stranger test, 2026-10-19).
    assert not (_isolated / "local").exists()
    assert recipemod.load() == []                                   # no recipes file either: none, no crash
    _write(recipemod.RECIPES, [GOOD])
    assert not (_isolated / "local").exists()
    assert [r["id"] for r in recipemod.load()] == ["old-mac-build"]
    assert recipemod.check(tool_ids=set()) == []
    assert agent.describe("old-mac-build")["trust"] == "recipe"
    r = readiness.report([])
    assert r["recipes"] == {"published": 1, "own": 0}


def test_a_complete_recipe_passes_and_each_missing_part_is_named():
    assert recipemod.check([dict(GOOD, public=True)], tool_ids=set()) == []
    bad = dict(GOOD, public=True, technique=None, outcome="")
    problems = recipemod.check([bad], tool_ids=set())
    assert any("'technique' is missing" in p for p in problems)
    assert any("'outcome' is missing" in p for p in problems)


def test_a_recipe_cannot_take_a_tool_name_or_another_recipe_name():
    problems = recipemod.check([dict(GOOD, public=True), dict(GOOD, public=False)], tool_ids={"old-mac-build"})
    assert any("two recipes share this id" in p for p in problems)
    assert any("a tool already has this id" in p for p in problems)


def test_a_pointer_must_live_inside_fieldkit_and_a_published_one_outside_local(_isolated, tmp_path_factory):
    outside = tmp_path_factory.mktemp("scripts") / "probe.py"
    outside.write_text("print(1)\n")
    problems = recipemod.check([dict(GOOD, public=False, pointer=str(outside))], tool_ids=set())
    assert any("outside Fieldkit" in p for p in problems), problems
    inside = _isolated / "local" / "recipes" / "old-mac-build" / "probe.py"
    inside.parent.mkdir(parents=True)
    inside.write_text("print(1)\n")
    assert recipemod.check([dict(GOOD, public=False, pointer="local/recipes/old-mac-build/probe.py")],
                           tool_ids=set()) == []
    problems = recipemod.check([dict(GOOD, public=True, pointer="local/recipes/old-mac-build/probe.py")],
                               tool_ids=set())
    assert any("never published" in p for p in problems), problems


def test_discover_finds_a_recipe_labels_it_and_puts_a_tool_first_at_the_same_fit():
    rec = dict(GOOD, id="hdmi-notes", title="Is the TV connected over HDMI right now", kind="recipe", public=True)
    hits = agent.discover("is the tv connected over hdmi", cards=[_tool()], recipes=[rec])
    assert [h["tool"] for h in hits] == ["hdmi-status", "hdmi-notes"]
    assert hits[1]["kind"] == "recipe" and hits[1]["trust"] == "recipe" and "never run" in hits[1]["read"]
    assert hits[0]["kind"] == "tool"


def test_describe_prints_the_whole_recipe_and_run_refuses_it(_isolated):
    _write(recipemod.RECIPES, [GOOD])
    d = agent.describe("old-mac-build")
    assert d["trust"] == "recipe"
    for part in ("PROBLEM:", "TECHNIQUE:", "1. patch the toolchain", "KEY FACTS:", "OUTCOME:", "never run"):
        assert part in d["text"], part
    with pytest.raises(agent.Refused, match="is a recipe"):
        agent.run("old-mac-build", {})
    text, is_error = mcp.call_tool("describe", {"tool": "old-mac-build"})
    assert "TECHNIQUE:" in text and not is_error


def test_the_mcp_answer_marks_recipes_as_read_only(_isolated, monkeypatch):
    _write(recipemod.RECIPES, [GOOD])
    monkeypatch.setattr(agent, "_cards", lambda: [])
    text, _ = mcp.call_tool("discover", {"goal": "build for an old mac high sierra"})
    assert "old-mac-build [RECIPE: read it, never run it]" in text, text


def test_local_recipes_never_reach_the_published_catalogue(_isolated):
    _write(recipemod.RECIPES, [GOOD])
    _write(recipemod.LOCAL_RECIPES, [dict(GOOD, id="my-private-probe", title="the owner's own")])
    cat = cardmod.catalogue(cards=[], results={}, public_only=False)
    assert {r["id"] for r in cat["recipes"]} == {"old-mac-build", "my-private-probe"}
    import fieldkit.desk.registry as registry
    registry_file = _isolated / "tools.yaml"
    registry_file.write_text('{"tools": []}', encoding="utf-8")
    old = registry.REGISTRY
    try:
        registry.REGISTRY = registry_file
        cat = cardmod.catalogue(cards=[], results={}, public_only=True)
    finally:
        registry.REGISTRY = old
    assert cat["schema"] == 2
    assert [r["id"] for r in cat["recipes"]] == ["old-mac-build"]
    assert set(cat["recipes"][0]) == {"id", "title", "problem", "tags", "platforms"}   # steps stay behind describe


def test_readiness_counts_recipes_and_keeps_library_files_out_of_the_tool_total(_isolated):
    _write(recipemod.RECIPES, [GOOD])
    _write(recipemod.LOCAL_RECIPES, [dict(GOOD, id="mine")])
    cards = [_tool(), _tool(id="lib/util.py", draft=True, reviewed=False, probe="library"),
             _tool(id="src/run.py", draft=True, reviewed=False, probe="safe")]
    r = readiness.report(cards)
    assert r["recipes"] == {"published": 1, "own": 1}
    assert r["library"] == 1
    assert r["total"] == 2
    assert "2 recipes" in "\n".join(readiness.lines(r))


def test_capture_copies_the_script_into_fieldkit_and_marks_the_old_copy(_isolated, tmp_path_factory):
    src = tmp_path_factory.mktemp("scripts") / "probe.py"
    src.write_text("print('probe')\n")
    dest = recipemod.capture("old-mac-build", str(src), log=lambda *_: None)
    assert dest.read_text() == "print('probe')\n"
    assert dest.parent == _isolated / "local" / "recipes" / "old-mac-build"
    assert src.with_name("probe.py.adopted").is_file()          # the sweep now lists it as adopted
    assert src.is_file()                                        # nothing deleted: adopt --finish recycles it later
