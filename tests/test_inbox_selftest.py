"""`fieldkit inbox self-test` (goal integrated-everywhere, c7): the owner inbox is empty by construction."""
from fieldkit import cli
from fieldkit.autopilot import inbox, inbox_selftest


def test_the_self_test_passes_and_leaves_the_real_inbox_alone(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "real-inbox")
    assert inbox_selftest.run() == 0
    out = capsys.readouterr().out
    assert "PASS agent approvals refused" in out and "PASS rollcall" in out and "PASS control" in out
    assert not (tmp_path / "real-inbox").exists()


def test_the_self_test_fails_when_an_agent_item_reaches_the_owner(monkeypatch, capsys):
    monkeypatch.setattr(inbox, "OWNER_KINDS", ("hardline", "approval"))
    assert inbox_selftest.run() == 3
    assert "FAIL agent items reached the owner inbox" in capsys.readouterr().out


def test_the_self_test_is_a_command():
    assert cli.build_parser().parse_args(["inbox", "self-test"]).action == "self-test"
