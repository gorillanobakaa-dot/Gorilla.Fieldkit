# Version: 1.1.0 · updated 26-10-23-11-10
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
    test: {cmd: "python -m pytest -q"}
install: {budget_gb: 5}
mac: {full_access: air}
owner_approvals: ["build-harness accept"]
release: ask
never: [force_push, delete_outside_scratch, credentials]
"""
DAY = date(2026, 10, 23)


class Facts:
    """proofs.py's shape, with the answers fixed: what git and the provers would find."""

    def __init__(self, repo="Gorilla.Opencode", branch="feat/rpm", ff=True, proven=()):
        self.repo, self.branch, self.ff, self.proven = repo, branch, ff, set(proven)
        self.PROVERS = {w: (lambda w: lambda rp, c, cfg: (w in self.proven, w))(w)
                        for w in ("full_tests_pass_here", "other_machine_reviewed", "ci_green_both")}

    def where(self, repo_path):
        return self.repo, self.branch

    def fast_forward(self, repo_path, branch):
        return self.ff, "ff" if self.ff else "not a fast-forward"


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
    assert M.allows(owner, "push", today=DAY, repo_path=".", provers=Facts())[0] == "ask"


def test_what_the_mandate_allows_and_what_it_does_not(owner):
    M.approve(owner, "correct horse battery", True)
    a = lambda *x, **k: M.allows(owner, *x, today=DAY, **k)[0]
    push = lambda **f: a("push", repo_path=".", provers=Facts(**f))
    assert push() == "allow"
    assert push(branch="main") == "ask"
    assert push(repo="Other", branch="feat/x") == "ask"
    assert a("push", repo_path=".", force="true", provers=Facts()) == "deny"
    assert a("mac", name="air") == "allow" and a("mac", name="another") == "ask"
    assert a("release") == "ask" and a("delete_everything") == "ask"


def test_a_push_that_is_not_a_fast_forward_is_not_allowed(owner):
    # M1.1, the review's point 4: fast-forward-only was written in the mandate and checked nowhere
    M.approve(owner, "correct horse battery", True)
    v, why = M.allows(owner, "push", today=DAY, repo_path=".", provers=Facts(ff=False))
    assert v == "ask" and "fast-forward" in why


def test_the_caller_cannot_assert_a_proof(owner, capsys):
    # M1.1, the review's point 4: full_tests_pass_here=1 other_machine_reviewed=<sha> used to be enough
    M.approve(owner, "correct horse battery", True)
    said = dict(repo="Gorilla.Opencode", branch="feat/rpm", commit="6cff573", full_tests_pass_here="1",
                other_machine_reviewed="6cff573")
    assert M.allows(owner, "merge_main", today=DAY, **said)[0] == "ask"
    assert M.allows(owner, "push", today=DAY, repo="Gorilla.Opencode", branch="feat/rpm")[0] == "ask"
    assert M.main(["allows", str(owner), "merge_main", "full_tests_pass_here=1", "commit=6cff573"]) == 2
    assert "cannot be given" in capsys.readouterr().out


def test_merge_needs_every_condition_proven_by_the_tool(owner):
    M.approve(owner, "correct horse battery", True)
    m = lambda proven: M.allows(owner, "merge_main", today=DAY, repo_path=".", commit="6cff573",
                                provers=Facts(proven=proven))
    assert m(["full_tests_pass_here"])[0] == "ask"
    v, why = m(["full_tests_pass_here", "other_machine_reviewed"])
    assert v == "allow" and "6cff573" in why


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


def test_the_passphrase_needs_twelve_characters(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_MANDATE_HOME", str(tmp_path / "h"))
    with pytest.raises(ValueError):
        M.keygen("eleven char")


def test_the_mandate_home_from_the_environment_counts_only_under_pytest(tmp_path, monkeypatch):
    # M1.1, the review's point 2: FIELDKIT_MANDATE_HOME=<dir> fieldkit ship push brought its own trust store
    monkeypatch.setenv("FIELDKIT_MANDATE_HOME", str(tmp_path / "mine"))
    assert M.home() == tmp_path / "mine"
    monkeypatch.setattr(M, "_under_test", lambda: False)
    assert M.home() == M.Path.home() / ".fieldkit" / "mandate"


def test_a_trusted_key_must_match_its_file_name(owner, tmp_path):
    # M1.1, the review's point 3: <owner-fp>.pub holding another key passed
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    M.approve(owner, "correct horse battery", True)
    fp = M._seal_path(owner).read_text().split('"key": "')[1].split('"')[0]
    other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,
                                                                    serialization.PublicFormat.Raw)
    (M.trusted_dir() / f"{fp}.pub").write_text(other.hex())
    assert fp not in M.trusted_keys()
    assert not M.verify(owner, DAY)[0]


