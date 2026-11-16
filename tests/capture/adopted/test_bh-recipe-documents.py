"""Candidate test for bh-recipe-documents: the script keeps every distilled step, in order, and has a check per step."""
from pathlib import Path

STEPS = ["git fetch -q origin", "git checkout -q tests/test_zero_command.py", "fieldkit guard -- python", "fieldkit privacy scan .", "git add -a"]


def test_script_has_every_step_in_order():
    text = ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-documents.sh')).read_text(encoding='utf-8')
    pos = -1
    for s in STEPS:
        i = text.find(s, pos + 1) if s else pos + 1
        assert i > pos, f'step missing or out of order: {s}'
        pos = i


def test_script_is_strict():
    assert 'set -euo pipefail' in ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-documents.sh')).read_text(encoding='utf-8')
