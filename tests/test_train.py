"""The merge train (fieldkit ship ready / train / update / proof): a local bare repository plays GitHub.

The reproduction of 2026-10-26: PR A merges, PR B becomes conflicting ONLY in a generated file, the train merges main
into B here, regenerates the file, the pre-commit hook proves it is only a merge (no full suite), and B merges; a PR
whose session announced a pending commit is never merged.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.method import mergeproof as mp
from fieldkit.method import ship, train

ROOT = Path(__file__).resolve().parents[1]
GEN = """import hashlib, json, pathlib
cards = sorted(p.name for p in pathlib.Path("cards").glob("*.txt"))
print(json.dumps({"cards": cards, "digest": hashlib.sha256("".join(cards).encode()).hexdigest()[:12]}, indent=1))
"""
GEN_YAML = "generated:\n  index.json: ['{python}', gen.py]\n"
HOOK = """#!/bin/sh
if [ -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
  if "$FK_PY" -m fieldkit.method.mergeproof hook --key "$(git write-tree)" >/dev/null 2>>"$FK_HOOKLOG.why"; then
    echo proof >> "$FK_HOOKLOG"
  else
    echo full >> "$FK_HOOKLOG"
  fi
fi
exit 0
"""


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"git {' '.join(a)}: {r.stderr}")
    return r.stdout.strip()


def regen(repo):
    out = subprocess.run([sys.executable, "gen.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    Path(repo, "index.json").write_text(out, encoding="utf-8", newline="\n")


def add_card(repo, name, msg=None):
    Path(repo, "cards", name).write_text(name + "\n", encoding="utf-8")
    regen(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg or f"card {name}")
    return git(repo, "rev-parse", "HEAD")


class FakeGH:
    """GitHub as far as the train uses it, on a bare repository: PRs, comments, labels, update-branch, checks, merge."""

    def __init__(self, bare, srv):
        self.bare, self.srv = bare, srv
        subprocess.run(["git", "clone", "-q", str(bare), str(srv)], check=True)
        self._prs, self.comments, self.ci, self.merges, self.updates = {}, {}, {}, [], []
        self.crash_on_ci = set()

    def head(self, branch):
        out = git(self.bare, "rev-parse", f"refs/heads/{branch}", check=False)
        return out or None

    def add(self, n, branch, base="main", updated="2026-10-26T18:00:00Z"):
        self._prs[n] = {"number": n, "title": branch, "headRefName": branch, "baseRefName": base, "labels": set(),
                        "isDraft": False, "updatedAt": updated, "state": "OPEN"}
        self.comments[n] = []

    def pr(self, n):
        d = dict(self._prs[n])
        d["headRefOid"] = self.head(d["headRefName"])
        d["labels"] = [{"name": x} for x in sorted(d["labels"])]
        return d

    def prs(self):
        return [self.pr(n) for n in sorted(self._prs) if self._prs[n]["state"] == "OPEN"]

    def markers(self, n):
        return train.parse_markers(self.comments[n])

    def comment(self, n, body):
        self.comments[n].append(body)
        return True, ""

    def label(self, n, add=True):
        (self._prs[n]["labels"].add if add else self._prs[n]["labels"].discard)(train.LABEL)
        return True, ""

    def _srv_merge(self, into, other):
        git(self.srv, "fetch", "-q", "origin")
        git(self.srv, "checkout", "-q", "-B", into, f"origin/{into}")
        r = subprocess.run(["git", "-C", str(self.srv), "merge", "--no-ff", "--no-edit", f"origin/{other}"],
                           capture_output=True, text=True)
        if r.returncode:
            git(self.srv, "merge", "--abort", check=False)
            return False
        git(self.srv, "push", "-q", "origin", into)
        return True

    def update_branch(self, n, head):
        br = self._prs[n]["headRefName"]
        if self.head(br) != head:
            return False, "expected_head_sha does not match"
        self.updates.append(n)
        return (True, "updated") if self._srv_merge(br, "main") else (False, "merge conflict")

    def ci_state(self, sha):
        if sha in self.crash_on_ci:
            self.crash_on_ci.discard(sha)
            raise KeyboardInterrupt("the train crashed while waiting for CI")
        return self.ci.get(sha, "green"), f"fake CI {self.ci.get(sha, 'green')} for {sha[:10]}"

    def wait_checks(self, n, timeout):
        pass

    def merge(self, n, head):
        br = self._prs[n]["headRefName"]
        if self.head(br) != head:
            return False, "Head branch was modified. Review and try the merge again."
        if not self._srv_merge("main", br):
            return False, "conflict"
        self._prs[n]["state"] = "MERGED"
        self.merges.append(n)
        return True, "merged"

    def files(self, n):
        git(self.srv, "fetch", "-q", "origin")
        return git(self.srv, "diff", "--name-only", f"origin/main...origin/{self._prs[n]['headRefName']}").split()

    def retarget(self, n, base):
        self._prs[n]["baseRefName"] = base
        return True, ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "fieldkit-test", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "fieldkit-test", "FIELDKIT_TESTCOST_DIR": str(tmp_path / "testcost"),
                 "FK_PY": sys.executable, "FK_HOOKLOG": str(tmp_path / "hook.log"),
                 "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "cards").mkdir()
    for c in ("a.txt", "m.txt"):
        (seed / "cards" / c).write_text(c + "\n", encoding="utf-8")
    (seed / "gen.py").write_text(GEN, encoding="utf-8")
    (seed / "fieldkit" / "method").mkdir(parents=True)
    (seed / "fieldkit" / "method" / "generated.yaml").write_text(GEN_YAML, encoding="utf-8")
    (seed / "hooks").mkdir()
    (seed / "hooks" / "pre-commit").write_text(HOOK, encoding="utf-8", newline="\n")
    os.chmod(seed / "hooks" / "pre-commit", 0o755)
    regen(seed)
    git(seed, "add", "-A")
    git(seed, "update-index", "--chmod=+x", "hooks/pre-commit")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "-u", "origin", "main")

    def clone(name):
        d = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(bare), str(d)], check=True)
        git(d, "config", "core.hooksPath", "hooks")
        return d

    gh = FakeGH(bare, tmp_path / "github")
    return {"tmp": tmp_path, "bare": bare, "clone": clone, "gh": gh, "log": tmp_path / "hook.log"}


def session_pr(w, n, branch, card, base="main"):
    d = w["clone"](f"s{n}")
    git(d, "checkout", "-q", "-b", branch, f"origin/{base}")
    add_card(d, card)
    git(d, "push", "-q", "-u", "origin", branch)
    w["gh"].add(n, branch, base)
    return d


def run_train(w, zero, **kw):
    def push(work, base):
        return ship.push(work, base, mandate=lambda *a, **k: ("ask", "test", None))
    kw.setdefault("ci", lambda repo, sha: (True, "fake green"))
    return train.train(zero, gh=w["gh"], mandate=lambda *a, **k: ("allow", "test mandate", None),
                       may_update=lambda repo, branch: (True, "test"), push=push, stations=[], sleep=lambda s: None,
                       **kw)


# -- the reproduction --------------------------------------------------------------------------------------------------
def test_reproduction_generated_conflict_merged_without_full_suite(world):
    w, gh = world, world["gh"]
    a = session_pr(w, 1, "feat/a", "b.txt")
    b = session_pr(w, 2, "feat/b", "y.txt")
    c = session_pr(w, 3, "feat/c", "q.txt")
    for n, d in ((1, a), (2, b), (3, c)):
        assert train.ready(d, n, gh)["state"] == "declared"
    assert train.ready(c, 3, gh, pending="one more commit: a test-isolation guard")["state"] == "not-final"
    zero = w["clone"]("zero")
    r = run_train(w, zero)
    assert gh.merges == [1, 2], r
    assert [x["pr"] for x in r["left"]] == [3] and "pending" in r["left"][0]["why"]
    # B conflicted only in the generated index: merged here, regenerated, pushed, then merged on green
    log = [x for x in train.load_state(zero)["log"] if x["pr"] == 2]
    assert any(x["what"] == "update-local" and x["state"] == "merged" for x in log), log
    assert w["log"].read_text().split() == ["proof"], Path(str(w["log"]) + ".why").read_text()
    git(zero, "fetch", "-q", "origin")
    git(zero, "checkout", "-q", "--detach", "origin/main")
    idx = json.loads((zero / "index.json").read_text())
    assert idx["cards"] == ["a.txt", "b.txt", "m.txt", "y.txt"]
    assert mp.gen_matches(zero, "index.json", [sys.executable, "gen.py"])[0]


def test_github_shows_the_conflict_the_train_resolves(world):
    w, gh = world, world["gh"]
    session_pr(w, 1, "feat/a", "b.txt")
    session_pr(w, 2, "feat/b", "y.txt")
    srv = w["tmp"] / "github"
    gh._srv_merge("main", "feat/a")
    head = gh.head("feat/b")
    assert gh.update_branch(2, head) == (False, "merge conflict")      # what GitHub said about #124
    zero = w["clone"]("zero")
    git(zero, "fetch", "-q", "origin")
    assert train.predict(zero, head) == ("update-local", ["index.json"])
    assert srv.is_dir()


def test_session_update_hook_skips_only_a_proven_merge(world):
    w = world
    a = session_pr(w, 1, "feat/a", "b.txt")
    b = session_pr(w, 2, "feat/b", "y.txt")
    w["gh"]._srv_merge("main", "feat/a")
    r = mp.update(b, ci=lambda repo, sha: (True, "fake green"))
    assert r["state"] == "merged" and r["regenerated"] == ["index.json"], r
    assert "1 generated" in r["proof"] and r["skip_ok"]
    assert w["log"].read_text().split() == ["proof"]
    # the same merge with a hand edit is NOT only a merge: the hook asks for the full suite
    git(b, "reset", "-q", "--hard", "HEAD~1")
    git(b, "merge", "-q", "--no-commit", "origin/main", check=False)
    regen(b)
    Path(b, "cards", "a.txt").write_text("edited by hand\n", encoding="utf-8")
    git(b, "add", "-A")
    skip, why, _ = mp.hook_decide(b, key=git(b, "write-tree"))
    assert not skip and "cards/a.txt (hand-edit)" in why
    assert a.is_dir()


def test_hook_full_suite_without_ci_record_for_main(world):
    w = world
    session_pr(w, 1, "feat/a", "b.txt")
    b = session_pr(w, 2, "feat/b", "y.txt")
    w["gh"]._srv_merge("main", "feat/a")
    ours = git(b, "rev-parse", "HEAD")
    r = mp.update(b, ci=lambda repo, sha: (sha == ours, "only our side is green"))
    assert r["state"] == "merged"
    assert w["log"].read_text().split() == ["full"]
    assert "no CI record" in Path(str(w["log"]) + ".why").read_text()


def test_hook_full_suite_when_a_generated_file_drifts(world):
    w = world
    session_pr(w, 1, "feat/a", "b.txt")
    b = session_pr(w, 2, "feat/b", "y.txt")
    w["gh"]._srv_merge("main", "feat/a")
    git(b, "fetch", "-q", "origin")
    for sha in (git(b, "rev-parse", "HEAD"), git(b, "rev-parse", "origin/main")):
        mp.ci_record(b, sha, lambda repo, s: (True, "fake green"))
    git(b, "merge", "-q", "--no-commit", "origin/main", check=False)
    git(b, "checkout", "--ours", "--", "index.json")            # the stale index kept by hand: not its generator
    git(b, "add", "-A")
    skip, why, _ = mp.hook_decide(b, key=git(b, "write-tree"))
    assert not skip and "index.json (generated-drift)" in why


# -- the declaration ---------------------------------------------------------------------------------------------------
def test_ready_refused_while_a_commit_is_in_the_hook(world):
    w, gh = world, world["gh"]
    d = session_pr(w, 1, "feat/a", "b.txt")
    marker = mp.common_dir(d) / "pre-commit.running.4242"
    marker.write_text("2026-10-26 18:50:00 " + git(d, "rev-parse", "--show-toplevel"))
    r = train.ready(d, 1, gh)
    assert r["state"] == "not-final" and "pre-commit hook" in r["error"]
    marker.write_text("2026-10-26 18:50:00 C:/another/worktree")     # another worktree's hook does not count
    assert train.hook_running(d) == []
    marker.unlink()
    add_card(d, "z.txt")                                    # committed but not pushed
    r = train.ready(d, 1, gh)
    assert r["state"] == "not-final" and "push first" in r["error"]
    assert gh.comments[1] == []


def test_head_moved_after_ready_is_never_merged(world):
    w, gh = world, world["gh"]
    d = session_pr(w, 1, "feat/a", "b.txt")
    train.ready(d, 1, gh)
    add_card(d, "late.txt")
    git(d, "push", "-q", "origin", "feat/a")
    zero = w["clone"]("zero")
    r = run_train(w, zero)
    assert gh.merges == [] and "head moved after the declaration" in r["left"][0]["why"]
    ok, _, why = train.readiness(gh.pr(1), gh.markers(1))
    assert not ok and "declares again" in why


def test_fake_gh_refuses_a_head_mismatch(world):
    w, gh = world, world["gh"]
    session_pr(w, 1, "feat/a", "b.txt")
    assert gh.merge(1, "0" * 40)[0] is False


def test_never_merged_red(world):
    w, gh = world, world["gh"]
    d = session_pr(w, 1, "feat/a", "b.txt")
    train.ready(d, 1, gh)
    gh.ci[gh.head("feat/a")] = "red"
    r = run_train(w, w["clone"]("zero"))
    assert gh.merges == [] and "never merged red" in r["left"][0]["why"]


# -- order and resume --------------------------------------------------------------------------------------------------
def test_dependencies_first(world):
    w, gh = world, world["gh"]
    d1 = session_pr(w, 7, "feat/zero-net", "n.txt")
    d2 = w["clone"]("s5")
    git(d2, "checkout", "-q", "-b", "feat/zero-set2", "origin/feat/zero-net")
    add_card(d2, "s2.txt")
    git(d2, "push", "-q", "-u", "origin", "feat/zero-set2")
    gh.add(5, "feat/zero-set2", base="feat/zero-net")
    zero = w["clone"]("zero")
    rows = train.plan(zero, gh, stations=[])
    assert [r["pr"] for r in rows] == [7, 5] and rows[1]["after"] == [7]
    for n, d in ((7, d1), (5, d2)):
        train.ready(d, n, gh)
    r = run_train(w, zero)
    assert gh.merges == [7, 5], r
    assert gh._prs[5]["baseRefName"] == "main"


def test_resume_after_a_crash(world):
    w, gh = world, world["gh"]
    a = session_pr(w, 1, "feat/a", "b.txt")
    b = session_pr(w, 2, "feat/b", "y.txt")
    train.ready(a, 1, gh)
    train.ready(b, 2, gh)
    zero = w["clone"]("zero")
    real = gh.ci_state
    calls = {"n": 0}

    def crash_on_b(sha):
        if sha not in (gh.head("feat/a"),) and calls["n"] == 0:
            calls["n"] += 1
            raise KeyboardInterrupt("power cut while waiting for #2's CI")
        return real(sha)
    gh.ci_state = crash_on_b
    with pytest.raises(KeyboardInterrupt):
        run_train(w, zero)
    st = train.load_state(zero)
    assert st["merged"] == [1] and st["made"]["2"], st
    r = run_train(w, zero)                                   # the same command again picks up where it stopped
    assert gh.merges == [1, 2] and r["state"] == "done", r


# -- the other failure modes -------------------------------------------------------------------------------------------
def test_overlap_and_stale_and_orphan(world):
    w, gh = world, world["gh"]
    session_pr(w, 1, "feat/a", "b.txt")
    me = session_pr(w, 2, "feat/b", "b.txt")             # the same card file in parallel
    hits = train.overlap(me, gh, stations=[("gemini:main", {"shared": "cards/b.txt", "branch": "feat/x"})])
    assert {h["who"] for h in hits} == {"PR #1 (feat/a)", "station gemini:main"}
    pr = gh.pr(1)
    flags = train.survey_flags(pr, False, [("claude:x", {"branch": "feat/other"})],
                               now=train.time.mktime(train.time.strptime("2026-10-28", "%Y-%m-%d")))
    assert any(f.startswith("stale") for f in flags) and any(f.startswith("orphan") for f in flags)
    assert train.survey_flags(pr, True, [], now=0) == []


def test_frozen_checkout_gets_ff_pending_then_baseline(world):
    w, gh = world, world["gh"]
    d = session_pr(w, 1, "feat/a", "b.txt")
    train.ready(d, 1, gh)
    zero = w["clone"]("zero")
    main_co = w["clone"]("main-checkout")
    train.freeze(zero, "elevated goal windows-capabilities", checkout=main_co)
    r = run_train(w, zero)
    assert r["ff_pending"] and r["ff_pending"][0]["checkout"] == str(main_co)
    ran = []
    u = train.unfreeze(zero, checkout=main_co,
                       run=lambda cmd, **k: ran.append(cmd) or subprocess.CompletedProcess(cmd, 0, "baseline set", ""))
    assert u["state"] == "unfrozen" and ran and ran[0][-3:] == ["parity", "baseline", "--set"]
    assert git(main_co, "rev-parse", "HEAD") == git(w["bare"], "rev-parse", "main")
    assert train.load_state(zero)["ff_pending"] == []


def test_ci_verdict_never_green_while_a_rerun_runs():
    ok = [{"name": "test (windows-latest)", "status": "completed", "conclusion": "success"},
          {"name": "test (ubuntu-latest)", "status": "completed", "conclusion": "success"}]
    sha = "a" * 40
    assert train.ci_verdict(ok, sha)[0] == "green"
    assert train.ci_verdict(ok, sha, workflows=[{"name": "CI", "status": "in_progress"}])[0] == "pending"
    assert train.ci_verdict(ok[:1], sha)[0] == "pending"                     # no ubuntu job yet
    assert train.ci_verdict([dict(ok[0], conclusion="failure"), ok[1]], sha)[0] == "red"
    assert train.ci_verdict([], sha)[0] == "none"


def test_failure_modes_are_named_and_each_has_a_check():
    names = [m["name"] for m in train.FAILURE_MODES]
    assert len(names) == len(set(names)) >= 9
    for m in train.FAILURE_MODES:
        assert m["check"] and m["tool"].startswith(("fieldkit", "hooks/"))


def test_ship_dispatches_the_train_verbs(capsys):
    assert ship.main(["train", "--modes"]) == 0
    assert "merged-before-final" in capsys.readouterr().out


def test_platform_index_on_this_tree_equals_its_generator():
    gens = mp.generators(ROOT)
    path = "fieldkit/desk/platform_index.json"
    ok, why = mp.gen_matches(ROOT, path, gens[path])
    assert ok, why


def test_prove_kinds(world):
    w = world
    d = w["clone"]("p")
    base = git(d, "rev-parse", "HEAD")
    git(d, "checkout", "-q", "-b", "x")
    Path(d, "gen.py").write_text(GEN + "# ours\n", encoding="utf-8")
    git(d, "commit", "-q", "-am", "ours")
    ours = git(d, "rev-parse", "HEAD")
    git(d, "checkout", "-q", "-b", "y", base)
    Path(d, "gen.py").write_text("# theirs\n" + GEN, encoding="utf-8")
    git(d, "commit", "-q", "-am", "theirs")
    theirs = git(d, "rev-parse", "HEAD")
    git(d, "checkout", "-q", "x")
    git(d, "merge", "-q", "--no-edit", "y")
    p = mp.prove(d, ours, theirs, "HEAD", tree_dir=d)
    assert p["merge_clean"] and not p["skip_ok"] and p["rows"][0]["kind"] == "line-merged"
    assert mp.expected3("b", "o", "b") == "o" and mp.expected3("b", "b", "t") == "t"
    assert mp.expected3("b", "o", "t") is None
