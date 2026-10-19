"""fieldkit cards export: the catalogue Gorilla OpenCode embeds (2026-10-19). Only reviewed tools, only the public
tools.yaml (the owner's local cards name his own folders and devices), and what a model needs to pick one."""
import json

from fieldkit import cli
from fieldkit.core import settings
from fieldkit.desk import cards, registry


def test_the_catalogue_holds_only_public_reviewed_tools():
    c = cards.catalogue()
    public = {t["id"] for t in settings.read_file(registry.REGISTRY)["tools"]}
    assert c["schema"] == 1 and c["fieldkit_commit"] and c["tools"]
    assert all(t["id"] in public for t in c["tools"]), "a local card would ship inside a public program"
    assert {t["trust"] for t in c["tools"]} <= set(cards.CATALOGUE_LEVELS), "a draft must never be suggested"
    ids = [t["id"] for t in c["tools"]]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    one = c["tools"][0]
    assert set(one) == {"id", "title", "trust", "safety", "inputs"}


def test_the_export_command_writes_the_file(tmp_path):
    out = tmp_path / "catalogue.json"
    assert cli.main(["cards", "export", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tools"] and data["schema"] == 1
