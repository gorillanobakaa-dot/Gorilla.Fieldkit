"""One decision register for both operating systems (2026-10-14)."""
import json

from fieldkit.buildh import decisions as dec

REG = """release: test
decisions:
  - id: both
    title: applies everywhere
    decided: 2026-10-14
    by: maintainer
    provenance: test
    status: enforced
    why: a check
    models: quote this id, do not re-argue it
    verify:
      - tree_contains: {path: a.txt, text: locked}
  - id: win-only
    title: windows only
    decided: 2026-10-14
    by: maintainer
    provenance: test
    status: enforced
    why: a check
    platforms: [windows]
    verify:
      - tree_contains: {path: missing.txt, text: x}
"""


def _owner(tmp_path):
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(REG, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("locked\n", encoding="utf-8")
    return tmp_path, work


def test_an_entry_for_the_other_os_is_reported_not_failed(tmp_path, monkeypatch):
    owner, work = _owner(tmp_path)
    monkeypatch.setattr(dec, "this_platform", lambda: "linux")
    r = dec.check(owner, work)
    v = {x["id"]: x["verdict"] for x in r["rows"]}
    assert v == {"both": "ENFORCED", "win-only": "OTHER OS"} and r["ok"]
    monkeypatch.setattr(dec, "this_platform", lambda: "windows")
    r = dec.check(owner, work)
    assert {x["id"]: x["verdict"] for x in r["rows"]}["win-only"] == "UNCHECKABLE" and not r["ok"]


def test_models_note_is_printed(tmp_path, monkeypatch):
    owner, work = _owner(tmp_path)
    monkeypatch.setattr(dec, "this_platform", lambda: "linux")
    out = "\n".join(dec.lines(dec.check(owner, work)))
    assert "for models: quote this id, do not re-argue it" in out


def test_unknown_platform_is_a_register_problem(tmp_path):
    owner, _ = _owner(tmp_path)
    p = owner / "decisions" / "PRODUCT-DECISIONS.yaml"
    p.write_text(REG.replace("[windows]", "[macos]"), encoding="utf-8")
    assert any("unknown platform" in x for x in dec.load(owner)["problems"])


def test_register_found_in_the_harness_root_without_the_owner_preflight(tmp_path):
    owner, _ = _owner(tmp_path)
    assert dec.register_root({"meta": {"harness_root": str(owner)}}) == owner
    assert dec.register_root({"meta": {"harness_root": str(tmp_path / "work")}}) is None
    assert dec.register_root({"meta": {}}) is None
