"""Plan P37: a feature is done on an OS only after it RAN end to end there (docs/METHODS.md 47)."""
import subprocess
import textwrap

import pytest

from fieldkit.integration import core, selftest
from fieldkit.lifecycle import brieflint, tasks


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True)


def _base(tmp_path):
    selftest.build(tmp_path, "fieldkit")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")


def _edit_card(tmp_path, old, new):
    p = tmp_path / "fieldkit/desk/tools.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_a_card_whose_platforms_changed_needs_an_e2e_job_per_os(tmp_path):
    _base(tmp_path)
    _edit_card(tmp_path, "- linux", "- linux\n  - macos")          # claims a third OS, names no job for it
    gaps = [g for g in core.run_check(tmp_path, "HEAD")["gaps"] if g["surface"] == "5-e2e"]
    assert gaps and "macos: no e2e job named" in gaps[0]["what"]


def test_an_old_card_with_unchanged_platforms_owes_nothing(tmp_path):
    _base(tmp_path)
    _edit_card(tmp_path, "alpha title", "alpha title changed")
    assert not [g for g in core.run_check(tmp_path, "HEAD")["gaps"] if g["surface"] == "5-e2e"]


def test_a_named_job_must_exist_and_run_on_that_os(tmp_path):
    _base(tmp_path)
    (tmp_path / ".github/workflows/e2e.yml").write_text(
        "jobs:\n  e2e-alpha-windows:\n    runs-on: ubuntu-latest\n  e2e-alpha-linux:\n    runs-on: ubuntu-latest\n",
        encoding="utf-8")
    msg = core.check_e2e(core.Repo(tmp_path), {}, core.load_cards(core.Repo(tmp_path), "fieldkit/desk/tools.yaml")["alpha"])
    assert "windows: job 'e2e-alpha-windows' runs on ubuntu-latest" in msg and "linux" not in msg.split("windows")[1]
    (tmp_path / ".github/workflows/e2e.yml").write_text("jobs: {}\n", encoding="utf-8")
    assert "is in no workflow" in core.check_e2e(core.Repo(tmp_path), {}, {"platforms": ["linux"], "e2e": {"linux": "x"}})


def test_platforms_why_excuses_the_e2e_job(tmp_path):
    assert core.check_e2e(core.Repo(tmp_path), {}, {"platforms": ["windows", "linux"], "platforms_why": "x"}) is None


def test_the_real_leakgate_card_names_a_job_that_exists():
    repo = core.Repo(".")
    card = core.load_cards(repo, "fieldkit/desk/tools.yaml")["bh-leakgate"]
    assert card["e2e"] == {"linux": "e2e-leakgate-linux"}
    assert "e2e-leakgate-linux" in core.workflow_jobs(repo)


PORT = "P37: port the leak gate to Linux (parity with Windows).\n"


def test_a_port_brief_must_name_an_e2e_job():
    assert brieflint.ports_to_os(PORT) and not brieflint.ports_to_os("Fix a typo in the README for Linux users.")
    out = brieflint.lint(PORT + "SCOPE: x\n", ".", plan="P37")
    assert any(sev == "refuse" and "E2E" in m for sev, m in out)
    ok = brieflint.lint(PORT + "E2E: e2e-leakgate-linux\n", ".", plan="P37")
    assert not [m for sev, m in ok if sev == "refuse" and "E2E" in m]
    bad = brieflint.lint(PORT + "E2E: e2e-nope\n", ".", plan="P37")
    assert any("is in no .github/workflows" in m for sev, m in bad)


def _task(tmp_path, brief):
    b = tmp_path / "brief.md"
    b.write_text(brief, encoding="utf-8")
    return {"brief": str(b), "title": "t"}


def test_done_is_refused_for_a_port_task_without_an_e2e_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/e2e.yml").write_text("jobs:\n  e2e-x-linux:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    t = _task(tmp_path, PORT)
    assert "end-to-end CI job" in tasks.e2e_refusal(t, "all tests pass", str(tmp_path))
    assert tasks.e2e_refusal(t, "ran e2e-x-linux green", str(tmp_path)) == ""
    assert tasks.e2e_refusal(_task(tmp_path, PORT + "E2E: e2e-x-linux\n"), "", str(tmp_path)) == ""
    assert tasks.e2e_refusal(_task(tmp_path, "Fix a bug in the parser.\n"), "", str(tmp_path)) == ""


def test_report_raises_for_done_without_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "ledger", lambda: {"T-1": _task(tmp_path, PORT)})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(tasks.E2EMissing):
        tasks.report("T-1", "done", note="done")


def test_a_card_main_added_does_not_owe_an_e2e_job_in_a_merge(tmp_path):
    """Merging main into a branch: HEAD is the branch, MERGE_HEAD is main. A card new on main (not on the branch) is not
    new work of this branch; before this the merge commit was refused for every card main had added."""
    _base(tmp_path)
    _git(tmp_path, "checkout", "-qb", "main-side")
    _edit_card(tmp_path, "- linux", "- linux\n  - macos")          # main claims a third OS, names no job
    _git(tmp_path, "commit", "-qam", "main adds a claim")
    _git(tmp_path, "checkout", "-q", "-")
    (tmp_path / "branch.txt").write_text("branch work\n", encoding="utf-8")       # diverge: a fast-forward has no MERGE_HEAD
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "branch work")
    r = subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-commit", "--no-ff", "main-side"], capture_output=True, text=True)
    assert (tmp_path / ".git/MERGE_HEAD").exists(), r.stdout + r.stderr
    assert not [g for g in core.run_check(tmp_path, "HEAD")["gaps"] if g["surface"] == "5-e2e"]
