from fieldkit import visual
from fieldkit.migrate import measure


def test_visual_row_is_deferred_to_s4_at_s0_and_other_rows_stay_red():
    rows = [{"check": visual.ROW, "ok": False, "evidence": "static: 0 fail, 1 unverifiable"},
            {"check": "memory", "ok": False, "evidence": "8 GB installed, need 16 GB"}]
    out = measure.defer_visual(rows)
    assert out[0]["ok"] and "S4.visual-static" in out[0]["evidence"]
    assert out[1]["ok"] is False
