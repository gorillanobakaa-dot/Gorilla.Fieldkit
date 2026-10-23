# Version: 1.0.0 · updated 26-10-23-14-36
"""The other computer's verdict (fieldkit/mandate/verdict.py): its OS runs the suite on the commit and a tool signs a
pass; no model approves (2026-10-23: the Windows session rightly refused to APPROVE another Claude's merge, so every
merge waited for the owner). And the mandate's release door."""
import subprocess

import pytest

from fieldkit.mandate import mandate as M
from fieldkit.mandate import verdict as V


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=tester", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "Gorilla.Opencode.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    (w / "go.mod").write_text("module x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "one")
    _g(w, "push", "-q", "origin", "main")
    _g(w, "checkout", "-q", "-b", "feat/x")
    (w / "a.go").write_text("package x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "two")
    _g(w, "push", "-q", "origin", "feat/x")
    return w, _g(w, "rev-parse", "HEAD")


ALLOW = {"allow_verdict": {"Gorilla.Opencode": ["feat/*"]}}


def _go(here, base):
    """go test's output on the commit (the verdict worktree) and on origin/main (the baseline worktree)."""
    return lambda cmd, cwd, **k: subprocess.CompletedProcess(cmd, 0, base if "mandate-base-" in str(cwd) else here, "")


def test_a_pass_on_this_os_is_a_verdict_and_fewer_tests_is_not(repo):
    w, sha = repo
    two = "ok  \tx/a\t0.1s\nok  \tx/b\t0.2s\n"
    passed, ev = V.verdict(w, sha, name="Gorilla.Opencode", run=_go(two, two), local={}, approvals=ALLOW)
    assert passed, ev
    passed, ev = V.verdict(w, sha, name="Gorilla.Opencode", run=_go("ok  \tx/a\t0.1s\n", two), local={}, approvals=ALLOW)
    assert not passed and "fewer tests" in ev


def test_only_pushed_work_and_a_full_sha_get_a_verdict(repo):
    w, sha = repo
    (w / "b.go").write_text("package x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "local only")
    passed, ev = V.verdict(w, _g(w, "rev-parse", "HEAD"), name="Gorilla.Opencode", run=_go("ok x\n", "ok x\n"), local={}, approvals=ALLOW)
    assert not passed and "on no branch of origin" in ev
    assert not V.verdict(w, sha[:10], name="Gorilla.Opencode", local={}, approvals=ALLOW)[0]


def test_the_suite_is_this_computers_never_the_requesters(repo):
    w, _ = repo
    assert V.test_cfg(w, "Gorilla.Opencode", local={})["cmd"] == "go test ./..."
    mine = {"cmd": "go test -short ./..."}
    assert V.test_cfg(w, "Gorilla.Opencode", local={"mandate": {"tests": {"Gorilla.Opencode": mine}}}) == mine
    assert "cmd" not in V.handle_request.__code__.co_varnames                 # a request carries no command


def test_a_commit_on_a_branch_not_allowed_here_gets_no_verdict(repo):
    w, sha = repo
    passed, ev = V.verdict(w, sha, name="Gorilla.Opencode", run=_go("ok x\n", "ok x\n"), local={},
                           approvals={"allow_verdict": {"Gorilla.Opencode": ["release/*"]}})
    assert not passed and "none of the branches allowed" in ev


@pytest.mark.parametrize("path,text", [
    ("conftest.py", "def pytest_collection_modifyitems(items): items.clear()\n"),
    ("pytest.ini", "[pytest]\naddopts = -k nothing\n"),
    ("main_test.go", "package x\nimport \"testing\"\nfunc TestMain(m *testing.M) {}\n"),
    ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-k nothing'\n"),
])
def test_a_commit_that_changes_how_its_tests_run_needs_a_person(repo, path, text):
    # the Windows review, point 2: a commit controls its own test run, so it could fake its pass and its counts
    w, _ = repo
    (w / path).write_text(text)
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "the tests, differently")
    _g(w, "push", "-q", "origin", "feat/x")
    passed, ev = V.verdict(w, _g(w, "rev-parse", "HEAD"), name="Gorilla.Opencode", run=_go("ok x\n", "ok x\n"),
                           local={}, approvals=ALLOW)
    assert not passed and ev.startswith("NEEDS A PERSON") and path in ev


def test_a_version_bump_in_pyproject_is_not_a_test_change(repo):
    w, _ = repo
    (w / "pyproject.toml").write_text("[project]\nversion = '0.2.0'\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "0.2.0")
    _g(w, "push", "-q", "origin", "feat/x")
    assert V.test_control_changes(w, _g(w, "rev-parse", "HEAD")) == []


def test_needs_a_person_is_never_signed(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "home", lambda: tmp_path / "h")
    sent = []
    V.send("Gorilla.Opencode", "a" * 40, True, "NEEDS A PERSON: conftest.py", call=lambda q: sent.append(q) or {"ok": 1})
    assert "approval" not in sent[0]


def test_a_pass_is_signed_a_fail_never_is(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    monkeypatch.setattr(M, "home", lambda: tmp_path / "h")
    sha = "6cff573" + "0" * 33
    sent = []
    V.send("Gorilla.Opencode", sha, True, "2 passed", call=lambda req: sent.append(req) or {"ok": True})
    V.send("Gorilla.Opencode", sha, False, "fewer tests", call=lambda req: sent.append(req) or {"ok": True})
    raw = bytes.fromhex(M._review_paths()[1].read_text().strip())
    Ed25519PublicKey.from_public_bytes(raw).verify(bytes.fromhex(sent[0]["approval"]["sig"]),
                                                   f"APPROVE Gorilla.Opencode {sha}".encode())
    assert sent[0]["done"] and "approval" not in sent[1] and sent[1]["text"].startswith("VERDICT FAIL")


def test_the_listener_starts_the_verdict_tool_for_its_own_repositories_only(repo):
    w, sha = repo
    started = []
    popen = lambda argv, **k: started.append(argv)
    lock = w.parent / "verdict.lock"
    h = lambda req, approvals=ALLOW: V.handle_request(req, popen=popen, repos=[w], approvals=approvals, lock=lock)
    assert not h({"repo": "Gorilla.Opencode", "commit": "abc"})["ok"]
    assert not h({"repo": "Other", "commit": sha})["ok"]
    assert not h({"repo": "../../etc", "commit": sha})["ok"]
    assert "allow_verdict" in h({"repo": "Gorilla.Opencode", "commit": sha}, approvals={})["error"]   # not allowed
    assert started == []
    r = h({"repo": "Gorilla.Opencode", "commit": sha})
    assert r["ok"] and started[0][-6:] == ["verdict", str(w), sha, "--send", "--lock", str(lock)]
    assert "one at a time" in h({"repo": "Gorilla.Opencode", "commit": sha})["error"] and len(started) == 1


def test_the_link_hands_a_signed_verdict_request_to_the_tool(monkeypatch):
    from fieldkit.peers import link
    k = b"k" * 32
    got = []
    monkeypatch.setattr(V, "handle_request", lambda req, popen=None: got.append(req) or {"ok": True, "started": True})
    monkeypatch.setattr(link, "_log", lambda text: None)
    req = link.sign({"verb": "verdict-request", "repo": "Gorilla.Opencode", "commit": "a" * 40}, k)
    assert link.handle(req, link.Verifier(k))["started"] and got[0]["commit"] == "a" * 40
    assert not link.handle(dict(req, commit="b" * 40), link.Verifier(k))["ok"]          # changed after signing


def test_ship_merge_asks_the_other_computer_once_then_waits(repo, tmp_path):
    from fieldkit.method import ship
    w, sha = repo
    why = "m: not proven: other_machine_reviewed: no signed 'APPROVE Gorilla.Opencode ...' from the other computer"
    asked = []
    call = lambda req: asked.append(req) or {"ok": True}
    kw = dict(mandate=lambda *a, **k: ("ask", why, None), verdict_call=call, verdict_state=tmp_path / "asked.json")
    r = ship.merge(w, **kw)
    assert r["state"] == "waiting-verdict" and "inbox --wait 3600" in r["next"]
    assert asked == [{"verb": "verdict-request", "repo": "Gorilla.Opencode", "commit": sha}]
    assert "asked already" in ship.merge(w, **kw)["verdict_request"] and len(asked) == 1
    bad = dict(kw, mandate=lambda *a, **k: ("ask", why + "; full_tests_pass_here: the suite failed", None))
    assert ship.merge(w, **bad)["state"] == "not-mandated"                     # its own suite first


# ---- the release door ----------------------------------------------------------------------------------------------
def test_the_release_door_names_projects(tmp_path, monkeypatch):
    from datetime import date
    m = {"project": "p", "goal": "g", "expires": "2026-10-30", "repos": {}, "never": [],
         "release": {"cycle": {"gorilla-opencode": ["v0.1.151"]}}}
    monkeypatch.setattr(M, "verify", lambda p, today=None: (True, "in force"))
    monkeypatch.setattr(M, "load", lambda p: m)
    a = lambda **k: M.allows("x.yaml", "release", today=date(2026, 10, 23), **k)[0]
    # the Windows review, point 4: only the exact version the owner wrote
    assert a(project="gorilla-opencode", version="v0.1.151") == "allow"
    assert a(project="gorilla-opencode", version="v0.1.152") == "ask" and a(project="gorilla-opencode") == "ask"
    assert a(project="firefox", version="v0.1.151") == "ask" and a() == "ask"
    m["release"] = {"cycle": ["gorilla-opencode"]}                          # a list names no version: not opened
    assert a(project="gorilla-opencode", version="v0.1.151") == "ask"
    m["release"] = {"cycle": {"gorilla-opencode": ["v0.1.151"]}}
    assert "gorilla-opencode v0.1.151" in "\n".join(M.doors(m))
