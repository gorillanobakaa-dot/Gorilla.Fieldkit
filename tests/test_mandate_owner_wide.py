# Version: 1.0.0 · updated 26-10-25-23-40
"""The owner-wide mandate and the pre-push gate (2026-10-25: "the approval has to come once, at the beginning" and
"has to cover all our projects"). Every test closes a way a push could get past the owner without the owner's
signature: a branch or tag outside the patterns, a base branch, a delete, a moved tag, a rename, a rewrite, an expired,
stopped, edited or foreign-signed mandate, a push that did not come through fieldkit ship push.

The end-to-end test at the bottom pushes a real clone through the real Gorilla.Opencode pre-push hook (the proposed
one, which calls `fieldkit mandate gate` as a separate process) with `fieldkit ship push`, no owner flag."""
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from fieldkit.mandate import gate as G
from fieldkit.mandate import mandate as M

DAY = date(2026, 10, 26)
GOOD = {
    "project": "owner-wide", "scope": "owner-wide", "goal": "one approval for every repository",
    "expires": "2099-01-01",
    "defaults": {"push": ["feat/*", "fix/*", "release-*", "night/*"], "tags": ["v*"],
                 "merge_to_main": {"when": ["ci_green"], "how": "pull-request"}, "release": "from_tag"},
    "repos": {"Gorilla.Opencode": {}, "pfind": {"push": ["*"]}, "debian-kernel": {"kernel": True},
              "no-tags": {"tags": []}},
    "install": {"budget_gb": 5},
    "never": ["force_push"],
}


