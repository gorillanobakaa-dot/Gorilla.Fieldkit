"""Candidate test for bh-recipe-test: the script keeps every distilled step, in order, and has a check per step."""
from pathlib import Path

STEPS = ["cargo test --release <N>>&<N>", "git fetch -q origin", "go build ./... <N>>&<N>", "go test ./internal/llm/tools/ -run", "python -m pytest -q", "git add -a"]


def test_script_has_every_step_in_order():
    text = ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-test.sh')).read_text(encoding='utf-8')
    pos = -1
    for s in STEPS:
        i = text.find(s, pos + 1) if s else pos + 1
        assert i > pos, f'step missing or out of order: {s}'
        pos = i


def test_script_is_strict():
    assert 'set -euo pipefail' in ((Path(__file__).resolve().parents[3] / 'fieldkit/capture/adopted/bh-recipe-test.sh')).read_text(encoding='utf-8')
