"""Card effects use the schema's words only, and a card that changes things is never marked read-only
(2026-10-19: 13 home-made effect words; "starts-processes" hid three read-only cards that start a program)."""
from pathlib import Path

import yaml

from fieldkit.desk import cardcheck, cards

PUBLIC = Path(__file__).resolve().parents[1] / "fieldkit" / "desk" / "tools.yaml"


def test_every_public_card_uses_schema_effects():
    bad = []
    for c in yaml.safe_load(PUBLIC.read_text(encoding="utf-8"))["tools"]:
        unknown = [e for e in c.get("effects") or [] if e not in cards.EFFECTS]
        if unknown:
            bad.append(f"{c['id']}: unknown effects {unknown}")
    assert not bad, bad


def test_no_card_is_read_only_while_its_effects_change_things():
    # the COMPUTED safety (cards.all_cards: `changes: false` makes a card read-only), not the raw YAML field: on
    # 2026-10-19 two owner cards with changes: false and writing effects passed a check that read only the field and
    # then failed the other session's pre-commit. Includes the owner's local cards where they exist.
    bad = [(c["id"], sorted(set(c.get("effects") or []) & cards.CHANGING)) for c in cards.all_cards()
           if c.get("safety") == "read-only" and set(c.get("effects") or []) & cards.CHANGING]
    assert not bad, bad


def test_the_card_check_refuses_a_home_made_effect_word():
    card = {"id": "x", "entry": ["python", "-m", "fieldkit", "where"], "effects": ["starts-processes"],
            "safety": "reversible", "inputs": []}
    problems, _ = cardcheck.check_card(card)
    assert any("effects not in the schema: starts-processes" in p for p in problems), problems
