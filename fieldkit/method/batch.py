# Version: 1.0.0 · updated 26-11-12-06-00
"""fieldkit ship batch: many PRs -> one branch -> one CI -> one merge; on red, the culprit is found, not guessed.

    fieldkit ship batch collect [--max N]      the PRs a batch may take: open, not draft, auto-merge on, green on every
                                               required check at their CURRENT head, not parked (TWO STRIKES); oldest
                                               first. A red PR is sent back to its own fix, never batched.
    fieldkit ship batch build [--only N ...]   a fresh branch night/batch-YYYYMMDD-HHMM from origin/main, each member's
                                               head merged in order. A conflict only in generated files (batch.yaml) is
                                               regenerated; any other conflict leaves that PR out (journaled with the
                                               files). Pushed through ship push (the mandate door).
    fieldkit ship batch run [--max N]          collect + build + one PR (body lists every member) + auto-merge on +
                                               wait for its CI. GREEN: merged, the member PRs close as merged. RED: the
                                               culprit is found from the failing test names (an infrastructure red
                                               re-runs, never bisects), dropped with its log and a brief, the batch is
                                               rebuilt without it and run again.
    fieldkit ship batch status                 the last batch's journal (JSON with --json)
    fieldkit ship batch select [--only N ...]  the tests the batch's CI runs: what no member's own green head proved,
                                               plus what two members both cover (the integration risk)

Owner, 2026-11-12: "if for each of every one of them we have to wait 30 to 40 minutes, that's bullshit. We need a
standard operating procedure that joins all these PRs in one thing ... a deterministic tool ... if it fails the harness
will find which one has failed." The hand-made batch of that day (7 PRs, PR 477): the only conflict was a generated
file. Same day, owner: "check the GitHub before running thousands of tests: some of them already passed" - 5 of those 7
PRs were RED on windows-latest; collect takes only PRs green at their own head.

Never: force-push, hand-resolve a conflict, batch a red or parked PR, bisect an infrastructure red. Data is
batch.yaml. Exit 0 done, 3 refused or left something out (with why), 2 bad usage.
"""
import argparse
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from . import emit
from . import mergeproof as mp
from . import strikes
from . import train

POLICY = Path(__file__).with_name("batch.yaml")
JOURNAL = "batch.jsonl"
MARK = re.compile(r"<!-- fieldkit-batch (\{.*?\}) -->", re.S)


def policy(path=None):
    import yaml
    return (yaml.safe_load(Path(path or POLICY).read_text(encoding="utf-8")) or {}).get("batch") or {}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def stamp(now=None):
    return time.strftime("%Y%m%d-%H%M", time.gmtime(now))


def journal(repo, kind, **kw):
    """One row of the batch journal (the clone's git folder; both machines' sessions read it with `batch status`)."""
    row = dict(kw, kind=kind, at=_now())
    with (mp.state_dir(repo) / JOURNAL).open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_journal(repo):
    p = mp.state_dir(repo) / JOURNAL
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []


# -- GitHub, through gh (a fake in the tests) --------------------------------------------------------------------------
class BatchGH(train.GH):
    BFIELDS = "number,title,headRefName,baseRefName,headRefOid,isDraft,createdAt,state,autoMergeRequest"

    def batch_prs(self):
        return self._json("pr", "list", "-R", self.slug, "--state", "open", "--limit", "200", "--json", self.BFIELDS)

    def create_pr(self, branch, base, title, body):
        rc, out, err = self._gh("pr", "create", "-R", self.slug, "--head", branch, "--base", base, "--title", title,
                                "--body", body)
        m = re.search(r"/pull/(\d+)", out or "")
        return (int(m.group(1)) if m else None), (err or out).strip()[-300:]

    def auto_merge(self, n, head):
        rc, out, err = self._gh("pr", "merge", str(n), "-R", self.slug, "--auto", "--merge", "--match-head-commit", head)
        return rc == 0, (out + err).strip()[-300:]

    def close(self, n, comment):
        rc, _, err = self._gh("pr", "close", str(n), "-R", self.slug, "--comment", comment)
        return rc == 0, err.strip()[-200:]

    def required_ok(self, head, need):
        return train.ci_verdict(self.check_runs(head), head, need=tuple(need), slug=self.slug,
                                workflows=self._json("api", f"repos/{self.slug}/actions/runs?head_sha={head}", "--jq",
                                                     "[.workflow_runs[] | {name, status, conclusion}]") or [])


