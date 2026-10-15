"""The migration runs on Linux, where the harness folder IS the shared patch-set repo (2026-10-14)."""
from pathlib import Path

from fieldkit.migrate import plan


def test_the_harness_folder_with_the_intent_ledger_is_the_owner(tmp_path, monkeypatch):
    root = tmp_path / "gorilla-firefox"
    (root / "intents").mkdir(parents=True)
    (root / "intents" / "INTENTS.yaml").write_text("version: 1\nintents: []\n", encoding="utf-8")
    from fieldkit.buildh import buildrun
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: None)
    t = {"id": "x", "meta": {"harness_root": str(root)}, "steps": []}
    assert plan.Migration("x", t=t, find_install=False).owner == root
    t2 = {"id": "y", "meta": {"harness_root": str(tmp_path / "elsewhere")}, "steps": []}
    assert plan.Migration("y", t=t2, find_install=False).owner is None          # nothing invented


def test_brief_is_silent_without_migrations_and_loud_when_one_breaks(tmp_path, monkeypatch):
    from fieldkit.migrate import cli as mc
    assert mc.brief_lines(state_root=tmp_path) == []
    (tmp_path / "broken" / "migrate").mkdir(parents=True)
    (tmp_path / "broken" / "migrate" / "plan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(plan, "Migration", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no task")))
    out = mc.brief_lines(state_root=tmp_path)
    assert len(out) == 2 and out[0].startswith("FIREFOX MIGRATION broken: SITREP failed (RuntimeError")
    assert "build-harness symptom" in out[1]           # 2026-10-15: every brief ends with the symptom router
