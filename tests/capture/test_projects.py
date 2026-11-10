"""P26 piece 4: a captured candidate and its ledger lines are sorted by project; each project has a section with an entry card."""
import json
from pathlib import Path

import pytest
import yaml

from fieldkit.capture import loop, projects as P, store as st

ROOT = Path(__file__).resolve().parents[2]
REC = {"id": "recipe-build", "title": "Recipe: build", "episodes": {"in_group": 3, "replayed_ok": 3},
       "steps": [{"id": "a", "command": "cargo build", "sentence": "build", "check": "exit 0"},
                 {"id": "b", "command": "cargo test", "sentence": "test", "check": "exit 0"}]}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "store"))


def test_candidate_id_carries_the_project_prefix_once(tmp_path):
    d = loop.write_candidate(REC, tmp_path / "c", project="firefox")
    assert Path(d).name == "bh-recipe-build" and (Path(d) / "bh-recipe-build.sh").is_file()
    assert P.prefixed("kernel", "kernel-x") == "kernel-x"
    assert P.prefixed(None, "x") == "x" and P.prefixed("opencode", "x") == "opencode-x"
    assert P.project_of_id("bh-x") == "firefox" and P.project_of_id("zzz") is None


def test_ledger_line_names_its_project():
    loop._ledger({"reason": "r"}, "kernel")
    loop._ledger({"reason": "s"})
    a, b = [json.loads(x) for x in (st.capture_dir() / loop.LEDGER).read_text().splitlines()]
    assert a["project"] == "kernel" and "project" not in b


def test_group_project_from_the_run_then_from_the_cwd():
    eps = [{"cwd": "/x/Gorilla.Opencode/cmd"}, {"cwd": "/x/Gorilla.Opencode"}, {"cwd": "/x/firefox"}]
    assert loop._project_of_group("manual", eps) == "opencode"
    assert loop._project_of_group("kernel:job:1", eps) == "kernel"
    assert loop._project_of_group("manual", [{"cwd": "/tmp"}]) is None


def test_section_lists_workflow_steps_tools_adopted_and_open_lines():
    ledger = [{"project": "opencode", "reason": "mine"}, {"project": "kernel", "reason": "not mine"}]
    sec = P.section("opencode", ROOT, tools=["opencode-x", "bh-y", "other"], ledger=ledger)
    assert sec["tools"] == ["opencode-x"] and [o["reason"] for o in sec["open"]] == ["mine"]
    steps = sec["workflows"][0]["steps"]
    assert sec["workflows"][0]["recipe"] == "gorilla-opencode" and [s["n"] for s in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["stage"] == "toolchain" and all(s["command"] and s["check"] for s in steps)
    assert any("WORKFLOW 1" in ln for ln in P.lines(sec))
    with pytest.raises(KeyError):
        P.section("nope", ROOT, tools=[], ledger=[])


def test_each_project_has_an_entry_card_that_runs_its_section():
    cards = {c["id"]: c for c in yaml.safe_load((ROOT / "fieldkit/desk/tools.yaml").read_text(encoding="utf-8"))["tools"]}
    for name, cfg in P.load().items():
        c = cards[cfg["entry"]]
        assert c["entry"][-3:] == ["capture", "project", name]
        assert (ROOT / "fieldkit" / "emerge" / "recipes" / f"{cfg['recipes'][0]}.yaml").is_file()
        assert P.section(name, ROOT, ledger=[])["workflows"][0]["steps"], f"{name} section has no steps"