# -- 1. collect --------------------------------------------------------------------------------------------------------
def collect(gh, pol=None, max_n=None, parked=strikes.parked_for):
    """-> {"take": [rows], "left": [{pr, why}]}: oldest first. Each PR is judged at its CURRENT head, from GitHub."""
    pol = pol or policy()
    limit = max_n or pol.get("max") or 50
    take, left = [], []
    for pr in sorted(gh.batch_prs(), key=lambda p: (p.get("createdAt") or "", p["number"])):
        n, br = pr["number"], pr["headRefName"]
        why = ""
        if pr.get("isDraft"):
            why = "draft"
        elif br.startswith("night/batch-"):
            why = "a batch branch itself"
        elif not pr.get("autoMergeRequest"):
            why = "auto-merge is not on (its session did not declare it final)"
        elif parked(br):
            why = "parked (TWO STRIKES): left for a stronger model"
        else:
            state, detail = gh.required_ok(pr["headRefOid"], pol.get("need_checks") or train.NEED_OS)
            if state != "green":
                why = (f"its own CI is {state} ({detail}): send it back to its own fix first, never batched"
                       if state == "red" else f"its own CI is {state} ({detail}): wait for it, never batched")
        if why:
            left.append({"pr": n, "branch": br, "why": why})
        elif len(take) >= limit:
            left.append({"pr": n, "branch": br, "why": f"over --max {limit}: the next batch"})
        else:
            take.append({"pr": n, "branch": br, "head": pr["headRefOid"]})
    return {"take": take, "left": left}


# -- 2. build ----------------------------------------------------------------------------------------------------------
def _argv(a):
    return [str(x).replace("{python}", sys.executable) for x in a]


def _regenerate(work, pol, paths):
    """Run the generator of each path in `paths` that batch.yaml names -> the paths written. Raises RuntimeError."""
    done = []
    for g in pol.get("generated") or []:
        if g["path"] in paths and g.get("regenerate"):
            r = subprocess.run(_argv(g["regenerate"]), cwd=str(work), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=mp._gen_env(), timeout=600)
            if r.returncode:
                raise RuntimeError(f"the generator of {g['path']} failed (exit {r.returncode}): "
                                   f"{(r.stderr or r.stdout).strip()[-200:]}")
            done.append(g["path"])
    return done


def merge_member(work, member, pol, who):
    """Merge one PR head into the checked-out batch branch. -> {"ok": True, "regenerated": [...]} or
    {"ok": False, "why", "files"}; on a refusal the tree is back as it was (merge --abort)."""
    g = {x["path"]: x for x in pol.get("generated") or []}
    ref = f"refs/remotes/origin/{member['branch']}"
    r = mp._git(work, *who, "merge", "--no-ff", "--no-commit", ref, check=False)
    conflicted = mp._git(work, "diff", "--name-only", "--diff-filter=U").split()
    left = []
    for path in conflicted:
        if path in g and g[path].get("driver"):
            if not mp._resolve_with_driver(work, path):
                left.append(path)
        elif path in g:
            mp._git(work, "checkout", "--theirs", "--", path, check=False)   # replaced by its generator below
        else:
            left.append(path)
    if left or (r.returncode and not conflicted):
        mp._git(work, "merge", "--abort", check=False)
        return {"ok": False, "files": left or [], "why": ("a real conflict in " + ", ".join(left)) if left else
                f"merge failed: {(r.stderr or r.stdout).strip()[-200:]}"}
    regen = []
    try:
        regen = _regenerate(work, pol, set(conflicted))
    except RuntimeError as e:
        mp._git(work, "merge", "--abort", check=False)
        return {"ok": False, "files": [], "why": str(e)}
    for p in regen + [c for c in conflicted if c in g]:
        mp._git(work, "add", "--", p)
    c = mp._git(work, *who, "commit", "-m", f"Merge PR #{member['pr']} ({member['branch']}) into the batch", check=False)
    if c.returncode:
        mp._git(work, "merge", "--abort", check=False)
        return {"ok": False, "files": [], "why": f"commit refused: {(c.stdout + c.stderr).strip()[-300:]}"}
    return {"ok": True, "regenerated": sorted(set(regen) | {x for x in conflicted if x in g})}


