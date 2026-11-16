"""Fixture refusals never reach the real ledger (2026-11-16: Windows test runs sent them over the cable as LEDGER1)."""
from pathlib import Path

from fieldkit import refusals


def test_record_without_root_writes_to_the_test_folder_not_the_real_state(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_REFUSALS_DIR", str(tmp_path))
    real = refusals.state_root(refusals.ROOT) / "state" / refusals.LEDGER
    before = real.read_bytes() if real.is_file() else b""
    refusals.record("ci", "CI red: own failure: tests/test_x.py::test_y (feat/x)", session="t")
    assert "test_x.py::test_y" in (Path(tmp_path) / refusals.LEDGER).read_text(encoding="utf-8")
    assert (real.read_bytes() if real.is_file() else b"") == before


def test_an_explicit_root_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_REFUSALS_DIR", str(tmp_path / "env"))
    assert refusals.state(tmp_path / "r") != Path(tmp_path / "env")
