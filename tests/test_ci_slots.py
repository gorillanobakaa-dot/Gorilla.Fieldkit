"""C3: slot budget across projects and cancel-in-progress on every workflow."""
from pathlib import Path

import yaml

from fieldkit.method import ciplan

ROOT = Path(__file__).resolve().parents[1]


def _cfg(**slots):
    cfg = ciplan.load_config(ROOT / ".github" / "ci.yaml")
    cfg["slots"] = {**cfg["slots"], **slots}
    return cfg


def test_the_shipped_budget_fits_the_account_and_the_runner_budget():
    cfg = ciplan.load_config(ROOT / ".github" / "ci.yaml")
    assert ciplan.slot_problems(cfg) == []
    assert cfg["slots"]["projects"]["fieldkit"] == {"windows": 16, "linux": 16}


def test_budgets_over_the_account_limit_fail():
    problems = ciplan.slot_problems(_cfg(account_limit=30))
    assert any("exceed the account limit" in p for p in problems)


def test_a_runner_budget_over_the_projects_slots_fails():
    cfg = ciplan.load_config(ROOT / ".github" / "ci.yaml")
    cfg["budget"] = {"windows": 20, "linux": 16}
    assert any("windows 20" in p for p in ciplan.slot_problems(cfg))


def test_a_missing_project_budget_fails():
    assert any("no budget" in p for p in ciplan.slot_problems(ciplan.load_config(ROOT / ".github" / "ci.yaml"), project="ghost"))


def test_check_reports_a_slot_problem():
    cfg = _cfg(account_limit=1)
    assert any(p.startswith("slots:") for p in ciplan.check([], {}, cfg))


def test_every_workflow_cancels_superseded_pull_request_runs_and_never_others():
    flows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert flows
    for f in flows:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        c = doc.get("concurrency")
        assert c, f"{f.name} has no concurrency block"
        assert "pull_request" in str(c["cancel-in-progress"]), f.name
        assert "== 'pull_request'" in str(c["cancel-in-progress"]), f.name
