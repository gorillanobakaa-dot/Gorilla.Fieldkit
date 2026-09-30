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
