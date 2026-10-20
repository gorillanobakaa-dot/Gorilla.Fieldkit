"""release-cover (2026-10-18): a decision no page of the platform named after it was decided is due on the new page,
whatever the previous page's date (build 1 of 158 skipped 18 decisions and was written without release-cover)."""
from fieldkit.buildh import releasecover as rc


def test_a_decision_no_later_page_named_is_due_and_a_named_one_is_not(tmp_path):
    reg = tmp_path / "reg.yaml"
    reg.write_text("decisions:\n"
                   "  - {id: D-158-01, title: remote control closed, decided: '2026-10-14', status: enforced}\n"
                   "  - {id: D-158-13, title: google only, decided: '2026-10-15', status: enforced}\n"
                   "  - {id: D-157-40, title: hidden pages, decided: '2026-10-09', status: enforced}\n"
                   "  - {id: D-158-99, title: old, decided: '2026-10-14', status: retired}\n", encoding="utf-8")
    pages = [{"tag": "v158.0-win64", "published": "2026-10-18", "body": "D-158-13 Google only"},
             {"tag": "v157.0-win64", "published": "2026-10-10", "body": "D-157-40 hidden pages"}]
    assert rc.uncovered(reg, pages, "2026-10-04") == [("D-158-01", "remote control closed")]


def test_a_page_published_before_the_decision_does_not_cover_it(tmp_path):
    reg = tmp_path / "reg.yaml"
    reg.write_text("decisions:\n  - {id: D-158-13, title: google only, decided: '2026-10-18', status: enforced}\n",
                   encoding="utf-8")
    pages = [{"tag": "v157.0-win64", "published": "2026-10-10", "body": "D-158-13 announced early"}]
    assert rc.uncovered(reg, pages, "2026-10-01") == [("D-158-13", "google only")]


def test_a_placeholder_left_on_the_page_fails(tmp_path):
    reg = tmp_path / "reg.yaml"
    reg.write_text("decisions: []\n", encoding="utf-8")
    from fieldkit.leakgate import summary as lsum
    page = lsum.HEADING + "\n\nLEAK GATE: TO BE FILLED after the run\n"
    rows = {r["check"]: r["ok"] for r in rc.check(page, reg, "", "2026-01-01")}
    assert rows["release page: no placeholder left (TO BE FILLED)"] is False
    rows = {r["check"]: r["ok"] for r in rc.check(lsum.HEADING + "\nPASS\n", reg, "", "2026-01-01")}
    assert rows["release page: no placeholder left (TO BE FILLED)"] is True


def test_release_cover_refuses_github_while_a_leak_gate_runs(monkeypatch):
    # 2026-10-19: release-cover called gh twice while a release leak gate was measuring the machine
    import subprocess
    import pytest
    from fieldkit.leakgate import status
    monkeypatch.setattr(status, "gate_running", lambda: [4242])
    with pytest.raises(RuntimeError, match="leak gate is running"):
        rc.previous_release(run=subprocess.run)


def test_gate_running_finds_the_gate_process_only():
    from types import SimpleNamespace as NS
    from fieldkit.leakgate import status
    procs = [NS(info={"pid": 1, "cmdline": ["python", "-m", "fieldkit", "build-harness", "leakgate", "firefox-158.0", "--release"]}),
             NS(info={"pid": 2, "cmdline": ["python", "-m", "fieldkit", "build-harness", "leakgate-summary", "t"]}),
             NS(info={"pid": 3, "cmdline": ["python", "-m", "pytest"]})]
    assert status.gate_running(NS(process_iter=lambda attrs: procs)) == [1]
