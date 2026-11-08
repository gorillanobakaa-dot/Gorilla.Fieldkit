from fieldkit.capture import weaktest as W

REC = {"id": "r", "goal": "Save the work.", "accepted": True, "steps": [
    {"id": "a", "sentence": "Save your changes as a commit.", "check": "The commit shows in the log.",
     "failures": [{"error": "hook refused the commit"}]},
    {"id": "b", "sentence": "Run the work through the guard so heavy jobs run one at a time and the machine stays cool "
                            "while every single test in the whole suite runs again.", "check": "It passes.", "failures": []}]}


def test_reader_check_counts_the_long_sentence():
    r = W.reader_check(REC)
    assert (r["steps"], r["passed"]) == (2, 1) and r["failed"][0]["step"] == "b"


def test_verdict_takes_the_last_word_and_none_when_absent():
    assert W.verdict("It may FAIL at first, so: PASS") == "PASS"
    assert W.verdict("no idea") is None


def test_cases_use_the_recorded_error():
    cs = W.cases(REC)
    assert cs[1] == ("a", "exit code 1\nhook refused the commit", "FAIL") and len(cs) == 4


def test_model_check_scores_wrong_and_missing_answers():
    ok = W.model_check(REC, lambda t: "FAIL" if "exit code 1" in t else "PASS")
    assert ok["correct"] == 4
    bad = W.model_check(REC, lambda t: "PASS")
    assert bad["correct"] == 2 and {w["expected"] for w in bad["wrong"]} == {"FAIL"}
    assert W.model_check(REC, lambda t: "")["correct"] == 0


def test_run_without_model_says_not_verified_and_records_a_runner_error():
    res = W.run([REC])
    assert "not verified" in res["model_note"] and "model" not in res["recipes"][0]

    def boom(t):
        raise OSError("down")
    res = W.run([REC], runner=boom)
    assert res["recipes"][0]["model"]["error"].startswith("not verified")


def test_shipped_recipes_pass_the_reader_rules():
    for rec in W.load_recipes():
        assert not W.reader_check(rec)["failed"], rec["id"]


def test_doc_skill_preflight_is_listed_offered_and_shown_as_data():
    from fieldkit.capture import workflows as WF

    recipes = WF.load()
    recipe = recipes["doc-skill-preflight"]
    assert recipe["accepted"] is True
    assert any("doc-skill-preflight" in line for line in WF.list_lines())
    assert "doc-skill-preflight" in WF.offer_line("preflight this skill doc")
    shown = "\n".join(WF.show_lines(recipe))
    assert "fieldkit skills validate PATH --strict --json" in shown
    assert "fieldkit cards show skill-frontmatter-check --json" in shown
    assert not W.reader_check(recipe)["failed"]


def test_workflow_offer_matches_intent_only_for_accepted_recipes(tmp_path):
    import yaml
    from fieldkit.capture import workflows as WF
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(dict(REC, id="recipe-release", intent="release", title="T")))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump({"id": "procedure-build", "intent": "build", "reason": "x"}))
    assert "recipe-release" in WF.offer_line("please publish the release", tmp_path)
    assert WF.offer_line("compile the kernel", tmp_path) == ""          # build is only a procedure
    assert WF.offer_line("hello", tmp_path) == ""
    assert WF.show_lines(WF.load(tmp_path)["recipe-release"])[2].startswith("1. Save your changes")


def test_prompt_hook_carries_the_offer():
    from fieldkit.autopilot import room
    out = room._workflows({"prompt": "publish the release notes"}, "UserPromptSubmit", "")
    assert out.startswith("WORKFLOW:")
    assert room._workflows({"prompt": "publish the release"}, "PreToolUse", "") == ""