def _store(monkeypatch, root):
    from fieldkit.mandate import guarded
    monkeypatch.setattr(guarded, "root", lambda: root)
    monkeypatch.setattr(guarded, "guarded", lambda path=None: (True, "a test store"))

    def write(name, line, append=False, run=None):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(line + "\n")
        return True, f"written: {p}"
    monkeypatch.setattr(guarded, "write", write)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A test owner: key made and trusted, the mandate store at tmp_path/home (mandates/ inside)."""
    _store(monkeypatch, tmp_path / "guarded")
    monkeypatch.setattr(M, "home", lambda: tmp_path / "home")
    pub, _ = M.keygen("correct horse battery")
    assert M.trust_raw(bytes.fromhex(pub.read_text().strip()))[0]
    (tmp_path / "home" / "mandates").mkdir(parents=True)
    return tmp_path / "home"


def write_mandate(home, data=None, name="owner-wide", approve=True):
    f = home / "mandates" / f"{name}.yaml"
    f.write_text(yaml.safe_dump(data or GOOD, sort_keys=False), encoding="utf-8")
    if approve:
        assert M.approve(f, "correct horse battery", True)[0] == 0
    return f


# ---- the approve screen ------------------------------------------------------------------------------------------------
def test_the_proposed_mandate_names_every_repository_and_reads_as_doors():
    m = M.load(Path(M.__file__).with_name("owner-wide.yaml"))
    assert M.owner_wide(m) and len(m["repos"]) >= 20
    text = "\n".join(M.doors(m))
    for repo in ("Gorilla.Opencode", "Gorilla.Fieldkit", "gorilla-firefox", "debian-kernel", "TPFanCtrl2",
                 "Gorilla.Portable.Network.Hub", "kernel-7.0.9-unleashed"):
        assert f"branches of {repo}:" in text
    assert "NEVER install or boot" in text and "INSTALL software" not in text      # budget 0: no install door shown


def test_the_approve_screen_shows_what_is_not_covered_and_the_threat_model(home, capsys, monkeypatch):
    f = write_mandate(home, approve=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert M.main(["approve", str(f)]) == 3                                         # no terminal: never approved
    out = capsys.readouterr().out
    for must in ("NOT COVERED", "decision-register", "leak-gate", "force-push", "deleting a branch or a tag",
                 "permanent deletes", "third-party tool", "UAC", "booting a kernel", "guards, the git hooks",
                 "THREAT MODEL", "WHO CAN ACT", "KEY STORAGE", "WHAT IS LOGGED", "HOW TO STOP IT"):
        assert must in out, must
    assert not f.with_suffix(".seal").exists()


# ---- what the mandate allows ---------------------------------------------------------------------------------------------
class Facts:
    def __init__(self, repo="Gorilla.Opencode", branch="feat/x", ff=True, tag_new=(True, "new"), on_origin=None,
                 green=True):
        self.repo, self.branch, self.ff, self.tag_new, self.green = repo, branch, ff, tag_new, green
        self.on_origin = on_origin or (True, f"v1 on origin at {'a' * 40}")
        self.PROVERS = {"ci_green": lambda rp, c, cfg: (self.green, "green" if self.green else "CI not green")}

    def where(self, rp):
        return self.repo, self.branch

    def fast_forward(self, rp, b):
        return self.ff, "ff"

    def tag_new_on_pushed_commit(self, rp, t):
        return self.tag_new

    def tag_on_origin(self, rp, t):
        return self.on_origin


@pytest.mark.parametrize("branch,verdict", [("feat/a", "allow"), ("release-v1", "allow"), ("night/n1", "allow"),
                                            ("fix/b", "allow"), ("main", "ask"), ("windows-port", "ask"),
                                            ("topic/x", "ask"), ("feat/../main", "ask")])
def test_push_patterns(home, branch, verdict):
    f = write_mandate(home)
    assert M.allows(f, "push", DAY, repo_path=".", provers=Facts(branch=branch))[0] == verdict


def test_a_base_branch_is_never_pushed_even_when_a_pattern_says_star(home):
    f = write_mandate(home)
    assert M.allows(f, "push", DAY, repo_path=".", provers=Facts(repo="pfind", branch="main"))[0] == "ask"
    assert M.allows(f, "push", DAY, repo_path=".", provers=Facts(repo="pfind", branch="anything"))[0] == "allow"


def test_a_repository_the_mandate_does_not_name_is_the_owners(home):
    f = write_mandate(home)
    assert M.allows(f, "push", DAY, repo_path=".", provers=Facts(repo="someone-else"))[0] == "ask"


def test_tags(home):
    f = write_mandate(home)
    assert M.allows(f, "tag", DAY, repo_path=".", tag="v1.2.3", provers=Facts())[0] == "allow"
    assert M.allows(f, "tag", DAY, repo_path=".", tag="nightly", provers=Facts())[0] == "ask"
    assert M.allows(f, "tag", DAY, repo_path=".", tag="v1", provers=Facts(tag_new=(False, "exists")))[0] == "ask"
    assert M.allows(f, "tag", DAY, repo_path=".", tag="v1", provers=Facts(repo="no-tags"))[0] == "ask"


def test_a_release_needs_from_tag_the_tag_on_origin_and_green_ci(home):
    f = write_mandate(home)
    assert M.allows(f, "release_tag", DAY, repo_path=".", tag="v1", provers=Facts())[0] == "allow"
    assert M.allows(f, "release_tag", DAY, repo_path=".", tag="v1", provers=Facts(green=False))[0] == "ask"
    assert M.allows(f, "release_tag", DAY, repo_path=".", tag="v1",
                    provers=Facts(on_origin=(False, "not on origin")))[0] == "ask"
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["defaults"]["release"] = "ask"
    g = write_mandate(home, data, name="no-release")
    assert M.allows(g, "release_tag", DAY, repo_path=".", tag="v1", provers=Facts())[0] == "ask"


@pytest.mark.parametrize("what,cmd", [("kernel", "sudo dpkg -i linux-image-7.0.9-unleashed_amd64.deb"),
                                      ("linux-headers-7.0.9", "apt install ./x.deb"),
                                      ("grub", "sudo update-grub"), ("boot it", "sudo kexec -l vmlinuz"),
                                      ("restart", "sudo systemctl reboot")])
def test_a_kernel_is_never_installed_or_booted_under_a_mandate(home, what, cmd):
    write_mandate(home)
    assert M.decide("install", DAY, gb=0.1, what=what, cmd=cmd)[0] == "ask"
    assert M.decide("install", DAY, gb=0.1, what="ffmpeg", cmd="sudo apt-get install ffmpeg")[0] == "allow"


# ---- the pre-push gate, on real git repositories ---------------------------------------------------------------------------
def git(repo, *a, env=None, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=env)
    if check and r.returncode:
        raise AssertionError(f"git {' '.join(a)}: {r.stderr}")
    return r


@pytest.fixture
def clone(tmp_path):
    """origin = a bare repository named Gorilla.Opencode.git; a clone with main pushed and a feature branch."""
    origin = tmp_path / "remote" / "Gorilla.Opencode.git"
    origin.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    c = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(c)], check=True, capture_output=True)
    git(c, "config", "user.email", "t" + "@" + "localhost")             # built: the privacy scan reads source
    git(c, "config", "user.name", "t")
    git(c, "checkout", "-q", "-b", "main")
    (c / "a.txt").write_text("a\n")
    git(c, "add", "a.txt")
    git(c, "commit", "-q", "-m", "a")
    git(c, "push", "-q", "origin", "main")
    git(c, "checkout", "-q", "-b", "feat/x")
    (c / "b.txt").write_text("b\n")
    git(c, "add", "b.txt")
    git(c, "commit", "-q", "-m", "b")
    return c


def sha(repo, ref="HEAD"):
    return git(repo, "rev-parse", ref).stdout.strip()


Z = "0" * 40
SHIP = {"FIELDKIT_SHIP_PUSH": "1"}


def line(local_ref, local_sha, remote_ref, remote_sha=Z):
    return f"{local_ref} {local_sha} {remote_ref} {remote_sha}\n"


def test_gate_allows_a_mandated_branch_and_logs_it(home, clone):
    f = write_mandate(home)
    ok, why, p = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert ok, why
    assert any(e["what"] == "push-gate" for e in M.read_log(f))


def test_gate_refuses_a_push_that_did_not_come_through_ship(home, clone):
    write_mandate(home)
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env={}, today=DAY)
    assert not ok and "fieldkit ship push" in why[0]


@pytest.mark.parametrize("stdin,needle", [
    (lambda c: line("refs/heads/topic", sha(c), "refs/heads/topic"), "not a branch the mandate"),
    (lambda c: line("refs/heads/main", sha(c), "refs/heads/main", sha(c, "main")), "base branch"),
    (lambda c: line("(delete)", Z, "refs/heads/feat/old", sha(c, "main")), "deleting"),
    (lambda c: line("refs/heads/feat/x", sha(c), "refs/heads/feat/y"), "another name"),
    (lambda c: line("refs/heads/feat/x", sha(c, "main"), "refs/heads/feat/x", sha(c)), "not a fast-forward"),
    (lambda c: line("refs/notes/x", sha(c), "refs/notes/x"), "neither a branch nor a tag"),
    (lambda c: line("refs/tags/nightly", sha(c, "main"), "refs/tags/nightly"), "not a tag the mandate"),
    (lambda c: line("refs/tags/v1", sha(c, "main"), "refs/tags/v1", sha(c)), "never moved"),
    (lambda c: line("refs/tags/v1", sha(c), "refs/tags/v1"), "on no branch of origin"),
    (lambda c: "garbage\n", "4 fields"),
])
def test_gate_refuses_everything_outside_the_mandate(home, clone, stdin, needle):
    write_mandate(home)
    ok, why, _ = G.check(clone, stdin(clone), env=SHIP, today=DAY)
    assert not ok and any(needle in w for w in why), why


def test_gate_allows_a_new_tag_on_a_pushed_commit_or_with_its_branch(home, clone):
    write_mandate(home)
    assert G.check(clone, line("refs/tags/v1", sha(clone, "main"), "refs/tags/v1"), env=SHIP, today=DAY)[0]
    both = line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x") + line("refs/tags/v2", sha(clone), "refs/tags/v2")
    assert G.check(clone, both, env=SHIP, today=DAY)[0]


def test_gate_refuses_an_expired_mandate(home, clone):
    write_mandate(home, dict(GOOD, expires="2026-10-25"))
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert not ok and "expired" in why[0]


def test_gate_refuses_a_stopped_mandate(home, clone):
    f = write_mandate(home)
    M.stop(f)
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert not ok and "STOPPED" in why[0]


def test_gate_refuses_a_mandate_edited_after_its_seal(home, clone):
    f = write_mandate(home)
    f.write_text(f.read_text().replace("- feat/*", "- '*'"), encoding="utf-8")  # widen it after the owner signed
    ok, why, _ = G.check(clone, line("refs/heads/topic", sha(clone), "refs/heads/topic"), env=SHIP, today=DAY)
    assert not ok and "changed after its approval" in why[0]


def test_gate_refuses_a_mandate_sealed_with_a_key_nobody_trusted(home, clone, tmp_path, monkeypatch):
    f = write_mandate(home, approve=False)
    monkeypatch.setattr(M, "home", lambda: tmp_path / "forger")            # a second key, never pinned
    M.keygen("another long passphrase")
    assert M.approve(f, "another long passphrase", True)[0] == 0
    monkeypatch.setattr(M, "home", lambda: home)
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert not ok and "not trusted" in why[0]


def test_gate_refuses_a_forged_signature(home, clone):
    import json
    f = write_mandate(home)
    seal = json.loads(f.with_suffix(".seal").read_text())
    seal["sig"] = ("0" if seal["sig"][0] != "0" else "1") + seal["sig"][1:]
    f.with_suffix(".seal").write_text(json.dumps(seal))
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert not ok and "signature is not valid" in why[0]


def test_gate_with_no_mandate_at_all_refuses(home, clone):
    ok, why, _ = G.check(clone, line("refs/heads/feat/x", sha(clone), "refs/heads/feat/x"), env=SHIP, today=DAY)
    assert not ok and "no mandate in force" in why[0]


def test_the_gate_command_fails_closed_when_it_breaks(monkeypatch, capsys):
    monkeypatch.setattr(G, "check", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(""))
    assert G.main(["--repo", "."]) == 3
    assert "the gate failed" in capsys.readouterr().err


# ---- end to end: the real Gorilla.Opencode hook, the real fieldkit ship push, a separate gate process -------------------
# 2026-10-26: the template lives in Fieldkit (fieldkit/mandate/hooks/gorilla-opencode), so this runs everywhere
HOOKS = [Path(os.environ["FK_OPENCODE_HOOKS"])] if os.environ.get("FK_OPENCODE_HOOKS") else [
    Path(M.__file__).with_name("hooks") / "gorilla-opencode",
    Path.home() / "Documents" / "Gorilla.Opencode" / "scripts" / "git-hooks"]
HOOKDIR = next((h for h in HOOKS if (h / "hooks.py").is_file() and "mandate_gate" in (h / "hooks.py").read_text()), None)
SITE = """import os
if os.environ.get("FK_E2E_HOME"):
    from pathlib import Path
    from fieldkit.mandate import guarded as G, mandate as M
    M.home = lambda: Path(os.environ["FK_E2E_HOME"])
    G.root = lambda: Path(os.environ["FK_E2E_STORE"])
    G.guarded = lambda path=None: (True, "the e2e test store")
    # this test is about the hook and the mandate; the tag guard (no notes, no CI here) has its own tests/test_tag_guard.py
    from fieldkit.method import tagguard
    tagguard.check = lambda repo, tag, **kw: {"ok": True, "rows": []}
