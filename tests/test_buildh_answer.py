"""answer: line operations are applied exactly; anything sloppy is refused, never guessed at."""
import pytest

from fieldkit.buildh import answer


def _file(tmp_path, body=b"a\nb\nc\nd\ne\n"):
    p = tmp_path / "prefs.js"
    p.write_bytes(body)
    return p


def test_operations_apply_bottom_up_so_numbers_mean_what_the_model_saw(tmp_path):
    p = _file(tmp_path)
    reply = ("I think lines 2 and 3 go, and 4 changes.\n"
             "DELETE 2-3\nCHANGE 4: D2\nINSERT AFTER 5: f\nINSERT AFTER 1: a2\n")
    n, summary = answer.apply(p, reply)
    assert n == 4 and p.read_bytes() == b"a\na2\nD2\ne\nf\n"


def test_backticks_and_crlf_are_handled(tmp_path):
    p = _file(tmp_path, b"x\r\ny\r\nz\r\n")
    answer.apply(p, "```\nDELETE 2\n```")
    assert p.read_bytes() == b"x\r\nz\r\n"


def test_multiline_insert_with_code_block(tmp_path):
    p = _file(tmp_path)
    reply = ("Sure, I can help!\n"
             "```\n"
             "INSERT AFTER 2: line 1\n"
             "line 2\n"
             "  line 3\n"
             "```\n"
             "And some text here.")
    n, summary = answer.apply(p, reply)
    assert n == 1
    assert p.read_bytes() == b"a\nb\nline 1\nline 2\n  line 3\nc\nd\ne\n"


def test_multiline_change(tmp_path):
    p = _file(tmp_path)
    reply = "```\nCHANGE 3: C\nD\nE\n```"
    n, summary = answer.apply(p, reply)
    assert n == 1
    assert p.read_bytes() == b"a\nb\nC\nD\nE\nd\ne\n"


@pytest.mark.parametrize("reply,why", [
    ("I changed the file.", "no operations"),
    ("DELETE 9", "not inside"),
    ("DELETE 2-3\nCHANGE 3: q", "two operations"),
    ("CHANGE 0: q", "not inside"),
])
def test_sloppy_answers_are_refused_and_the_file_is_untouched(tmp_path, reply, why):
    p = _file(tmp_path)
    with pytest.raises(answer.BadAnswer, match=why):
        answer.apply(p, reply)
    assert p.read_bytes() == b"a\nb\nc\nd\ne\n"


# ── question parsing ────────────────────────────────────────────────────────

def test_parse_questions_correct_answer():
    text = "745 REMOVE\n749 KEEP\n750 KEEP"
    assert answer.parse_questions(text, {745, 749, 750}) == [(745, "remove"), (749, "keep"), (750, "keep")]


def test_parse_questions_with_thinking_preamble():
    text = "I think 745 is old.\n```\n745 REMOVE\n749 KEEP\n```\nDone."
    assert answer.parse_questions(text, {745, 749}) == [(745, "remove"), (749, "keep")]


def test_parse_questions_missing_answer():
    with pytest.raises(answer.BadAnswer, match=r"line\(s\) not answered: \[750\]"):
        answer.parse_questions("745 REMOVE\n749 KEEP", {745, 749, 750})


def test_parse_questions_double_answer():
    with pytest.raises(answer.BadAnswer, match="line 745 answered twice"):
        answer.parse_questions("745 REMOVE\n745 KEEP\n749 KEEP", {745, 749})


def test_parse_questions_unknown_line():
    with pytest.raises(answer.BadAnswer, match=r"line 746 is not a question \(asked: \[745, 749\]\)"):
        answer.parse_questions("745 REMOVE\n746 REMOVE\n749 KEEP", {745, 749})


def test_parse_questions_free_text_only():
    with pytest.raises(answer.BadAnswer, match="no answers found"):
        answer.parse_questions("I think they should all be removed.", {745, 749})


def test_parse_questions_wrong_word():
    with pytest.raises(answer.BadAnswer, match="no answers found"):
        answer.parse_questions("745 DELETE", {745})