def test_a_stop_is_permanent_for_that_mandate(owner):
    # M1.1, the review's point 5: deleting the .STOP lifted it
    M.approve(owner, "correct horse battery", True)
    M.stop(owner)
    M.stop_path(owner).unlink()
    ok, why = M.verify(owner, DAY)
    assert not ok and "STOPPED" in why
    assert M.approve(owner, "correct horse battery", True)[0] == 4
    owner.write_text(MANDATE.replace("budget_gb: 5", "budget_gb: 4"))    # a changed mandate is a new approval
    assert M.approve(owner, "correct horse battery", True)[0] == 0 and M.verify(owner, DAY)[0]


# ---- the provers (proofs.py): a model can write a file, not a signature ------------------------------------------------
def _signed_note(k, evidence, os_name="WINDOWS", done=True):
    from fieldkit.peers import link
    req = link.sign({"verb": "message", "text": "[FROM WINDOWS #50] [DONE] review", "done": done,
                     "evidence": evidence, "seq": 50, "os": os_name}, k)
    return {"n": 1, "kind": "message", "text": req["text"], "signed": req}


def test_the_review_note_is_checked_again_with_the_link_key(tmp_path):
    import json
    from fieldkit.mandate import proofs
    k = b"k" * 32
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    rv = lambda: proofs.other_machine_reviewed(".", "6cff5731234", {}, link_home=tmp_path, link_key=k, my_os="LINUX")
    forged = _signed_note(b"x" * 32, "reviewed Gorilla.Opencode 6cff573: 120 passed")   # another key
    (inbox / "0001-message.json").write_text(json.dumps(forged))
    assert not rv()[0]
    (inbox / "0002-message.json").write_text(json.dumps(_signed_note(k, "reviewed Gorilla.Opencode a1b2c3d")))
    assert not rv()[0]                                                                    # another commit
    (inbox / "0003-message.json").write_text(json.dumps(_signed_note(k, "reviewed 6cff573", os_name="LINUX")))
    assert not rv()[0]                                                                    # from this computer
    (inbox / "0004-message.json").write_text(json.dumps(_signed_note(k, "reviewed 6cff573: 120 passed")))
    assert rv()[0]


def test_the_suite_must_show_a_pass_not_only_exit_0(tmp_path, monkeypatch):
    import subprocess
    from fieldkit.mandate import proofs
    monkeypatch.setattr(proofs, "head", lambda rp: "abc")
    monkeypatch.setattr(proofs, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    run = lambda out, rc=0: (lambda *a, **k: subprocess.CompletedProcess(a, rc, out, ""))
    cfg = {"test": {"cmd": "python -m pytest -q"}}
    assert not proofs.full_tests_pass_here(".", "abc", cfg, run=run("no tests ran"))[0]
    assert not proofs.full_tests_pass_here(".", "abc", cfg, run=run("3 failed, 9 passed", 1))[0]
    assert not proofs.full_tests_pass_here(".", "abc", {}, run=run("9 passed"))[0]        # no command in the mandate
    assert proofs.full_tests_pass_here(".", "abc", cfg, run=run("9 passed in 2.1s"))[0]


def test_a_note_stored_by_the_link_keeps_its_signature_for_the_prover(tmp_path, monkeypatch):
    # the inbox kept only the text: nothing could check later that the other computer sent it
    from fieldkit.mandate import proofs
    from fieldkit.peers import link
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    k = b"k" * 32
    link.store("message", _signed_note(k, "reviewed 6cff573: 120 passed")["signed"])
    assert proofs.other_machine_reviewed(".", "6cff5731234", {}, link_key=k, my_os="LINUX")[0]


# ---- M3: the callers --------------------------------------------------------------------------------------------------
def test_an_owner_approval_is_granted_only_as_listed(owner):
    M.approve(owner, "correct horse battery", True)
    a = lambda what: M.allows(owner, "approval", today=DAY, what=what)[0]
    assert a("fieldkit build-harness accept firefox-158 S2") == "allow"
    assert a("fieldkit build-harness approve x") == "ask"
    assert a("fieldkit mandate approve build-harness accept") == "ask"          # never the mandate's own key
    assert "as if you approved it: build-harness accept" in "\n".join(M.doors(M.load(owner)))


def test_decide_reads_every_mandate_here(owner):
    assert M.decide("approval", what="build-harness accept x")[0] == "ask"      # not approved yet
    M.approve(owner, "correct horse battery", True)
    v, why, path = M.decide("approval", what="build-harness accept x")
    assert v == "allow" and path == owner and M.in_force() == [owner]


def test_an_install_counts_what_the_disk_measured(owner):
    import subprocess
    M.approve(owner, "correct horse battery", True)
    disk = iter([10e9, 13e9])                                  # declared 1 GB, the disk grew 3 GB
    code, msg = M.install(1, "toolchain", ["true"], run=lambda c: subprocess.CompletedProcess(c, 0),
                          used=lambda: next(disk))
    assert code == 0 and "3.00 GB" in msg
    assert [e["gb"] for e in M.read_log(owner) if e["what"] == "install"] == [3.0]
    code, msg = M.install(2.5, "more", ["true"], run=lambda c: 1 / 0, used=lambda: 0)   # 3 + 2.5 > 5: never runs
    assert code == 3 and "NOT INSIDE" in msg


def _git(cwd, *a):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """origin (bare, named like the mandate's repository) and a clone on feat/x one commit ahead of main."""
    origin = tmp_path / "Gorilla.Opencode.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    for k, v in (("user.email", "tester"), ("user.name", "t"), ("commit.gpgsign", "false"),
                 ("core.hooksPath", "/dev/null")):
        _git(work, "config", k, v)
    (work / "a").write_text("1")
    _git(work, "add", "a")
    _git(work, "commit", "-qm", "one")
    _git(work, "push", "-q", "origin", "HEAD:main")
    _git(work, "switch", "-qc", "feat/x")
    (work / "a").write_text("2")
    _git(work, "commit", "-qam", "two")
    return origin, work


