"""Candidate test for bh-recipe-release: the script keeps every distilled step, in order, and has a check per step."""
from pathlib import Path

STEPS = ["gh pr view <N>", "git checkout -q -b", "git commit -q -f", "fieldkit guard -- python", "git add -a", "fieldkit ship ci-wait <N>", "git fetch -q origin", "fieldkit inbox add <S>", "fieldkit task status --json", "fieldkit privacy scan .", "gh release view v0.<N>", "fieldkit size recycle <P>"]


def test_script_has_every_step_in_order():
    text = ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-release.sh')).read_text(encoding='utf-8')
    pos = -1
    for s in STEPS:
        i = text.find(s, pos + 1) if s else pos + 1
        assert i > pos, f'step missing or out of order: {s}'
        pos = i


def test_script_is_strict():
    assert 'set -euo pipefail' in ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-release.sh')).read_text(encoding='utf-8')