def build(repo, members, base="main", pol=None, name=None, push=None, work=None):
    """-> {"ok", "branch", "members": [...], "left": [{pr, why, files}], "head"}. `push(work, base)` is ship.push."""
    pol = pol or policy()
    branch = name or pol.get("branch", "night/batch-{stamp}").format(stamp=stamp())
    work = work or train.clone_for(repo)
    mp._git(work, "fetch", "--quiet", "origin", check=False)
    if mp._git(work, "status", "--porcelain").strip() or (Path(work) / ".git" / "MERGE_HEAD").exists():
        mp._git(work, "merge", "--abort", check=False)
        mp._git(work, "reset", "--hard", "--quiet")       # the train's own clone: a crash left it half-merged
    mp._git(work, "checkout", "--quiet", "-B", branch, f"refs/remotes/origin/{base}")
    who, joined, left = mp.identity(work), [], []
    for m in members:
        got = merge_member(work, m, pol, who)
        if got["ok"]:
            joined.append(dict(m, regenerated=got["regenerated"]))
            journal(repo, "joined", pr=m["pr"], branch=branch, regenerated=got["regenerated"])
        else:
            left.append({"pr": m["pr"], "why": got["why"], "files": got["files"]})
            journal(repo, "left-out", pr=m["pr"], branch=branch, why=got["why"], files=got["files"])
    rep = {"branch": branch, "members": joined, "left": left, "work": str(work)}
    if not joined:
        return dict(rep, ok=False, state="empty", next="no PR could be joined; nothing was pushed")
    fixed = []
    try:
        fixed = _regenerate(work, pol, set(pol.get("after_merge") or []))
    except RuntimeError as e:
        return dict(rep, ok=False, state="generator-failed", error=str(e), next="fix the generator; nothing was pushed")
    changed = [p for p in fixed if mp._git(work, "status", "--porcelain", "--", p).strip()]
    if changed:
        mp._git(work, "add", "--", *changed)
        c = mp._git(work, *who, "commit", "-m", "batch: generated files regenerated after the last merge", check=False)
        if c.returncode:
            return dict(rep, ok=False, state="commit-refused", error=(c.stdout + c.stderr).strip()[-300:],
                        next="the hook refused the regeneration commit; read why")
    head = mp.rev(work, "HEAD")
    if push is None:
        from .ship import push as push_
        push = push_
    p = push(work, base)
    if not p.get("ok"):
        return dict(rep, ok=False, state="push-refused", head=head, error=p.get("error") or p.get("state"),
                    next=p.get("next", "nothing was forced"))
    journal(repo, "built", branch=branch, head=head, members=[m["pr"] for m in joined], left=[x["pr"] for x in left])
    return dict(rep, ok=True, state="built", head=head, next=f"fieldkit ship batch run (opens one PR from {branch})")


# -- selection: what the batch's CI runs -------------------------------------------------------------------------------
def tests_of(repo, paths, base=None):
    from .. import testcost
    pl = testcost.plan(repo, base=base, paths=list(paths))
    if pl.get("unmapped"):                             # a file no test covers: the selection fails closed (full suite)
        raise ValueError(f"unmapped files: {', '.join(pl['unmapped'][:5])}")
    return set(pl["tests"])


def select(repo, member_files, regenerated=(), pol=None):
    """Item 8 (2026-11-12): a test a single member's green head already ran is not run again; a test two members both
    cover (a file they both touched, or a test covering both's files) is the integration risk and runs; so do the tests
    of the files the batch itself wrote (regenerated). -> {"tests": sorted, "proven": sorted, "owners": {test: [PR]}}.
    `member_files`: {pr: [changed paths]}."""
    pol = pol or policy()
    sel = pol.get("select") or {}
    owners = {}
    for pr, files in member_files.items():
        for t in tests_of(repo, files):
            owners.setdefault(t, []).append(pr)
    run, proven = set(), set()
    for t, who in owners.items():
        if len(who) >= 2 and sel.get("overlap_risk", True):
            run.add(t)
        elif sel.get("skip_proven", True):
            proven.add(t)
        else:
            run.add(t)
    if regenerated:
        run |= tests_of(repo, regenerated)
    proven -= run
    return {"tests": sorted(run), "proven": sorted(proven), "owners": {t: sorted(w) for t, w in sorted(owners.items())}}


# -- red: the culprit --------------------------------------------------------------------------------------------------
def failing_files(log):
    """-> the test FILES a failed log names (`FAILED tests/x.py::test_y`), and the ids."""
    ids = strikes.failed_tests(log)
    return {i.split("::")[0].replace("\\", "/") for i in ids}, ids


