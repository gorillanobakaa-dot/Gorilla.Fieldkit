import pytest

from fieldkit.desk import ownership


@pytest.mark.parametrize("value", ["C:\\", "C:relative", r"\\server\share\file",
                                   r"C:\name:stream", r"C:\x\..\file", r"C:\COM1.txt", r"C:\*.txt"])
def test_refuses_nonliteral_or_broad_targets(value):
    with pytest.raises(ValueError):
        ownership.target_path(value)


def test_accepts_spaces_and_shell_metacharacters_as_literal_path():
    path = r"C:\a space & (a) %b% !c!\t.txt"
    assert str(ownership.target_path(path)) == path


def test_cancellation_has_a_distinct_result(monkeypatch, capsys):
    class Cancelled(OSError):
        winerror = 1223

    def cancel(_):
        raise Cancelled()

    monkeypatch.setattr(ownership.sys, "platform", "win32")
    monkeypatch.setattr(ownership, "take", cancel)
    assert ownership.main(["take", "C:\\one"]) == 2
    assert "Take Ownership cancelled." in capsys.readouterr().err
