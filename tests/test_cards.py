"""cards and readiness: inputs read without running, trust computed from facts, docs never drift."""
from fieldkit.desk import cards, readiness

SCRIPT = '''"""Resize an image."""
import argparse
raise SystemExit("importing or running this is a failure of the test")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("src", help="input image")
    p.add_argument("dst", nargs="?", help="output image")
    p.add_argument("--size", type=int, required=True, help="pixels")
    p.add_argument("--mode", choices=["fit", "fill"])
    p.add_argument("--tags", nargs="+")
    p.add_argument("-v", "--verbose", action="store_true")
'''


def test_argparse_inputs_are_read_not_run(tmp_path):
    f = tmp_path / "resize.py"
    f.write_text(SCRIPT)
    got = {i["name"]: i for i in cards.argparse_inputs(f)}
    assert got["src"] == {"name": "src", "flag": None, "type": "str", "required": True, "help": "input image"}
    assert got["dst"]["type"] == "str" and got["dst"]["required"] is False
    assert got["size"]["type"] == "int" and got["size"]["required"] is True
    assert got["mode"]["choices"] == ["fit", "fill"] and got["tags"]["type"] == "list"
    assert got["verbose"]["type"] == "flag" and got["verbose"]["flag"] == "-v"


def _card(**kw):
    base = {"id": "t", "title": "t", "path": None, "entry": ["python", "t.py"], "inputs": [], "effects": [],
            "safety": "read-only", "modes": {}, "tests": ["python -m pytest"], "platforms": [], "draft": False,
            "reviewed": True, "portable": True, "probe": "safe"}
    base.update(kw)
    return base


def test_trust_ladder():
    ok = {"t": {"ok": True, "sha256": None}}
    assert cards.trust(_card(draft=True, reviewed=False), ok)[0] == "gathered"
    assert cards.trust(_card(safety="unknown"), ok)[0] == "gathered"
    assert cards.trust(_card(tests=[]), ok) == ("carded", ["no test declared"])
    assert cards.trust(_card(), {})[0] == "carded"                               # declared, never passed
    assert cards.trust(_card(), {"t": {"ok": False}}) == ("carded", ["tests failed"])
    assert cards.trust(_card(), ok) == ("verified", [])
    lvl, why = cards.trust(_card(portable=False), ok)
    assert lvl == "tested" and why == ["hard-coded home path"]
    lvl, why = cards.trust(_card(safety="reversible", modes={"apply": "x"}), ok)
    assert lvl == "tested" and "preview/undo/verify" in why[0]
    assert cards.trust(_card(safety="reversible", modes={"preview": "p", "undo": "u", "verify": "v"}), ok)[0] == "verified"


def test_changed_file_loses_tested(tmp_path):
    f = tmp_path / "tool.py"
    f.write_text("x = 1\n")
    c = _card(path=str(f))
    res = {"t": {"ok": True, "sha256": cards.file_sha(f)}}
    assert cards.trust(c, res)[0] == "verified"
    f.write_text("x = 2\n")
    assert cards.trust(c, res) == ("carded", ["file changed since its tests passed"])


def test_draft_card_from_harvest_entry(tmp_path):
    f = tmp_path / "wipe.ps1"
    f.write_text("param([string]$Path)\nRemove-Item $Path -Recurse\n")
    e = {"id": "src/wipe.ps1", "title": "wipe", "language": "powershell", "touches": ["deletes-files"],
         "options": ["Path"], "portable": True, "probe": None}
    c = cards.draft_card(dict(e, path=str(f)))
    assert c["draft"] and c["safety"] == "irreversible" and c["entry"][0] == "powershell"
    assert cards.trust(c, {})[0] == "gathered"


def test_readiness_counts_and_docs_block(tmp_path, monkeypatch):
    cs = [_card(id="a"), _card(id="b", tests=[]), _card(id="c", draft=True, reviewed=False),
          _card(id="d", retired=True)]
    monkeypatch.setattr(cards, "test_results", lambda: {"a": {"ok": True}})
    r = readiness.report(cs)
    assert r["levels"] == {"gathered": 1, "carded": 1, "tested": 0, "verified": 1} and r["total"] == 3
    readme = tmp_path / "README.md"
    readme.write_text("# X\n\nintro\n")
    monkeypatch.setattr(readiness, "DOCS", readme)
    readiness.write_docs(r)
    readiness.write_docs(r)                                                   # idempotent: one block
    text = readme.read_text()
    assert text.count(readiness.BEGIN) == 1 and "| verified | 1 |" in text and text.startswith("# X")


def test_cards_cache_is_used_and_invalidated(tmp_path, monkeypatch):
    monkeypatch.setattr(cards, "CACHE", tmp_path / "cache.json")
    calls = []
    monkeypatch.setattr(cards, "_build_cards", lambda: calls.append(1) or [{"id": "x"}])
    sig = ["s1"]
    monkeypatch.setattr(cards, "_signature", lambda: sig[0])
    assert cards.all_cards() == [{"id": "x"}] and cards.all_cards() == [{"id": "x"}]
    assert len(calls) == 1                                  # second call came from the cache
    sig[0] = "s2"                                           # something changed on disk
    cards.all_cards()
    assert len(calls) == 2


def test_scope_backup_counts_as_undo_only_for_reversible():
    ok = {"t": {"ok": True, "sha256": None}}
    modes = {"preview": ["+", "--check"], "verify": [{"output_contains": "x"}]}
    assert cards.trust(_card(safety="reversible", modes=modes, scope=["file"]), ok)[0] == "verified"
    lvl, why = cards.trust(_card(safety="irreversible", modes=modes, scope=["file"]), ok)
    assert lvl == "tested" and "undo" in why[0]            # a file backup cannot undo a service change


def test_home_path_ignores_placeholders():
    from fieldkit.desk.discover import HOME_PATH
    assert HOME_PATH.findall(r"not C:\Users\.. and /home/you and C:\Users\me\x") == []
    assert HOME_PATH.findall("C:" + "\\" + "Users" + "\\" + "gorilla9" + "\\" + "Documents")
