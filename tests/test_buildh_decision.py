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
    assert "REVERT config/m.cfg" in text


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
    for typed in ("", "yes", "y", "revert", "REVERT"):
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
    out = tmp_path / "saved"
    out.mkdir()
    msg = decision.apply(b, "revert", b["confirm"], out)
    assert "sccache.exe" not in (repo / "config" / "m.cfg").read_text() and "restored" in msg
    saved = next(out.glob("*.diff"))
    subprocess.run(["git", "-C", str(repo), "apply", str(saved)], check=True)         # the change can be put back
    assert "other/path" in (repo / "config" / "m.cfg").read_text()