def owners_of(failed_files, member_tests):
    """-> {file: [PRs whose changed files that test file covers]}; `member_tests`: {pr: set(test files)}."""
    return {f: sorted(pr for pr, ts in member_tests.items() if f in {t.replace("\\", "/") for t in ts})
            for f in sorted(failed_files)}


def attribute(failed_files, member_tests):
    """The deterministic part: -> ("culprit", PR) when exactly one member owns every failing test file;
    ("candidates", [PRs]) to bisect among (the members that own any failing file; all members when none does)."""
    own = owners_of(failed_files, member_tests)
    if own:
        common = set.intersection(*[set(v) for v in own.values()])
        if len(common) == 1:
            return "culprit", next(iter(common))
        pool = sorted(common or {pr for v in own.values() for pr in v})
        if pool:
            return "candidates", pool
    return "candidates", sorted(member_tests)


def bisect(members, is_green):
    """Halves: -> (culprit PR, runs). `is_green(subset)` builds that subset and runs the failing tests. The culprit is in
    the first half when the first half is red, else in the second; ceil(log2 n) runs. One culprit per call: a second
    one is found by the next round's red."""
    pool, runs = list(members), 0
    while len(pool) > 1:
        half = pool[:len(pool) // 2]
        runs += 1
        pool = pool[len(pool) // 2:] if is_green(half) else half
    return (pool[0] if pool else None), runs


def runs_allowed(n, pol=None):
    return math.ceil(math.log2(max(n, 2))) + int((pol or policy()).get("culprit", {}).get("max_rounds_extra", 1))


def find_culprit(log, member_files, repo=".", is_green=None, pol=None, tests_of_=None):
    """Red batch -> {"state": "infra" | "culprit" | "unexplained", ...}. Order: a runner-infrastructure signature
    re-runs (never bisects); failing test names -> members that own them; one owner = the culprit; else bisect."""
    pol = pol or policy()
    inf = strikes.infra(log) if (pol.get("culprit") or {}).get("infra_reruns", True) else ""
    if inf:
        return {"state": "infra", "why": inf, "next": "re-run the failed jobs (ci-wait does, 3 times per head)"}
    files, ids = failing_files(log)
    if not files:
        return {"state": "unexplained", "why": "no failing test name in the log and no infrastructure signature",
                "next": "a model explains this log: the cheapest capable rung (fieldkit counsel, haiku first); its "
                        "finding becomes a signature"}
    tmap = {pr: (tests_of_(pr) if tests_of_ else tests_of(repo, fs)) for pr, fs in member_files.items()}
    kind, val = attribute(files, tmap)
    rep = {"failed": sorted(ids), "owners": owners_of(files, tmap)}
    if kind == "culprit":
        return dict(rep, state="culprit", pr=val, runs=0, how="one member owns every failing test")
    if is_green is None:
        return dict(rep, state="candidates", candidates=val, why="several members own the failures; bisect needed")
    pr, runs = bisect(val, is_green)
    return dict(rep, state="culprit" if pr else "unexplained", pr=pr, runs=runs, candidates=val, how="bisect")


def local_runner(repo, members, base, ids, pol, work=None, python=None):
    """The default `is_green` of bisect: build the subset here (no push) and run ONLY the tests that failed on the batch.
    Local first, so the runner being down or slow costs nothing: a bisect is `ceil(log2 n)` runs of a few tests."""
    by_pr = {m["pr"]: m for m in members}

    def is_green(subset):
        b = build(repo, [by_pr[p] for p in subset], base, pol, name="night/batch-bisect",
                  push=lambda w, bs: {"ok": True}, work=work)
        if not b["ok"] or b["left"]:
            return False                               # a subset that does not even join is not green
        here = [i for i in sorted(ids) if (Path(b["work"]) / i.split("::")[0]).is_file()]   # a test this subset lacks cannot fail
        if not here:
            journal(repo, "bisect-run", subset=list(subset), green=True, note="the failing tests are not in it")
            return True
        r = subprocess.run([python or sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *here],
                           cwd=b["work"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        journal(repo, "bisect-run", subset=list(subset), green=r.returncode == 0)
        return r.returncode == 0
    return is_green


def brief_for(pr, branch, culprit, log):
    return (f"# PR #{pr} ({branch}) failed inside batch CI\n\nIts own CI was green at its head; joined with the other "
            f"members of the batch it fails.\nHow it was found: {culprit.get('how', '?')} "
            f"({culprit.get('runs', 0)} bisect run(s)).\nFailing tests: {', '.join(culprit.get('failed') or ['?'])}\n\n"
            f"Fix it on the PR's own branch (merge origin/main first, run the tests above), push with `fieldkit ship "
            f"push --auto`; it joins the next batch.\n\n## Log tail\n\n```\n" + "\n".join((log or [])[-40:]) + "\n```\n")


# -- 3/4. run ----------------------------------------------------------------------------------------------------------
def body_of(members, left, tests=None):
    lines = [f"fieldkit ship batch: {len(members)} PRs, one branch, one CI, one merge.", "", "Members (merged in this order):"]
    lines += [f"- #{m['pr']} `{m['branch']}` at `{m['head'][:10]}`" for m in members]
    if left:
        lines += ["", "Left out of this batch:"] + [f"- #{x['pr']}: {x['why']}" for x in left]
    meta = {"members": [m["pr"] for m in members], "tests": tests}
    return "\n".join(lines) + f"\n\n<!-- fieldkit-batch {json.dumps(meta, sort_keys=True)} -->\n"


def _selection(gh, members, repo, pol):
    """The test files the batch CI runs (item 8), or None = the normal selection (also when anything is unmapped)."""
    try:
        files = {m["pr"]: gh.files(m["pr"]) for m in members}
        regen = sorted({f for m in members for f in (m.get("regenerated") or [])})
        return select(repo, files, regen, pol)["tests"]
    except Exception:
        return None


def run(repo=".", gh=None, base="main", pol=None, max_n=None, only=None, push=None, builder=None, waiter=None,
        finder=None, max_rounds=3, work=None):
    """collect -> build -> PR -> CI -> merge; on red find the culprit, drop it, rebuild, run again (max_rounds)."""
    pol = pol or policy()
    gh = gh or BatchGH(repo)
    c = collect(gh, pol, max_n)
    take = [m for m in c["take"] if not only or m["pr"] in only]
    rep = {"collected": [m["pr"] for m in take], "left": list(c["left"]), "rounds": []}
    for x in c["left"]:
        journal(repo, "not-collected", pr=x["pr"], why=x["why"])
    if len(take) < 2:
        return dict(rep, ok=False, state="nothing-to-batch",
                    next="fewer than 2 PRs are ready: `fieldkit ship push --auto` and ci-wait one by one is the way")
    waiter = waiter or (lambda n: train.ci_wait(gh, n, 3600 * 3))
    dropped = []
    for rnd in range(1, max_rounds + 1):
        b = (builder or build)(repo, take, base=base, pol=pol, push=push, work=work,
                               name=pol.get("branch", "night/batch-{stamp}").format(stamp=stamp()) + (f"-r{rnd}" if rnd > 1 else ""))
        rep["left"] += [x for x in b.get("left", [])]
        r = {"round": rnd, "branch": b.get("branch"), "state": b.get("state")}
        rep["rounds"].append(r)
        if not b["ok"]:
            return dict(rep, ok=False, state=b["state"], error=b.get("error"), next=b.get("next"))
        members = b["members"]
        n, err = gh.create_pr(b["branch"], base, f"batch {b['branch'].split('/')[-1]}: {len(members)} PRs",
                              body_of(members, rep["left"], _selection(gh, members, repo, pol)))
        if not n:
            return dict(rep, ok=False, state="pr-failed", error=err, next="the branch is pushed; open the PR by hand")
        gh.auto_merge(n, b["head"])
        r["pr"] = n
        res = waiter(n)
        r["ci"] = res.get("state") or ("green" if res.get("ok") else "red")
        journal(repo, "ci", pr=n, branch=b["branch"], state=r["ci"])
        if res.get("ok"):
            if gh.pr(n).get("state") != "MERGED":
                gh.merge(n, b["head"])
            rep["merged"] = [m["pr"] for m in members]
            for m in members:                          # GitHub closes them as merged; a still-open one is closed with a note
                if gh.pr(m["pr"]).get("state") == "OPEN":
                    gh.close(m["pr"], f"Merged through batch PR #{n} (its commits are in main).")
            journal(repo, "merged", pr=n, members=rep["merged"], dropped=dropped)
            return dict(rep, ok=True, state="merged", batch_pr=n, dropped=dropped,
                        next=f"#{n} is merged with {len(members)} PRs")
        log = res.get("log") or []
        files = {m["pr"]: gh.files(m["pr"]) for m in members}
        _, ids = failing_files(log)
        found = (finder or find_culprit)(log, files, repo,
                                         is_green=local_runner(repo, members, base, ids, pol, work) if ids else None)
        r["culprit"] = {k: v for k, v in found.items() if k != "owners"}
        if found["state"] != "culprit":                # infra: ci-wait already re-ran; unexplained: a model reads the log
            gh.close(n, "Batch CI red and not attributed: " + found.get("why", found["state"]))
            return dict(rep, ok=False, state=found["state"], error=found.get("why"), next=found.get("next"), log=log[-30:])
        bad = next(m for m in members if m["pr"] == found["pr"])
        brief = mp.state_dir(repo) / f"batch-brief-{bad['pr']}.md"
        brief.write_text(brief_for(bad["pr"], bad["branch"], found, log), encoding="utf-8", newline="\n")
        gh.comment(bad["pr"], f"Dropped from batch PR #{n}: it fails joined with the others "
                              f"({found.get('how')}). Brief: {brief}\n\n```\n" + "\n".join(log[-25:]) + "\n```")
        gh.close(n, f"Batch CI red; culprit PR #{bad['pr']} dropped, the batch is rebuilt without it.")
        dropped.append({"pr": bad["pr"], "how": found.get("how"), "runs": found.get("runs", 0), "brief": str(brief)})
        journal(repo, "dropped", pr=bad["pr"], batch=n, how=found.get("how"), runs=found.get("runs", 0))
        take = [m for m in members if m["pr"] != bad["pr"]]
        if len(take) < 2:
            return dict(rep, ok=False, state="batch-too-small", dropped=dropped,
                        next="fewer than 2 PRs remain: the rest go one by one (ship push --auto, ci-wait)")
    return dict(rep, ok=False, state="rounds-used", dropped=dropped, next="the batch was red 3 times: read the journal")


def status(repo="."):
    rows = read_journal(repo)
    last = [r for r in rows if r["kind"] in ("built", "ci", "merged", "dropped")][-8:]
    return {"ok": True, "rows": len(rows), "last": last, "left_out": [r for r in rows if r["kind"] == "left-out"][-10:]}


# -- command line ------------------------------------------------------------------------------------------------------
def lines(d):
    out = []
    for x in d.get("take", []):
        out.append(f"TAKE #{x['pr']} {x['branch']}")
    for x in d.get("left", []):
        out.append(f"LEFT #{x['pr']}: {x['why']}")
    for k in ("state", "batch_pr", "merged", "dropped", "error", "next"):
        if d.get(k):
            out.append(f"{k}: {d[k]}")
    return out or ["nothing"]


def main(argv=None, prog="fieldkit ship batch"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=["collect", "build", "run", "status", "select"])
    ap.add_argument("--max", type=int, dest="max_n", help="at most N PRs in the batch")
    ap.add_argument("--only", type=int, nargs="*", help="only these PR numbers")
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.verb == "status":
            r = status(a.repo)
            emit(r, a.json, lambda d: [f"{x['kind']} {json.dumps({k: v for k, v in x.items() if k != 'kind'})}"
                                       for x in d["last"]] or ["no batch yet"])
            return 0
        gh = BatchGH(a.repo)
        if a.verb == "collect":
            r = dict(collect(gh, None, a.max_n), ok=True)
            emit(r, a.json, lines)
            return 0
        if a.verb == "select":
            c = collect(gh)
            files = {m["pr"]: gh.files(m["pr"]) for m in c["take"] if not a.only or m["pr"] in a.only}
            r = dict(select(a.repo, files), ok=True)
            emit(r, a.json, lambda d: [f"RUN {t}" for t in d["tests"]] + [f"proven (not run): {len(d['proven'])}"])
            return 0
        if a.verb == "build":
            c = collect(gh, None, a.max_n)
            r = build(a.repo, [m for m in c["take"] if not a.only or m["pr"] in a.only], a.base)
            emit(r, a.json, lines)
            return 0 if r["ok"] else 3
        r = run(a.repo, gh, a.base, max_n=a.max_n, only=a.only)
        emit(r, a.json, lines)
        return 0 if r["ok"] else 3
    except RuntimeError as e:
        emit({"ok": False, "error": str(e), "next": "nothing was forced"}, a.json, lambda d: [f"error: {d['error']}"])
        return 3


if __name__ == "__main__":
    sys.exit(main())
