"""The quota guard stores the readable limit line, not the tail of a job's status JSON (I-34, 2026-11-23)."""
from fieldkit.lifecycle import models

CFG = {"availability": {"cooldown_minutes": 120, "patterns": ["usage limit", "max turns", "quota"]}}


def test_reason_is_the_limit_line_not_the_status_json():
    out = ('run guard job 20261123-163515-claude: 0.5 min\n'
           '{\n "status": "failed",\n "rc": 75,\n'
           ' "why": "crash: You have hit your usage limit, resets 5pm (Europe/London)\\nmore log here"\n}\n')
    hit = models.classify_failure(out, 75, now=1_000_000.0, data=CFG)
    assert hit["reason"] == "crash: You have hit your usage limit, resets 5pm (Europe/London)"


def test_long_single_line_is_cut_around_the_match():
    out = "x" * 500 + " quota exceeded for this account " + "y" * 500
    r = models.classify_failure(out, 1, now=1_000_000.0, data=CFG)["reason"]
    assert "quota exceeded" in r and len(r) <= 160


def test_plain_message_kept_as_is():
    r = models.classify_failure("Error: Reached max turns (1)", 1, now=1_000_000.0, data=CFG)["reason"]
    assert r == "Error: Reached max turns (1)"
