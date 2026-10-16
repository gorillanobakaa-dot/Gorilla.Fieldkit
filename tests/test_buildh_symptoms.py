# Version: 1.0.0 · updated 26-10-15-17-30
"""symptom: what you see -> the tool built for it. Every entry is checked: its words match its own example (English
and the Romanian the owner writes), its command is a real build-harness action, and no two entries claim one example."""
import re

from fieldkit.buildh import symptoms as sy


def _actions():
    src = open("fieldkit/cli.py", encoding="utf-8").read()
    start = src.index('"latest", "vault"')
    end = src.index("])", src.index('"symptom"', start))          # the list's real end, whatever comes last
    return set(re.findall(r'"([a-z][a-z-]+)"', src[start:end]))


def test_every_entry_is_complete_and_matches_its_own_example():
    ids = set()
    for e in sy.SYMPTOMS:
        assert e["id"] not in ids
        ids.add(e["id"])
        for k in ("seen", "example", "words", "action", "run", "proves", "look"):
            assert e[k], (e["id"], k)
        assert sy.match(e["example"])[0]["id"] == e["id"], e["id"]


def test_every_command_is_a_real_build_harness_action():
    acts = _actions()
    for e in sy.SYMPTOMS:
        assert e["action"] in acts, e["id"]


def test_the_owner_s_own_words_find_the_tool():
    # 2026-10-15, as the owner described them (Romanian and English)
    assert sy.match("pe meniuri informatia nu este prezenta ori este truncated la dialogul leave page")[0]["id"] == "dialog-text-cut"
    assert sy.match("you see the difference in the icons from the ones in the grid and the one on the taskbar")[0]["id"] \
        == "launcher-old-name-or-icon"
    assert sy.match("iconita din grid e diferita si scrie 155")[0]["id"] == "launcher-old-name-or-icon"


def test_unknown_symptom_matches_nothing():
    assert sy.match("the kettle is cold") == []


def test_lines_put_the_full_path_on_harness_commands():
    out = sy.lines([sy.SYMPTOMS[0]], "/opt/fieldkit/bin/fieldkit")
    assert out[1] == "  RUN FIRST: /opt/fieldkit/bin/fieldkit build-harness clip-check <task>"


def test_word_boundaries_are_real_boundaries():
    # a raw string with a doubled backslash matches a literal backslash, not a word edge (caught 2026-10-15)
    for e in sy.SYMPTOMS:
        for g in e["words"]:
            assert "\\\\" not in g, (e["id"], g)
    assert sy.match("the old icon in the grid")[0]["id"] == "launcher-old-name-or-icon"
    assert sy.match("the bold gridline") == []


def test_every_command_is_printed_with_the_full_path_of_its_program():
    for e in sy.SYMPTOMS:
        c = sy.command(e["run"], "/opt/fieldkit/bin/fieldkit", "/opt/venv/bin/python")
        assert c.startswith("/opt/"), (e["id"], c)
