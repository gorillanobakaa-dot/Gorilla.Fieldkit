"""F2.10: the Board shows one COST row per machine; the other machine's row is its own `cost` answer over the cable
(the numbers its `fieldkit session-cost` measures), and no answer is NOT COUNTED, never a guess."""
from datetime import date

import pytest

from fieldkit.lifecycle import board
from fieldkit.tokens import cost_daily

DAY = date(2026, 11, 20)
MINE = {"date": "2026-11-20", "machine": "L15-WIN", "usd": 3.5, "sessions": 2, "calls": 40, "unpriced_calls": 0}
THEIRS = {"date": "2026-11-20", "machine": "VAIO-LNX", "usd": 12.34, "sessions": 5, "calls": 321, "unpriced_calls": 0}


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cost_daily.settings, "state_root", lambda: tmp_path)
    monkeypatch.setattr(cost_daily, "peer_name", lambda: "VAIO-LNX")
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    (tmp_path / "coord").mkdir()
    (tmp_path / "coord" / "PLAN.md").write_text("| a | b |\n", encoding="utf-8")


def test_the_board_has_an_l15_row_and_a_vaio_row_with_the_vaio_own_dollars(tmp_path):
    now = 1_800_000_000.0
    ask = {}

    def call(req, timeout=0):
        ask.update(req)
        return {"ok": True, "measured_at": now, "data": THEIRS}

    lines = board.cost_note(DAY, call=call, now=now, measure=lambda d: MINE)
    assert ask["verb"] == "cost" and ask["kind"] == "daily" and ask["date"] == "2026-11-20"
    assert len(lines) == 2
    assert "COST L15-WIN 2026-11-20 | $3.50" in lines[0]
    assert "COST VAIO-LNX 2026-11-20 | $12.34" in lines[1] and "5 session(s)" in lines[1]
    assert (tmp_path / "coord" / "PLAN.md").read_text(encoding="utf-8").count("| COST ") == 2


def test_no_answer_from_the_vaio_is_not_counted_never_a_guess():
    def call(req, timeout=0):
        raise OSError("cable down")

    lines = board.cost_note(DAY, call=call, now=1_800_000_000.0, measure=lambda d: MINE)
    assert "$3.50" in lines[0]
    assert "NOT COUNTED" in lines[1] and "$" not in lines[1].split("|")[2]


def test_no_board_means_no_rows_and_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "none"))
    assert board.cost_note(DAY, call=lambda r, timeout=0: {"ok": False}, measure=lambda d: MINE) == []
    assert not (tmp_path / "none").exists()
