# Version: 1.0.0 · updated 26-10-23-04-25
"""fieldkit mandate: one owner approval per project, valid on both computers, checkable by each (2026-10-23).
Every test closes a way a false approval could get in: a changed byte, another key, no passphrase, a stop, a date."""
from datetime import date

import pytest

from fieldkit.mandate import mandate as M

MANDATE = """project: gorilla-opencode-mac-release
goal: Gorilla OpenCode works on the 2011 MacBook Air
expires: 2026-10-30
repos:
  Gorilla.Opencode:
    push: ["feat/*", "g/*"]
    merge_to_main: {when: [full_tests_pass_here, other_machine_reviewed], how: fast-forward}
install: {budget_gb: 5}
mac: {full_access: air}
release: ask
never: [force_push, delete_outside_scratch, credentials]
"""
DAY = date(2026, 10, 23)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_MANDATE_HOME", str(tmp_path / "home"))
    M.keygen("correct horse battery")
    f = tmp_path / "home" / "mandates" / "release.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(MANDATE)
    return f


def test_approved_with_the_passphrase_it_is_in_force(owner):
    code, msg = M.approve(owner, "correct horse battery", True)
    assert code == 0 and "APPROVED" in msg
    assert M.verify(owner, DAY)[0]


def test_no_passphrase_no_seal(owner):
    # a model can fake a terminal, not the owner's passphrase (the Windows side's review, points 1-2)
    assert M.approve(owner, "guessed by a model", True)[0] == 3
    assert not M.verify(owner, DAY)[0] and not M._seal_path(owner).exists()


def test_not_confirmed_is_not_approved(owner):
    assert M.approve(owner, "correct horse battery", False)[0] == 1


def test_one_changed_byte_voids_it(owner):
    M.approve(owner, "correct horse battery", True)
    owner.write_text(MANDATE.replace("budget_gb: 5", "budget_gb: 500"))      # a session widening its own budget
    ok, why = M.verify(owner, DAY)
    assert not ok and "changed after its approval" in why
    assert M.allows(owner, "install", today=DAY, gb=1)[0] == "ask"


def test_a_seal_from_an_untrusted_key_is_refused(owner, tmp_path, monkeypatch):
    M.approve(owner, "correct horse battery", True)
    seal = M._seal_path(owner).read_text()
    monkeypatch.setenv("FIELDKIT_MANDATE_HOME", str(tmp_path / "other"))      # the other computer, before trust
    ok, why = M.verify(owner, DAY)
    assert not ok and "not trusted" in why
    M.trust_raw(bytes.fromhex((tmp_path / "home" / "owner_signing.pub").read_text().strip()))
    assert M.verify(owner, DAY)[0]                                            # after the owner pins the public key
    assert M._seal_path(owner).read_text() == seal


def test_a_forged_signature_is_refused(owner):
    import json
    M.approve(owner, "correct horse battery", True)
    s = json.loads(M._seal_path(owner).read_text())
    s["sig"] = ("00" if s["sig"][:2] != "00" else "11") + s["sig"][2:]
    M._seal_path(owner).write_text(json.dumps(s))
    assert "not valid" in M.verify(owner, DAY)[1]


def test_stop_and_expiry_win(owner):
    M.approve(owner, "correct horse battery", True)
    assert not M.verify(owner, date(2026, 10, 31))[0]                          # after expires
    M.stop(owner)
    ok, why = M.verify(owner, DAY)
    assert not ok and "STOPPED" in why
    assert M.allows(owner, "push", today=DAY, repo="Gorilla.Opencode", branch="feat/x")[0] == "ask"


def test_what_the_mandate_allows_and_what_it_does_not(owner):
    M.approve(owner, "correct horse battery", True)
    a = lambda *x, **k: M.allows(owner, *x, today=DAY, **k)[0]
    assert a("push", repo="Gorilla.Opencode", branch="feat/rpm") == "allow"
    assert a("push", repo="Gorilla.Opencode", branch="main") == "ask"
    assert a("push", repo="Gorilla.Opencode", branch="feat/rpm", force="true") == "deny"
    assert a("push", repo="Gorilla.Opencode", branch="feat/rpm", force="false") == "allow"
    assert a("push", repo="Other", branch="feat/x") == "ask"
    assert a("mac", name="air") == "allow" and a("mac", name="another") == "ask"
    assert a("release") == "ask" and a("delete_everything") == "ask"


def test_merge_needs_every_condition_and_the_reviewed_commit(owner):
    # point 5: the other machine's review names the exact commit it tested, not a branch
    M.approve(owner, "correct horse battery", True)
    ok = dict(repo="Gorilla.Opencode", commit="6cff573", full_tests_pass_here="1")
    assert M.allows(owner, "merge_main", today=DAY, **ok)[0] == "ask"
    assert M.allows(owner, "merge_main", today=DAY, other_machine_reviewed="a1b2c3d", **ok)[0] == "ask"
    assert M.allows(owner, "merge_main", today=DAY, other_machine_reviewed="6cff573", **ok)[0] == "allow"


def test_the_install_budget_is_counted_from_the_log(owner):
    M.approve(owner, "correct horse battery", True)
    assert M.allows(owner, "install", today=DAY, gb=4)[0] == "allow"
    M.log(owner, "install", "claude", gb=4)
    assert M.allows(owner, "install", today=DAY, gb=2)[0] == "ask"


def test_the_owner_reads_every_door_before_yes(owner):
    lines = "\n".join(M.doors(M.load(owner)))
    assert "PUSH" in lines and "feat/*" in lines and "MERGE" in lines and "5 GB" in lines and "never" in lines


def test_a_second_key_is_refused(owner):
    with pytest.raises(FileExistsError):
        M.keygen("another passphrase")


def test_approve_and_keygen_refuse_without_a_terminal(owner, capsys):
    assert M.main(["approve", str(owner)]) == 3
    assert "real terminal" in capsys.readouterr().out
