"""OS parity inventory: a new single-system card fails unless the inventory names a counterpart, a port or a native reason."""
from fieldkit.desk import osparity, platformcheck


def _card(cid, plats):
    return {"id": cid, "platforms": plats, "title": "x"}


def test_shipped_inventory_matches_cards():
    assert osparity.problems(platformcheck.cards()) == []


def test_new_single_os_card_without_row_fails():
    p = osparity.problems([_card("brand-new", ["windows"])], rows={})
    assert len(p) == 1 and "brand-new" in p[0] and "Linux" in p[0]


def test_dual_card_needs_no_row():
    assert osparity.problems([_card("both", ["linux", "windows"])], rows={}) == []


def test_native_only_needs_a_real_reason():
    rows = {"c": {"status": "NATIVE-ONLY", "reason": "no"}}
    assert osparity.problems([_card("c", ["linux"])], rows=rows)
    rows = {"c": {"status": "NATIVE-ONLY", "reason": "reads the Linux kernel log for firmware"}}
    assert osparity.problems([_card("c", ["linux"])], rows=rows) == []


def test_port_needs_mechanism_and_order():
    c = [_card("c", ["windows"])]
    assert osparity.problems(c, rows={"c": {"status": "PORT", "order": 1, "mechanism": ""}})
    assert osparity.problems(c, rows={"c": {"status": "PORT", "mechanism": "systemd user timer instead"}})
    assert osparity.problems(c, rows={"c": {"status": "PORT", "order": 1, "mechanism": "systemd user timer instead"}}) == []


def test_unknown_status_and_same_os_counterpart_fail():
    cards = [_card("a", ["windows"]), _card("b", ["windows"])]
    rows = {"a": {"status": "EQUIVALENT", "counterpart": "b"}, "b": {"status": "MAYBE"}}
    p = osparity.problems(cards, rows=rows)
    assert any("counterpart" in x for x in p) and any("MAYBE" in x for x in p)


def test_stale_row_after_a_port_is_flagged():
    rows = {"ported": {"status": "PORT", "order": 1, "mechanism": "systemd user timer instead"}}
    assert osparity.problems([_card("ported", ["linux", "windows"])], rows=rows)


def test_platformcheck_reports_the_missing_row():
    r = platformcheck.check([_card("zzz-new", ["windows"])], frozen={"zzz-new"})
    assert any("os_parity" in x for x in r["problems"])


def test_doc_is_generated_from_the_inventory():
    assert osparity.DOC.read_text(encoding="utf-8").rstrip("\n") == osparity.markdown(osparity.inventory()).rstrip("\n"), \
        "run: fieldkit parity os --write"


# ---- the inventory beyond cards, the fixed record, the briefs ----------------------------------------------------------
import json
import sys

import pytest


def _found(kind="module", name="fieldkit/x.py", present=("windows",)):
    return {"id": f"{kind}:{name}", "kind": kind, "name": name, "present_on": list(present),
            "missing_on": ["linux"], "evidence": f"{name}:3"}


def test_shipped_items_match_the_scanners():
    assert osparity.item_problems() == []


def test_new_single_os_module_without_record_fails():
    p = osparity.item_problems(items={}, found=[_found()])
    assert len(p) == 1 and "module:fieldkit/x.py" in p[0]


def test_stale_item_record_fails():
    rec = {"module:gone.py": {"status": "NATIVE-ONLY", "reason": "a long enough sentence here"}}
    assert any("gone.py" in x for x in osparity.item_problems(items=rec, found=[]))


def test_gap_record_needs_its_fields_but_document_needs_only_a_target():
    f = [_found()]
    bad = {"module:fieldkit/x.py": {"status": "PARTIAL", "action": "PORT", "target": "t"}}
    assert any("mechanism" in x for x in osparity.item_problems(items=bad, found=f))
    ok = {"module:fieldkit/x.py": {"status": "PARTIAL", "action": "PORT", "target": "t", "mechanism": "m", "effort": "S",
                                   "proof": "p", "order": 2}}
    assert osparity.item_problems(items=ok, found=f) == []
    doc = {"module:fieldkit/x.py": {"status": "INCOMPATIBLE", "action": "DOCUMENT", "target": "the twin command"}}
    assert osparity.item_problems(items=doc, found=f) == []
    assert any("action" in x for x in osparity.item_problems(
        items={"module:fieldkit/x.py": {**ok["module:fieldkit/x.py"], "action": "FIX"}}, found=f))


def test_every_record_has_the_fixed_fields_and_four_lists_cover_them():
    recs = osparity.records()
    assert recs and all(set(osparity.FIELDS) <= set(r) for r in recs)
    ls = osparity.lists(recs)
    assert sum(len(v) for v in ls.values()) == len(recs)
    assert {r["status"] for r in recs} <= set(osparity.REC_STATUS)


def test_output_is_the_same_on_both_systems(monkeypatch, capsys):
    outs = []
    for plat in ("win32", "linux"):
        monkeypatch.setattr(sys, "platform", plat)
        osparity.main(["--json"])
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1] and json.loads(outs[0])["summary"]["records"] > 50


def test_scanners_find_a_one_sided_guard_and_pipeline_stages(tmp_path):
    m = tmp_path / "fieldkit" / "pkg"
    m.mkdir(parents=True)
    (m / "mod.py").write_text('import sys\n\ndef f():\n    if sys.platform != "win32":\n        return []\n    return 1\n')
    (m / "both.py").write_text('import sys\n\ndef f():\n    if sys.platform != "win32":\n        return 2\n    return 1\n')
    got = osparity.scan_modules(tmp_path)
    assert [(g["name"], g["present_on"]) for g in got] == [("fieldkit/pkg/mod.py", ["windows"])]
    d = tmp_path / "fieldkit" / "build" / "pipelines"
    d.mkdir(parents=True)
    (d / "p.yaml").write_text("name: p\nstages:\n  - {id: a, platforms: [debian]}\n  - {id: b, platforms: [linux]}\n")
    assert [(g["id"], g["present_on"]) for g in osparity.scan_stages(tmp_path)] == [("stage:p", ["linux"])]


def test_briefs_are_data_only_and_gaps_exit_3(tmp_path, capsys):
    n = osparity.briefs(osparity.records(), tmp_path / "b")
    files = list((tmp_path / "b").glob("parity-*.md"))
    assert n == len(files) > 0 and "fieldkit task start" in files[0].read_text(encoding="utf-8")
    assert osparity.gaps(osparity.records())
    assert osparity.main([]) == 3


def test_since_limits_records_to_touched_evidence(monkeypatch):
    class R:
        stdout = "fieldkit/power/drift.py\n"
    monkeypatch.setattr("subprocess.run", lambda *a, **k: R)
    got = osparity.since("origin/main", osparity.records())
    assert got and all(r["evidence"].startswith("fieldkit/power/drift.py") for r in got)