def test_ship_merge_refuses_without_a_mandate(repos):
    from fieldkit.method import ship
    origin, work = repos
    _git(work, "push", "-q", "-u", "origin", "feat/x")
    r = ship.merge(work, mandate=lambda *a, **k: ("ask", "no mandate on this computer", None))
    assert not r["ok"] and r["state"] == "not-mandated"
    assert _git(origin, "rev-parse", "main") != _git(work, "rev-parse", "HEAD")


def test_ship_merge_needs_the_exact_commit_pushed_first(repos):
    from fieldkit.method import ship
    origin, work = repos
    r = ship.merge(work, mandate=lambda *a, **k: ("allow", "x", None))
    assert not r["ok"] and r["state"] == "unpushed"


def test_ship_merge_under_a_mandate_fast_forwards_main_and_logs_it(repos, owner, monkeypatch):
    from fieldkit.mandate import proofs
    from fieldkit.method import ship
    origin, work = repos
    _git(work, "push", "-q", "-u", "origin", "feat/x")
    M.approve(owner, "correct horse battery", True)
    head = _git(work, "rev-parse", "HEAD")
    seen = {}
    monkeypatch.setattr(proofs, "PROVERS", {w: (lambda w: lambda rp, c, cfg: (seen.setdefault(w, c) == head, w))(w)
                                            for w in ("full_tests_pass_here", "other_machine_reviewed")})
    monkeypatch.setattr(M, "verify", lambda p, today=None: (True, "in force"))      # the clock: DAY is in the past soon
    r = ship.merge(work)
    assert r["ok"] and r["state"] == "merged", r
    assert _git(origin, "rev-parse", "main") == head                               # the artefact: main moved
    assert seen == {"full_tests_pass_here": head, "other_machine_reviewed": head}
    assert [e["commit"] for e in M.read_log(owner) if e["what"] == "merge_main"] == [head]


def test_ship_push_logs_a_push_the_mandate_covers(repos, owner):
    from fieldkit.method import ship
    origin, work = repos
    r = ship.push(work, mandate=lambda *a, **k: ("allow", "repos.Gorilla.Opencode.push matches feat/x", owner))
    assert r.get("pushed") and r["mandate"]
    assert [e["branch"] for e in M.read_log(owner) if e["what"] == "push"] == ["feat/x"]
    assert _git(origin, "rev-parse", "feat/x") == _git(work, "rev-parse", "HEAD")


def test_a_run_on_a_granted_mac_is_logged(owner):
    from fieldkit.mac import macssh
    assert macssh._mandate_log_run("air", ["ls"], 0, decide=lambda *a, **k: ("allow", "mac access to air", owner))
    assert not macssh._mandate_log_run("other", ["ls"], 0, decide=lambda *a, **k: ("ask", "no", None))
    assert [e["mac"] for e in M.read_log(owner) if e["what"] == "mac-run"] == ["air"]
