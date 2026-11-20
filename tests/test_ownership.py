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


@pytest.mark.parametrize("action", ["take", "take-drive"])
def test_cancellation_has_a_distinct_result(monkeypatch, capsys, action):
    class Cancelled(OSError):
        winerror = 1223

    def cancel(*args, **kwargs):
        raise Cancelled()

    monkeypatch.setattr(ownership.sys, "platform", "win32")
    monkeypatch.setattr(ownership, "take", cancel)
    assert ownership.main([action, "C:/one" if action == "take" else "C:/"]) == 2
    assert "Take Ownership cancelled." in capsys.readouterr().err


def test_cancelled_drive_confirmation_never_requests_elevation(monkeypatch):
    def unexpected_take(*args, **kwargs):
        pytest.fail("cancelling the drive confirmation should prevent the ownership operation")

    monkeypatch.setattr(ownership.sys, "platform", "win32")
    monkeypatch.setattr(ownership, "_confirm_drive", lambda path, recover: False)
    monkeypatch.setattr(ownership, "take", unexpected_take)
    assert ownership.main(["take-drive", "--explorer", "D:/"]) == 2


def test_drive_root_runs_only_through_the_recursive_action():
    path = ownership.target_path("D:/", drive=True)
    assert str(path) == "D:\\"
    assert ownership._takeown_args(path, drive=True) == ["/f", str(path), "/r", "/skipsl", "/d", "N"]
    assert ownership._takeown_args(path, drive=True, recover_inaccessible=True)[-1] == "Y"
    assert ownership._takeown_args(path / "one.txt") == ["/f", str(path / "one.txt")]
    for invalid in ("D:", "D:/folder", "//server/share"):
        with pytest.raises(ValueError):
            ownership.target_path(invalid, drive=True)


def test_cli_dispatches_whole_drive_with_explicit_permission_recovery(monkeypatch):
    calls = []
    monkeypatch.setattr(ownership.sys, "platform", "win32")
    monkeypatch.setattr(ownership, "take", lambda *args, **kw: calls.append((args, kw)) or "verified")
    assert ownership.main(["take-drive", "D:/"]) == 0
    assert ownership.main(["take-drive", "D:/", "--recover-inaccessible"]) == 0
    assert calls == [
        (("D:/",), {"drive": True, "recover_inaccessible": False}),
        (("D:/",), {"drive": True, "recover_inaccessible": True}),
    ]
