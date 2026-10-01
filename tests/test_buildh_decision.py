"""Decision briefs: analysis first, a safe default, and no change without a typed sentence at a real terminal."""
import os
import subprocess
import time

import pytest

from fieldkit.buildh import decision, task


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    r = tmp_path / "owner"
    (r / "config").mkdir(parents=True)
    (r / "logs").mkdir()
    (r / "config" / "m.cfg").write_text("keep this\nopt --with-ccache=sccache\n")
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=o", "-c", "user.email=o@example.com", "commit", "-q", "-m", "verified config"], check=True)
    (r / "logs" / "old-run.log").write_text("configure: --with-ccache=sccache found\n")
    past = time.time() - 3600
    os.utime(r / "logs" / "old-run.log", (past - 5000, past - 5000))
    (r / "config" / "m.cfg").write_text("keep this\nopt --with-ccache=/some/other/path/sccache.exe\n")
    return r


def test_a_clean_file_needs_no_decision(repo):
    subprocess.run(["git", "-C", str(repo), "checkout", "--", "config/m.cfg"], check=True)
    assert decision.owner_file_edit(repo, "config/m.cfg")["options"] == []


def test_the_brief_measures_instead_of_asking(repo):
    b = decision.owner_file_edit(repo, "config/m.cfg")
    text = "\n".join(decision.render(b))
    assert b["door"] == "two-way" and b["recommended"] == "revert"
    assert "OLD text" in text and "was in use during a real run" in text         # the old line is proven by a recorded run
    assert "has never been exercised" in text                                       # the new one never ran
    assert "NOT DETERMINED" in text and "Default if you do nothing: hold" in text
    assert "PUT BACK THE OLD m.cfg" in text


def test_something_ran_after_the_change_makes_it_a_one_way_door(repo):
    (repo / "logs" / "new-build.log").write_text("a build ran after the edit\n")
    os.utime(repo / "logs" / "new-build.log", (time.time() + 60, time.time() + 60))
    b = decision.owner_file_edit(repo, "config/m.cfg")
    assert b["door"] == "one-way" and b["recommended"] == "hold"
    assert "hide what was built" in b["why"]


def test_hold_changes_nothing_and_needs_nothing(repo):
    b = decision.owner_file_edit(repo, "config/m.cfg")
    before = (repo / "config" / "m.cfg").read_text()
    assert decision.apply(b, "hold", "", repo) == "nothing changed"
    assert (repo / "config" / "m.cfg").read_text() == before


def test_a_click_or_a_yes_is_not_enough_to_revert(repo, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    for typed in ("", "yes", "y", "revert", "REVERT", "put back"):
        with pytest.raises(task.Refused, match="type exactly"):
            decision.apply(b, "revert", typed, repo)


def test_an_agents_shell_cannot_revert_even_with_the_sentence(repo, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    with pytest.raises(task.Refused, match="real terminal"):
        decision.apply(b, "revert", b["confirm"], repo)
    assert "other/path" in (repo / "config" / "m.cfg").read_text()


def test_the_typed_sentence_at_a_terminal_reverts_and_saves_the_change(repo, monkeypatch, tmp_path):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    monkeypatch.setattr(decision, "COOLING_OFF_SECONDS", 0)
    decision.show(b)
    out = tmp_path / "saved"
    out.mkdir()
    msg = decision.apply(b, "revert", b["confirm"], out)
    assert "sccache.exe" not in (repo / "config" / "m.cfg").read_text() and "restored" in msg
    saved = next(out.glob("*.diff"))
    subprocess.run(["git", "-C", str(repo), "apply", str(saved)], check=True)         # the change can be put back
    assert "other/path" in (repo / "config" / "m.cfg").read_text()


# -- the reader may be someone with no IT knowledge ------------------------------------------------------

def test_the_plain_brief_has_no_jargon_and_leads_with_what_is_safe(repo):
    b = decision.owner_file_edit(repo, "config/m.cfg")
    text = "\n".join(decision.render_plain(b))
    assert "NOTHING HAS BEEN HURT" in text and "Nothing. That is the safe answer" in text
    for jargon in ("diff", "hash", "two-way", "blast", "git ", "revert", "commit", "fingerprint"):
        assert jargon not in text.lower(), jargon
    assert "pressing yes" in text and "PUT BACK THE OLD m.cfg" in text


def test_the_plain_brief_does_not_say_all_is_well_when_something_ran_afterwards(repo):
    (repo / "logs" / "new-build.log").write_text("a build ran after the edit\n")
    os.utime(repo / "logs" / "new-build.log", (time.time() + 60, time.time() + 60))
    text = "\n".join(decision.render_plain(decision.owner_file_edit(repo, "config/m.cfg")))
    assert "NOTHING HAS BEEN HURT" not in text.split("What happened")[1] or "Possibly" in text
    assert "do NOT change anything yet" in text and "PUT BACK THE OLD m.cfg" not in text


def test_nothing_is_changed_before_the_explanation_has_been_shown(repo, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    with pytest.raises(task.Refused, match="not been shown"):
        decision.apply(b, "revert", b["confirm"], repo)
    assert "other/path" in (repo / "config" / "m.cfg").read_text()


def test_a_change_is_never_one_quick_click_the_pause_is_enforced(repo, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    decision.show(b)
    with pytest.raises(task.Refused, match="wait .* more seconds"):
        decision.apply(b, "revert", b["confirm"], repo)
    assert "other/path" in (repo / "config" / "m.cfg").read_text()


def test_a_stale_explanation_is_refused_if_the_file_changed_again(repo, monkeypatch):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    monkeypatch.setattr(decision, "COOLING_OFF_SECONDS", 0)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    decision.show(b)
    (repo / "config" / "m.cfg").write_text("someone changed it yet again\n")
    with pytest.raises(task.Refused, match="changed again"):
        decision.apply(b, "revert", b["confirm"], repo)


def test_what_was_shown_and_what_was_done_is_logged_in_a_chain(repo, monkeypatch, tmp_path):
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    monkeypatch.setattr(decision, "COOLING_OFF_SECONDS", 0)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    decision.show(b)
    out = tmp_path / "saved"
    out.mkdir()
    decision.apply(b, "revert", b["confirm"], out)
    lines = decision._log_path().read_text(encoding="utf-8").splitlines()
    assert [__import__("json").loads(l)["event"] for l in lines] == ["shown", "reverted"]
    assert __import__("json").loads(lines[1])["prev"] == task.line_hash(lines[0])


def test_a_file_the_harness_knows_is_explained_by_what_it_is_for():
    assert "how to build Firefox" in decision._what_is("config/mozconfig.win64")
    assert decision._what_is("whatever/x.cfg") == "one of your project files"


def test_applying_a_fix_to_a_file_that_is_already_back_says_nothing_to_do(repo, monkeypatch):
    """The owner ran the command after the file had already been restored: KeyError 'confirm' (2026-10-01 20:34)."""
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    subprocess.run(["git", "-C", str(repo), "checkout", "--", "config/m.cfg"], check=True)
    b = decision.owner_file_edit(repo, "config/m.cfg")
    assert decision.apply(b, "revert", "PUT BACK THE OLD m.cfg", repo).startswith("nothing to do")
