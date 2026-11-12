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
