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


CHECKS = """release: test
decisions:
  - id: cfg
    title: mozconfig disables
    decided: 2026-10-14
    by: maintainer
    provenance: test
    status: enforced
    why: a check
    verify:
      - mozconfig_has: {file: gorilla-patchset/mozconfig, text: "ac_add_options --disable-updater"}
      - mozconfig_has: {file: config/mozconfig.win64, platform: windows, text: "ac_add_options --disable-updater"}
  - id: win-cfg
    title: windows config only
    decided: 2026-10-14
    by: maintainer
    provenance: test
    status: enforced
    why: a check
    verify:
      - mozconfig_has: {file: config/mozconfig.win64, platform: windows, text: "ac_add_options --disable-updater"}
"""


def _patchset_root(tmp_path, nested):
    root = tmp_path / "owner"
    (root / "decisions").mkdir(parents=True)
    (root / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(CHECKS, encoding="utf-8")
    ps = root / "gorilla-patchset" if nested else root
    ps.mkdir(exist_ok=True)
    (ps / "mozconfig").write_text("ac_add_options --disable-updater\n", encoding="utf-8")
    return root


def test_linux_layout_resolves_the_patchset_prefix_and_skips_windows_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(dec, "this_platform", lambda: "linux")
    r = dec.check(_patchset_root(tmp_path, nested=False), tmp_path)
    v = {x["id"]: x for x in r["rows"]}
    assert v["cfg"]["verdict"] == "ENFORCED" and any(e.startswith("skip mozconfig_has: windows") for e in v["cfg"]["evidence"])
    assert v["win-cfg"]["verdict"] == "OTHER OS"          # every check skipped: not ENFORCED on zero checks
    assert r["ok"]


def test_windows_layout_keeps_the_subfolder(tmp_path):
    root = _patchset_root(tmp_path, nested=True)
    assert dec.owner_path(root, "gorilla-patchset/mozconfig") == root / "gorilla-patchset" / "mozconfig"
    flat = _patchset_root(tmp_path / "flat", nested=False)
    assert dec.owner_path(flat, "gorilla-patchset/mozconfig") == flat / "mozconfig"


def test_unknown_platform_on_a_check_is_a_register_problem(tmp_path):
    root = _patchset_root(tmp_path, nested=False)
    p = root / "decisions" / "PRODUCT-DECISIONS.yaml"
    p.write_text(CHECKS.replace("platform: windows,", "platform: macos,", 1), encoding="utf-8")
    assert any("unknown platform 'macos' on a check" in x for x in dec.load(root)["problems"])


def test_citations_quote_the_decisions_that_cover_a_step(tmp_path, monkeypatch):
    monkeypatch.setattr(dec, "this_platform", lambda: "linux")
    owner, _ = _owner(tmp_path)
    p = owner / "decisions" / "PRODUCT-DECISIONS.yaml"
    p.write_text(REG.replace("    models: quote", "    covers: [remote/*]\n    models: quote"), encoding="utf-8")
    t = {"meta": {"harness_root": str(owner)}}
    lines = dec.citations(t, ["remote/components/Marionette.sys.mjs"])
    assert any(l.startswith("DECISION both (enforced)") for l in lines)
    assert "  for models: quote this id, do not re-argue it" in lines
    assert any(l.startswith("DECISION both") for l in dec.citations(t, ["a.txt"]))     # named by a check
    assert dec.citations(t, ["missing.txt"]) == []           # win-only entry: not cited on linux
    assert dec.citations(t, ["browser/other.js"]) == []
    assert dec.citations({"meta": {}}, ["a.txt"]) == []     # no register: nothing, never a crash
