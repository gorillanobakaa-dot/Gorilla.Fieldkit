"""fieldkit ship batch: a local bare repository plays GitHub; three PRs, one of them bad (plan P9, 2026-11-12).

Green path, a generated-file conflict regenerated, a real conflict left out, a red batch whose culprit is found in
<= log2(n)+1 runs, an infrastructure red that never bisects, and the item-8 selection (what a member's own green
head already proved is not run again).
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit.method import batch, mergeproof as mp, strikes

GEN = """import hashlib, json, pathlib
cards = sorted(p.name for p in pathlib.Path("cards").glob("*.txt"))
out = json.dumps({"cards": cards}, indent=1) + "\\n"
import sys
if "--write" in sys.argv:
    pathlib.Path("index.json").write_text(out, encoding="utf-8", newline="\\n")
else:
    print(out, end="")
"""
POL = {"branch": "night/batch-{stamp}", "max": 10, "need_checks": ["windows", "ubuntu"],
       "generated": [{"path": "index.json", "regenerate": ["{python}", "gen.py", "--write"]}],
       "after_merge": ["index.json"], "culprit": {"max_rounds_extra": 1, "infra_reruns": True},
       "select": {"skip_proven": True, "overlap_risk": True}}


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"git {' '.join(a)}: {r.stderr}")
    return r.stdout.strip()


def regen(repo):
    out = subprocess.run([sys.executable, "gen.py"], cwd=repo, capture_output=True, text=True, check=True).stdout
    Path(repo, "index.json").write_text(out, encoding="utf-8", newline="\n")


class FakeGH:
    def __init__(self, bare):
        self.bare, self.prs_, self.ci, self.closed, self.comments, self.merged = bare, {}, {}, [], [], []
        self.next_n, self.batch_prs_made, self.auto = 100, {}, []

    def add(self, n, branch, created, draft=False, auto=True, ci="green"):
        self.prs_[n] = {"number": n, "headRefName": branch, "baseRefName": "main", "isDraft": draft,
                        "createdAt": created, "state": "OPEN", "autoMergeRequest": {"x": 1} if auto else None}
        self.ci[n] = ci

    def head(self, branch):
        return git(self.bare, "rev-parse", f"refs/heads/{branch}")

    def batch_prs(self):
        return [dict(p, headRefOid=self.head(p["headRefName"])) for p in self.prs_.values() if p["state"] == "OPEN"]

    def required_ok(self, head, need):
        for n, p in self.prs_.items():
            if self.head(p["headRefName"]) == head:
                return self.ci[n], f"fake {self.ci[n]}"
        return "none", "unknown head"

    def pr(self, n):
        return dict(self.prs_[n], headRefOid=self.head(self.prs_[n]["headRefName"]))

    def files(self, n):
        br = self.prs_[n]["headRefName"]
        return git(self.bare, "diff", "--name-only", f"main...{br}").split()

    def create_pr(self, branch, base, title, body):
        n = self.next_n
        self.next_n += 1
        self.prs_[n] = {"number": n, "headRefName": branch, "baseRefName": base, "isDraft": False,
                        "createdAt": "2099", "state": "OPEN", "autoMergeRequest": None, "body": body}
        self.ci[n] = "green"
        return n, ""

    def auto_merge(self, n, head):
        self.auto.append(n)
        return True, ""

    def merge(self, n, head):
        git(self.bare, "update-ref", "refs/heads/main", head)
        self.prs_[n]["state"] = "MERGED"
        self.merged.append(n)
        return True, ""

    def close(self, n, comment):
        self.prs_[n]["state"] = "CLOSED"
        self.closed.append((n, comment))
        return True, ""

    def comment(self, n, body):
        self.comments.append((n, body))
        return True, ""


@pytest.fixture
def world(tmp_path, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "fieldkit-test", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "fieldkit-test", "FIELDKIT_TESTCOST_DIR": str(tmp_path / "tc"),
                 "FIELDKIT_PARKED_FILE": str(tmp_path / "parked.json")}.items():
        monkeypatch.setenv(k, v)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "cards").mkdir()
    (seed / "cards" / "m.txt").write_text("m\n", encoding="utf-8")
    (seed / "gen.py").write_text(GEN, encoding="utf-8")
    (seed / "shared.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    regen(seed)
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "-u", "origin", "main")
    repo = tmp_path / "zero"
    subprocess.run(["git", "clone", "-q", str(bare), str(repo)], check=True, capture_output=True)
    gh = FakeGH(bare)
    w = {"tmp": tmp_path, "bare": bare, "repo": repo, "gh": gh, "n": 0}

    def pr(n, branch, card=None, shared=None, test=None, **kw):
        d = tmp_path / f"s{n}"
        subprocess.run(["git", "clone", "-q", str(bare), str(d)], check=True, capture_output=True)
        git(d, "checkout", "-q", "-b", branch, "origin/main")
        if card:
            (d / "cards" / card).write_text(card + "\n", encoding="utf-8")
            regen(d)
        if shared:
            (d / "shared.txt").write_text(shared, encoding="utf-8")
        if test:
            (d / "tests").mkdir(exist_ok=True)
            (d / "tests" / f"test_{test[0]}.py").write_text(test[1], encoding="utf-8")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", f"pr {n}")
        git(d, "push", "-q", "-u", "origin", branch)
        w["n"] += 1
        gh.add(n, branch, f"2026-11-12T0{w['n']}:00:00Z", **kw)
    w["pr"] = pr

    def push(work, base):                               # the mandate door is ship push; here the bare repo
        br = git(work, "branch", "--show-current")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{br}", check=False)
        return {"ok": True, "branch": br}
    w["push"] = push
    return w


def no_parked(_branch):
    return None


# -- 1. collect: only PRs green at their own head ----------------------------------------------------------------------
def test_collect_takes_only_green_nondraft_automerge_unparked_oldest_first(world):
    w, gh = world, world["gh"]
    w["pr"](5, "feat/e", card="e.txt")
    w["pr"](2, "feat/b", card="b.txt")
    w["pr"](3, "feat/red", card="r.txt", ci="red")
    w["pr"](4, "feat/draft", card="d.txt", draft=True)
    w["pr"](6, "feat/noauto", card="n.txt", auto=False)
    w["pr"](7, "feat/parked", card="p.txt")
    w["pr"](8, "feat/pending", card="q.txt", ci="pending")
    got = batch.collect(gh, POL, parked=lambda b: {"x": 1} if b == "feat/parked" else None)
    assert [m["pr"] for m in got["take"]] == [5, 2]                       # creation order, not number order
    why = {x["pr"]: x["why"] for x in got["left"]}
    assert "its own fix" in why[3] and "draft" in why[4] and "auto-merge" in why[6] and "parked" in why[7]
    assert "wait for it" in why[8]
    assert [m["pr"] for m in batch.collect(gh, POL, 1, parked=no_parked)["take"]] == [5]


# -- 2. build ------------------------------------------------------------------------------------------------------------
def test_build_regenerates_generated_conflict_and_leaves_a_real_conflict_out(world):
    w, gh = world, world["gh"]
    w["pr"](1, "feat/a", card="a.txt", shared="ONE\ntwo\nthree\n")
    w["pr"](2, "feat/b", card="b.txt")                                     # conflicts with #1 only in index.json
    w["pr"](3, "feat/c", card="c.txt", shared="uno\ntwo\nthree\n")         # a REAL conflict with #1 in shared.txt
    c = batch.collect(gh, POL, parked=no_parked)
    b = batch.build(w["repo"], c["take"], "main", POL, push=w["push"])
    assert b["ok"], b
    assert [m["pr"] for m in b["members"]] == [1, 2]
    assert b["left"] == [{"pr": 3, "why": "a real conflict in shared.txt", "files": ["shared.txt"]}]
    assert b["members"][1]["regenerated"] == ["index.json"]
    tip = b["branch"]
    idx = json.loads(git(w["bare"], "show", f"refs/heads/{tip}:index.json"))
    assert idx["cards"] == ["a.txt", "b.txt", "m.txt"]                    # the file is its generator's output
    assert git(w["bare"], "show", f"refs/heads/{tip}:shared.txt").startswith("ONE")
    kinds = [r["kind"] for r in batch.read_journal(w["repo"])]
    assert kinds.count("joined") == 2 and "left-out" in kinds


def test_a_generator_that_fails_leaves_the_pr_out_not_a_hand_edit(world):
    w, gh = world, world["gh"]
    w["pr"](1, "feat/a", card="a.txt")
    w["pr"](2, "feat/b", card="b.txt")
    bad = dict(POL, generated=[{"path": "index.json", "regenerate": ["{python}", "nonexistent.py"]}])
    b = batch.build(w["repo"], batch.collect(gh, POL, parked=no_parked)["take"], "main", bad, push=w["push"])
    assert [m["pr"] for m in b["members"]] == [1] and b["left"][0]["pr"] == 2 and "generator" in b["left"][0]["why"]


# -- 3. run: green ---------------------------------------------------------------------------------------------------------
def test_run_green_merges_one_pr_and_closes_the_members(world, monkeypatch):
    w, gh = world, world["gh"]
    for i, c in enumerate("abc", 1):
        w["pr"](i, f"feat/{c}", card=f"{c}.txt")
    monkeypatch.setattr(batch, "collect", lambda g, p=None, m=None: _collect(g))
    r = batch.run(w["repo"], gh, pol=POL, push=w["push"], waiter=lambda n: {"ok": True})
    assert r["ok"] and r["state"] == "merged" and sorted(r["merged"]) == [1, 2, 3], r
    assert gh.merged == [100] and gh.auto == [100]
    assert sorted(n for n, _ in gh.closed) == [1, 2, 3]                    # none was merged by GitHub itself here
    body = gh.prs_[100]["body"]
    assert all(f"#{i}" in body for i in (1, 2, 3)) and "fieldkit-batch" in body


def _collect(gh):
    return {"take": [{"pr": p["number"], "branch": p["headRefName"], "head": p["headRefOid"]}
                                         for p in sorted(gh.batch_prs(), key=lambda p: p["createdAt"])], "left": []}


# -- 4. run: red, the culprit ------------------------------------------------------------------------------------------------
BAD = ("bad", "def test_y():\n    assert False\n")
OK = lambda name: (name, "def test_ok():\n    assert True\n")           # noqa: E731


def make_red_world(w, n_members, bad_index):
    for i in range(1, n_members + 1):
        t = BAD if i == bad_index else OK(f"ok{i}")
        w["pr"](i, f"feat/m{i}", card=f"m{i}.txt", test=t)


def red_waiter(w):
    def wait(n):
        br = w["gh"].prs_[n]["headRefName"]
        has_bad = subprocess.run(["git", "-C", str(w["bare"]), "cat-file", "-e", f"refs/heads/{br}:tests/test_bad.py"],
                                 capture_output=True).returncode == 0
        if has_bad:
            return {"ok": False, "state": "red", "log": ["FAILED tests/test_bad.py::test_y - assert False"]}
        return {"ok": True}
    return wait


def finder_with(tmap):
    return lambda log, files, repo, is_green=None: batch.find_culprit(log, files, repo, is_green, POL,
                                                                      tests_of_=lambda pr: tmap[pr])


@pytest.mark.parametrize("n,bad", [(3, 2), (4, 4), (5, 1)])
def test_red_batch_bisects_to_the_culprit_in_log2_runs_and_the_rest_merges(world, monkeypatch, n, bad):
    w, gh = world, world["gh"]
    make_red_world(w, n, bad)
    monkeypatch.setattr(batch, "collect", lambda g, p=None, m=None: _collect(g))
    # every member covers the failing test file: ownership alone cannot name the culprit, so it bisects
    tmap = {i: {"tests/test_bad.py"} for i in range(1, n + 1)}
    r = batch.run(w["repo"], gh, pol=POL, push=w["push"], waiter=red_waiter(w), finder=finder_with(tmap))
    assert r["ok"] and r["state"] == "merged", json.dumps(r, default=str)
    assert [d["pr"] for d in r["dropped"]] == [bad]
    assert r["dropped"][0]["how"] == "bisect"
    assert r["dropped"][0]["runs"] <= math.log2(n) + 1
    assert sorted(r["merged"]) == [i for i in range(1, n + 1) if i != bad]
    assert any(c[0] == bad and "Dropped from batch" in c[1] for c in gh.comments)
    brief = Path(r["dropped"][0]["brief"]).read_text(encoding="utf-8")
    assert f"PR #{bad}" in brief and "test_bad.py::test_y" in brief


def test_one_owner_of_every_failing_test_is_the_culprit_with_no_bisect_run(world, monkeypatch):
    w, gh = world, world["gh"]
    make_red_world(w, 3, 3)
    monkeypatch.setattr(batch, "collect", lambda g, p=None, m=None: _collect(g))
    tmap = {1: {"tests/test_ok1.py"}, 2: {"tests/test_ok2.py"}, 3: {"tests/test_bad.py"}}
    r = batch.run(w["repo"], gh, pol=POL, push=w["push"], waiter=red_waiter(w), finder=finder_with(tmap))
    assert r["dropped"][0]["pr"] == 3 and r["dropped"][0]["runs"] == 0 and r["state"] == "merged"
    assert "bisect-run" not in [x["kind"] for x in batch.read_journal(w["repo"])]


def test_an_infrastructure_red_re_runs_and_never_bisects():
    log = ["Temporary failure resolving 'archive.ubuntu.com'", "E: Failed to fetch http://x"]
    got = batch.find_culprit(log, {1: ["a"], 2: ["b"]}, is_green=lambda s: pytest.fail("bisected an infra red"), pol=POL)
    assert got["state"] == "infra"


def test_a_log_nobody_explains_goes_to_a_model_not_a_guess():
    got = batch.find_culprit(["Segmentation fault"], {1: ["a"]}, pol=POL)
    assert got["state"] == "unexplained" and "counsel" in got["next"]


def test_a_failed_test_named_in_an_infra_looking_log_is_never_infra():
    log = ["Temporary failure resolving 'x'", "FAILED tests/test_a.py::test_x - boom"]
    got = batch.find_culprit(log, {1: ["a"]}, pol=POL, tests_of_=lambda pr: {"tests/test_a.py"})
    assert got["state"] == "culprit" and got["pr"] == 1


# -- the pure parts --------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [2, 3, 5, 8, 13, 50])
def test_bisect_finds_each_position_within_the_budget(n):
    members = list(range(n))
    for bad in members:
        seen = []

        def is_green(subset, bad=bad):
            seen.append(len(subset))
            return bad not in subset
        got, runs = batch.bisect(members, is_green)
        assert got == bad and runs == len(seen) <= batch.runs_allowed(n, POL)


def test_attribute_one_owner_many_owners_and_no_owner():
    assert batch.attribute({"t1.py"}, {1: {"t1.py"}, 2: {"t2.py"}}) == ("culprit", 1)
    assert batch.attribute({"t1.py", "t2.py"}, {1: {"t1.py", "t2.py"}, 2: {"t2.py"}}) == ("culprit", 1)
    assert batch.attribute({"t1.py"}, {1: {"t1.py"}, 2: {"t1.py"}}) == ("candidates", [1, 2])
    assert batch.attribute({"t1.py", "t2.py"}, {1: {"t1.py"}, 2: {"t2.py"}}) == ("candidates", [1, 2])
    assert batch.attribute({"tx.py"}, {1: {"t1.py"}, 2: {"t2.py"}}) == ("candidates", [1, 2])


# -- item 8: do not re-test what a member's green head proved ------------------------------------------------------------------
def test_select_runs_only_overlap_and_batch_written_files(monkeypatch):
    cover = {"a.py": {"tests/t_a.py", "tests/t_ab.py"}, "b.py": {"tests/t_b.py", "tests/t_ab.py"},
             "c.py": {"tests/t_c.py"}, "gen.json": {"tests/t_gen.py"}}
    monkeypatch.setattr(batch, "tests_of", lambda repo, paths, base=None: set().union(*[cover[p] for p in paths]))
    got = batch.select(".", {1: ["a.py"], 2: ["b.py"], 3: ["c.py"]}, regenerated=["gen.json"], pol=POL)
    assert got["tests"] == ["tests/t_ab.py", "tests/t_gen.py"]
    assert got["proven"] == ["tests/t_a.py", "tests/t_b.py", "tests/t_c.py"]
    assert got["owners"]["tests/t_ab.py"] == [1, 2]
    off = dict(POL, select={"skip_proven": False, "overlap_risk": True})
    assert len(batch.select(".", {1: ["a.py"], 2: ["b.py"], 3: ["c.py"]}, pol=off)["tests"]) == 4


def test_policy_file_is_data_with_the_generated_files_the_owner_named():
    pol = batch.policy()
    paths = [g["path"] for g in pol["generated"]]
    assert {"fieldkit/desk/platform_index.json", "fieldkit/desk/tools.yaml", "docs/groups.yaml",
            "docs/PLATFORMS.md"} <= set(paths)
    assert pol["branch"].startswith("night/")                           # inside the mandate's push patterns
    assert pol["shards"]["default"] == 4


def test_body_lists_every_member_and_what_was_left_out():
    body = batch.body_of([{"pr": 1, "branch": "feat/a", "head": "a" * 40}], [{"pr": 9, "why": "conflict"}])
    assert "#1" in body and "#9: conflict" in body and body.count("fieldkit-batch") == 1


def test_failures_with_no_common_owner_bisect_among_every_owner():
    from fieldkit.method import batch
    kind, pool = batch.attribute({"tests/a.py", "tests/b.py"}, {1: {"tests/a.py"}, 2: {"tests/b.py"}, 3: {"tests/c.py"}})
    assert (kind, pool) == ("candidates", [1, 2])
