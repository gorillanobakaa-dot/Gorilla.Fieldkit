# Version: 1.0.0 · updated 26-10-23-18-04
"""fieldkit peers link toolset|startcheck|restart|verify-start: the 2026-10-22 night's manual steps as tools."""
import pytest

from fieldkit.peers import link, link_ops, link_relay


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    return tmp_path


SAME = {"os": "linux", "python": "3.13.5", "pytest": "9.1.1", "yaml": "6.0.3", "psutil": "7.2.2", "git": "2.47.3",
        "claude": "2.1.302", "link_commit": "bac2a1c"}


def test_same_toolset_has_no_gaps_and_git_or_python_minor_do_not_count():
    win = dict(SAME, os="win32", python="3.12.10", git="2.55.0")
    assert link_ops.compare(SAME, win) == []
    assert link_ops.toolset_lines(SAME, win)[-1].startswith("SAME TOOLSET")


def test_the_one_gap_of_that_night_is_named_with_its_fix():
    # 2026-10-22: claude 2.1.296 on the VAIO, 2.1.302 on the laptop, found by chat
    gaps = link_ops.compare(dict(SAME, claude="2.1.296"), SAME)
    assert [(g[0], g[1], g[2]) for g in gaps] == [("claude", "2.1.296", "2.1.302")]
    assert "claude update" in gaps[0][3]
    pip = link_ops.compare(dict(SAME, pytest=None), SAME)[0]
    assert pip[0] == "pytest" and "pip install pytest==9.1.1" in pip[3]


def test_python_older_than_3_11_is_a_gap():
    assert any(g[0] == "python" for g in link_ops.compare(SAME, dict(SAME, python="3.10.4")))


def _ready(monkeypatch, tmp_path):
    monkeypatch.setattr(link, "approvals", lambda: ({"allow_start": ["claude"], "cwd": str(tmp_path)}, "approved"))
    monkeypatch.setattr(link, "autopilot_flags", lambda: {"--cwd", "--max-turns", "--help"})
    from fieldkit.autopilot import run as aprun
    monkeypatch.setattr(aprun, "claude_exe", lambda: "/bin/claude")
    return aprun


def rows(r):
    return {name: ok for name, ok, _ in r}


def test_startcheck_passes_on_a_ready_computer(monkeypatch, tmp_path):
    _ready(monkeypatch, tmp_path)
    r = rows(link_ops.startcheck())
    assert all(r.values()), r


def test_startcheck_catches_the_brief_in_argv(monkeypatch, tmp_path):
    aprun = _ready(monkeypatch, tmp_path)
    def old_claude(prompt, popen=None, **kw):        # the pre-209debc shape: the prompt as a word on the line
        return popen(["claude", "-p", prompt]).communicate(None)
    monkeypatch.setattr(aprun, "claude", old_claude)
    assert rows(link_ops.startcheck())["brief goes on stdin, not argv"] is False


def test_startcheck_catches_unapproved_and_missing_claude(monkeypatch, tmp_path):
    aprun = _ready(monkeypatch, tmp_path)
    monkeypatch.setattr(link, "approvals", lambda: ({}, "no approved link.yaml"))
    monkeypatch.setattr(aprun, "claude_exe", lambda: None)
    r = rows(link_ops.startcheck())
    assert r["approved in link.yaml"] is False and r["claude found"] is False


def test_startcheck_catches_a_fieldkit_from_the_working_folder(monkeypatch, tmp_path):
    _ready(monkeypatch, tmp_path)
    other = tmp_path / "main-checkout" / "fieldkit" / "__init__.py"
    monkeypatch.setattr(link_ops, "_run", lambda argv, **kw: (0, str(other), ""))
    assert rows(link_ops.startcheck(run=lambda argv, **kw: (0, str(other), "")))["loads the link's own fieldkit"] is False


def note(text, evidence=""):
    item = link.store("message", {"from_host": "OTHER", "text": text})
    if evidence:                                      # what the sender's --done --evidence produces on arrival
        p = sorted((link.home() / "inbox").glob(f"{item['n']:04d}-*.json"))[0]
        import json
        d = json.loads(p.read_text())
        d["evidence"] = evidence
        p.write_text(json.dumps(d))
    return item


def test_verify_start_needs_done_and_evidence(home):
    note("funny.gorilla hello")                                   # no [DONE]
    note("[FROM LINUX #2] [DONE] hello", evidence="")             # no evidence
    clock = {"t": 0}
    code, item = link_ops.verify_start(0, seconds=5, now=lambda: clock["t"],
                                       sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert code == 3 and item is None
    note("[FROM LINUX #3] [DONE] hello", evidence="/x/fieldkit/__init__.py")
    code, item = link_ops.verify_start(0, seconds=0)
    assert code == 0 and item["n"] == 3


def test_relay_doctor_says_how_to_start_the_listener(home):
    code, lines = link_relay.doctor()
    assert code == 3 and "relay listen" in lines[-1] and "NOT LISTENING" in lines[-1]


def test_cli_dispatch(home, capsys):
    assert link.main(["verify-start"]) == 2
    assert "--since N" in capsys.readouterr().out


def test_approve_flags_reach_approve_in_one_call(home, monkeypatch):
    got = []
    monkeypatch.setattr(link, "approve", lambda **kw: got.append(kw) or 0)
    assert link.main(["approve", "--allow", "claude", "--allow", "codex,opencode", "--cwd", "/w", "--wall-minutes", "90"]) == 0
    assert got == [{"allow": ["claude", "codex", "opencode"], "cwd": "/w", "wall_minutes": 90}]
    assert link.main(["approve"]) == 0 and got[-1] == {}
