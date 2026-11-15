"""Row 55: after a successful --apply only a past ibmpmsvc request is left; NEXT must not send the owner to repeat it."""
import time

from fieldkit.power import drift

T = time.mktime((2026, 11, 2, 3, 29, 0, 0, 0, -1))
OFF = {"SBUTTONACTION": {"ac": 0}}
ON = {"SBUTTONACTION": {"ac": 1}}
EV = [{"t": T, "t_text": "x", "on_ac": True, "busy": True, "peak_c": 80, "load_pct": 90}]


def test_next_after_apply_names_the_date_the_event_leaves_the_window():
    n = drift.next_step(EV, OFF, 7)
    assert "--apply" not in n
    assert "nothing to apply" in n and "03:29" in n and "2026-11-09" in n


def test_next_still_says_apply_while_the_sleep_button_is_on():
    assert drift.next_step(EV, ON, 7) == "fieldkit power drift --apply"


def test_next_nothing_to_do_when_clean():
    assert drift.next_step([], OFF, 7) == "nothing to do"
    assert drift.next_step([dict(EV[0], busy=False)], OFF, 7) == "nothing to do"


def test_next_uses_the_latest_bad_event():
    older = dict(EV[0], t=T - 2 * 86400)
    assert "2026-11-09" in drift.next_step([older, EV[0]], OFF, 7)
    assert "2026-11-09" in drift.next_step([EV[0], older], OFF, 7)
