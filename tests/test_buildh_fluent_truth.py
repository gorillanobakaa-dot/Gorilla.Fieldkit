"""Live run 16, second pass (2026-10-01 22:19): the verifier reopened a done Fluent port and the tier removed the
owner's MOVED message a second time, leaving none where the owner's tree has one.

Rules: the copy a hunk removes is the one in the hunk's place (same neighbouring messages); with no copy there the
removal is already done. A message the owner's tree AND the pristine upstream file both have, that the tree lost,
is put back at the truth's position by the reconcile step. A hand-ported step is verified by meaning, not by the
patch's letter.
"""
from fieldkit.buildh import firefox, fluent, verify
from tests.test_buildh_fluent_moved import H2, TWICE

ONCE_MOVED = [l.replace("New Tab", "New Gorilla Tab") for i, l in enumerate(TWICE) if not (3 <= i <= 5)]   # the owner's new copy only
PRISTINE = TWICE[:9]                                                         # upstream: one copy, in the old place


def test_second_pass_does_not_take_the_moved_copy():
    new, notes, gone = fluent.port(ONCE_MOVED, H2)
    assert gone == [] and any("already removed (no copy where the hunk has it)" in n for n in notes)
    assert sum(1 for l in new if l == "user-context-add-container =") == 1
    assert fluent.copy_to_remove(fluent.entries(TWICE), "user-context-add-container", H2) == 1
    assert fluent.copy_to_remove(fluent.entries(ONCE_MOVED), "user-context-add-container", H2) is None
    # the verifier agrees: one copy, out of the hunk's place, is APPLIED
    v, _ = verify.score_hunk(ONCE_MOVED, H2, "contextual-identity.ftl")
    assert v == "APPLIED"
    assert verify.score_hunk(TWICE, H2, "contextual-identity.ftl")[0] != "APPLIED"


def test_a_lost_message_comes_back_where_the_truth_has_it(tmp_path):
    lost = [l for i, l in enumerate(ONCE_MOVED) if not (10 <= i <= 12)]      # the second pass took the moved copy
    assert "user-context-add-container =" not in lost
    new, inserted = fluent.restore_lost(lost, ONCE_MOVED, PRISTINE)
    assert inserted == [("user-context-add-container", 11)]
    assert new == ONCE_MOVED
    # a message only the truth has is a port's job; one upstream dropped is not restored
    assert fluent.restore_lost(lost, ONCE_MOVED, [l for l in PRISTINE if "add-container" not in l])[1] == []
    # as the step, with the pristine text given
    w, tr = tmp_path / "w", tmp_path / "t"
    for root, text in ((w, lost), (tr, ONCE_MOVED)):
        (root / "g").mkdir(parents=True)
        (root / "g/c.ftl").write_text("\n".join(text) + "\n", encoding="utf-8")
    t = {"workdir": str(w)}
    assert "fewer copies than the owner's tree" in fluent.check_dedupe(t, "g/c.ftl", str(tr), pristine_lines=PRISTINE)["why"][0]
    r = fluent.step_dedupe(t, "g/c.ftl", str(tr), pristine_lines=PRISTINE)
    assert r["ok"] and "restored user-context-add-container" in r["summary"]
    assert fluent.check_dedupe(t, "g/c.ftl", str(tr), pristine_lines=PRISTINE)["ok"]


def test_a_hand_port_is_verified_by_meaning():
    from tests.test_buildh_renamed import R2, H4
    after = R2[:5] + ["    if (lazy.allowTransparentBrowser) {"] + R2[9:]
    assert firefox.hand_port_holds(after, H4) == []
    assert firefox.still_holds({"done_by": "hand"}, "Tabbrowser.sys.mjs", H4, R2, after) == []
    assert firefox.hand_port_holds(R2, H4) != []
