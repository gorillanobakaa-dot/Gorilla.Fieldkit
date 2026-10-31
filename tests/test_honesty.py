"""fieldkit honesty check (rule HONESTY, owner order 2026-10-31): claims backed by what the tools printed."""
import json

from fieldkit.method import honesty

EVIDENCE = "17 passed in 0.46s\n0 finding(s), 0 bottleneck(s), 1 warning(s)\n"
NV = "NOT verified: the VAIO run (cable down)\n"


def kinds(report, ev=EVIDENCE, **kw):
    return [f["kind"] for f in honesty.check(report, ev, offline=kw.pop("offline", True), **kw)]


def test_a_pass_with_the_line_the_tool_printed_is_backed():
    assert kinds("Tests passed: `17 passed in 0.46s`\n" + NV) == []


def test_a_pass_with_no_quote_or_a_quote_nobody_printed_is_unbacked():
    """Board row O3: a PASS claimed with nothing printed."""
    assert kinds("All tests passed.\n" + NV) == ["UNBACKED"]
    assert kinds("Tests passed: `42 passed in 9.9s`\n" + NV) == ["UNBACKED"]


def test_a_negated_success_word_is_not_a_claim():
    assert kinds("The build is not green yet.\n" + NV) == []


def test_the_not_verified_field_is_required_and_not_empty():
    assert kinds("nothing claimed\n") == ["NOT-VERIFIED"]
    assert kinds("NOT verified: -\n") == ["NOT-VERIFIED"]
    assert kinds("NOT verified: - (a docs-only change: nothing to run)\n") == []


def test_merged_and_pushed_are_checked_at_the_source():
    st = {"264": "MERGED", "265": "OPEN"}
    rep = "PR #264 merged (`17 passed in 0.46s`)\nmerged #265 `17 passed in 0.46s`\npushed 83ba2ab\n" + NV
    found = honesty.check(rep, EVIDENCE, gh_state=st.get, pushed=lambda sha: False)
    assert [(f["kind"], f["line"]) for f in found] == [("ACTION", 2), ("ACTION", 3)]


def test_wrote_and_recycled_files_are_checked(tmp_path):
    (tmp_path / "there.md").write_text("x")
    rep = "wrote `there.md`\nwrote `gone.md`\nrecycled `there.md`\n" + NV
    assert [f["line"] for f in honesty.check(rep, "", offline=True, base=tmp_path)] == [2, 3]


def test_a_verbatim_quote_must_be_in_its_source(tmp_path):
    (tmp_path / "brief.md").write_text("Owner: \"build it along all the items that we missed today\"")
    ok = 'verbatim: "build it along all the items" (source: brief.md)\n' + NV
    bad = 'verbatim: "build it along every item we missed" (source: brief.md)\n' + NV
    assert kinds(ok, base=tmp_path) == []
    assert kinds(bad, base=tmp_path) == ["VERBATIM"]


def test_only_tool_results_count_in_a_claude_transcript(tmp_path):
    t = tmp_path / "s.jsonl"
    rows = [{"type": "user", "sessionId": "s", "message": {"content": [
                {"type": "tool_result", "content": [{"type": "text", "text": "17 passed in 0.46s"}]}]}},
            {"type": "assistant", "sessionId": "s", "message": {"content": [
                {"type": "text", "text": "99 passed in 1.00s"}]}}]
    t.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    ev, kind = honesty.session_evidence(t)
    assert kind == "tool_result" and "17 passed" in ev and "99 passed" not in ev


def test_the_cli_exits_3_on_a_finding_and_0_clean(tmp_path, capsys):
    log = tmp_path / "job.log"
    log.write_text(EVIDENCE)
    good, bad = tmp_path / "good.md", tmp_path / "bad.md"
    good.write_text("lint `0 finding(s), 0 bottleneck(s)` all ok\n" + NV)
    bad.write_text("DONE, everything green\n" + NV)
    assert honesty.main(["check", str(good), "--log", str(log), "--offline"]) == 0
    assert honesty.main(["check", str(bad), "--log", str(log), "--offline"]) == 3
    assert honesty.main(["check", str(bad)]) == 2


def test_a_sitrep_has_its_own_sections_so_no_not_verified_field_is_demanded():
    assert kinds("Tests passed: `17 passed in 0.46s`\n", require_nv=False) == []
    assert kinds("All green.\n", require_nv=False) == ["UNBACKED"]


def test_a_done_task_note_is_checked_against_its_job_log(tmp_path):
    from fieldkit.lifecycle import tasks
    log = tmp_path / "job.log"
    log.write_text(EVIDENCE)
    t = {"log": str(log)}
    assert tasks.honesty_of(t, "tests `17 passed in 0.46s`") == []
    assert tasks.honesty_of(t, "all tests passed")[0].startswith("UNBACKED")
    assert tasks.honesty_of({}, "all tests passed") == []          # no log: nothing to judge from


def test_rotate_handover_records_unbacked_claims(tmp_path):
    from fieldkit.tokens import rotate
    t = tmp_path / "s.jsonl"
    t.write_text(json.dumps({"type": "user", "sessionId": "s", "message": {"content": [
        {"type": "tool_result", "content": "17 passed in 0.46s"}]}}), encoding="utf-8")
    assert rotate._honesty(t, "tests `17 passed in 0.46s`", "the VAIO") == []
    assert [x["kind"] for x in rotate._honesty(t, "everything green", "the VAIO")] == ["UNBACKED"]
