"""gorilla-size refuses to recycle a folder that holds files unless --whole-folder is given (2026-10-25: Gemma 4 12B
widened "these files" to "the folder", which also held a .txt it was never asked about). Fieldkit's own callers that
recycle a whole work folder on purpose must say so, or they fail with exit 3 and leave the folder behind."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CALLERS = ["fieldkit/buildh/installertest.py", "fieldkit/desk/adopt.py", "fieldkit/method/stranger.py",
           "fieldkit/autopilot/toolrouter.py"]


def test_every_whole_folder_recycle_in_fieldkit_says_whole_folder():
    for rel in CALLERS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        calls = re.findall(r"""["']recycle["'],[^\]]*\]|size recycle [A-Z]:\\[^"\n]*""", text)
        assert calls, f"{rel}: no recycle call found (moved? update this test)"
        for c in calls:
            assert "--whole-folder" in c, f"{rel}: {c}"


def test_the_card_tells_a_small_model_that_wildcards_work_and_what_whole_folder_does():
    cards = yaml.safe_load((ROOT / "fieldkit/desk/tools.yaml").read_text(encoding="utf-8"))
    cards = cards if isinstance(cards, list) else cards.get("tools", [])
    card = next(c for c in cards if c.get("id") == "gorilla-size-recycle")
    inputs = {i["name"]: i for i in card["inputs"]}
    assert "Wildcards work" in inputs["paths"]["help"] and r"\*.png" in inputs["paths"]["help"]
    assert inputs["whole_folder"]["flag"] == "--whole-folder"
    assert "asked for the folder" in inputs["whole_folder"]["help"]
