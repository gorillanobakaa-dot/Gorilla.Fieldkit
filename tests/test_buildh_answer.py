"""answer: the model's text change is applied only when it is in the exact form."""
import pytest

from fieldkit.buildh import answer


def _file(tmp_path):
    p = tmp_path / "prefs.js"
    p.write_bytes(b"a\nb\nc\nd\n")
    return p


def test_applies_a_well_formed_answer_after_thinking_aloud(tmp_path):
    p = _file(tmp_path)
    reply = "Let me think... the change is at line 2.\nFROM LINE: 2\nTO LINE: 3\nNEW TEXT:\n<<<\nB\nC2\nextra\n>>>\n"
    assert answer.apply(p, reply) == (2, 3, 3)
    assert p.read_bytes() == b"a\nB\nC2\nextra\nd\n"


def test_empty_block_deletes_and_crlf_is_kept(tmp_path):
    p = tmp_path / "w.cpp"
    p.write_bytes(b"x\r\ny\r\nz\r\n")
    answer.apply(p, "FROM LINE: 2\nTO LINE: 2\nNEW TEXT:\n<<<\n>>>")
    assert p.read_bytes() == b"x\r\nz\r\n"


@pytest.mark.parametrize("reply,why", [
    ("I changed the file.", "required form"),
    ("FROM LINE: 3\nTO LINE: 2\nNEW TEXT:\n<<<\nq\n>>>", "not inside"),
    ("FROM LINE: 1\nTO LINE: 99\nNEW TEXT:\n<<<\nq\n>>>", "not inside"),
    ("FROM LINE: 1\nTO LINE: 1\nNEW TEXT:\n<<<\nq\n>>>\nFROM LINE: 2\nTO LINE: 2\nNEW TEXT:\n<<<\nr\n>>>", "exactly one"),
])
def test_sloppy_answers_are_refused_not_guessed(tmp_path, reply, why):
    p = _file(tmp_path)
    with pytest.raises(answer.BadAnswer, match=why):
        answer.apply(p, reply)
    assert p.read_bytes() == b"a\nb\nc\nd\n"
