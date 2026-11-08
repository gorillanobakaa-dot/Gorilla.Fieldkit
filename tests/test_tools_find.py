"""fieldkit tools find WORDS (rule USE THE HARNESS named it; until 2026-11-01 it did not exist)."""
from fieldkit import cli

ROWS = [{"id": "gorilla-size-recycle", "title": "To the Recycle Bin", "actions": ["recycle"]},
        {"id": "ship", "title": "The git side of shipping", "actions": ["prune", "push"]},
        {"id": "icons", "title": "Sharp desktop icons"}]


def test_find_ranks_id_and_title_hits_first_and_finds_words_in_any_field():
    assert [t["id"] for t in cli.tools_find(["recycle"], ROWS)] == ["gorilla-size-recycle"]
    assert [t["id"] for t in cli.tools_find(["prune"], ROWS)] == ["ship"]
    assert cli.tools_find(["nothing-like-this"], ROWS) == []


def test_the_parser_knows_find():
    a = cli.build_parser().parse_args(["tools", "find", "prune", "branch"])
    assert a.action == "find" and a.words == ["prune", "branch"]