"""


@pytest.mark.skipif(HOOKDIR is None or shutil.which("sh") is None,
                    reason="no sh to run the Gorilla.Opencode hook with")
def test_end_to_end_a_real_push_through_ship_and_the_opencode_hook(home, clone, tmp_path):
    hooks = clone / "scripts" / "git-hooks"
    hooks.mkdir(parents=True)
    shutil.copy(HOOKDIR / "hooks.py", hooks / "hooks.py")
    shutil.copy(HOOKDIR / "pre-push", clone / ".git" / "hooks" / "pre-push")    # keeps the template's own mode
    # git on Linux and macOS skips a hook that is not executable, in silence (2026-10-26: Ubuntu CI pushed through)
    assert sys.platform == "win32" or os.access(clone / ".git" / "hooks" / "pre-push", os.X_OK), \
        "the template pre-push is not executable: git update-index --chmod=+x it"
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(SITE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("GORILLA_OWNER_APPROVED", "FIELDKIT_SHIP_PUSH")}
    env.update(PYTHONPATH=os.pathsep.join([str(site), str(Path(M.__file__).parents[2])]),
               FK_E2E_HOME=str(home), FK_E2E_STORE=str(tmp_path / "guarded"))
    from fieldkit.mandate import guarded
    assert guarded.root() == tmp_path / "guarded"

    def ship(*a):
        return subprocess.run([sys.executable, "-m", "fieldkit", "ship", *a, "--repo", str(clone)],
                              capture_output=True, text=True, env=env, cwd=str(tmp_path))

    # no mandate: the hook wants the owner's flag
    r = ship("push")
    assert r.returncode == 3 and "GORILLA_OWNER_APPROVED" in r.stdout + r.stderr, r.stdout + r.stderr
    assert git(clone, "ls-remote", "origin", "refs/heads/feat/x").stdout == ""
    # a plain git push with a mandate in force is still refused (not through ship)
    f = write_mandate(home)
    assert git(clone, "push", "origin", "feat/x", env=env, check=False).returncode != 0
    # through ship: pushed, no flag
    r = ship("push")
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(clone, "ls-remote", "origin", "refs/heads/feat/x").stdout.split()[0] == sha(clone)
    assert any(e["what"] == "push-gate" for e in M.read_log(f))
    # a new tag on that pushed commit, through ship
    git(clone, "tag", "-a", "v9.9.9", "-m", "t")
    r = ship("push", "--tag", "v9.9.9")
    assert r.returncode == 0, r.stdout + r.stderr
    # a branch outside the patterns: refused by the hook
    git(clone, "checkout", "-q", "-b", "topic/y")
    r = ship("push")
    assert r.returncode == 3 and "not a branch the mandate" in r.stdout + r.stderr
    # the mandate edited after its seal: refused
    git(clone, "checkout", "-q", "feat/x")
    (clone / "c.txt").write_text("c\n")
    git(clone, "add", "c.txt")
    git(clone, "commit", "-q", "-m", "c")
    f.write_text(f.read_text().replace("2099-01-01", "2099-01-02"), encoding="utf-8")
    r = ship("push")
    assert r.returncode == 3 and "changed after its approval" in r.stdout + r.stderr
    # approved again, then stopped: refused
    assert M.approve(f, "correct horse battery", True)[0] == 0
    M.stop(f)
    r = ship("push")
    assert r.returncode == 3 and "STOPPED" in r.stdout + r.stderr
    # expired: refused
    g = write_mandate(home, dict(GOOD, expires="2026-10-01"), name="old")
    f.unlink()
    r = ship("push")
    assert r.returncode == 3 and "expired" in r.stdout + r.stderr, r.stdout + r.stderr
    # the owner's flag still works, mandate or not
    env["GORILLA_OWNER_APPROVED"] = "1"
    assert git(clone, "push", "-q", "origin", "feat/x", env=env, check=False).returncode == 0
    assert g.exists()


def test_a_hook_refusal_is_not_retried_as_a_network_error():
    from fieldkit.method import ship
    assert ship.refused_not_network("  PRE-PUSH REFUSED\n  - not a branch\nerror: failed to push some refs to 'x'")
    assert ship.refused_not_network("error: failed to push some refs to 'origin'")
    assert not ship.refused_not_network("fatal: unable to access 'https://github.com/x/': Could not resolve host")


class Run:
    def __init__(self, rc=0, out="https://github.com/o/r/releases/tag/v1"):
        self.rc, self.out, self.calls = rc, out, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.rc, self.out, "")


def test_gh_release_publishes_only_what_the_mandate_allows(monkeypatch, tmp_path):
    from fieldkit.core import sync
    from fieldkit.method import ship
    monkeypatch.setattr(sync, "_slug", lambda repo: "o/r")
    run = Run()
    r = ship.gh_release(tmp_path, "v1", ["--title", "v1"], mandate=lambda a, **k: ("ask", "CI not green", None), run=run)
    assert not r["ok"] and run.calls == []
    logged = []
    monkeypatch.setattr(ship, "_mandate_log", lambda p, what, why, **e: logged.append(what))
    r = ship.gh_release(tmp_path, "v1", ["--title", "v1"], mandate=lambda a, **k: ("allow", "ok", "m.yaml"), run=run)
    assert r["ok"] and logged == ["release"]
    assert run.calls[-1][:6] == ["gh", "release", "create", "v1", "--verify-tag", "-R"] and run.calls[-1][6] == "o/r"


@pytest.mark.parametrize("bad", [["--target", "main"], ["--target=x"], ["-R", "other/repo"], ["--repo=o/x"]])
def test_gh_release_never_takes_the_commit_or_repository_from_the_caller(tmp_path, bad):
    from fieldkit.method import ship
    run = Run()
    r = ship.gh_release(tmp_path, "v1", bad, mandate=lambda a, **k: ("allow", "ok", "m.yaml"), run=run)
    assert not r["ok"] and r["state"] == "bad-input" and run.calls == []


def test_a_tag_push_the_mandate_does_not_allow_pushes_nothing(clone):
    from fieldkit.method import ship
    git(clone, "tag", "-a", "v5", "-m", "t")
    r = ship.push_tag(clone, "v5", mandate=lambda a, **k: ("ask", "not a tag the mandate lets sessions push", None))
    assert not r["ok"] and git(clone, "ls-remote", "origin", "refs/tags/v5").stdout == ""
    assert not ship.push_tag(clone, "v-missing")["ok"]
