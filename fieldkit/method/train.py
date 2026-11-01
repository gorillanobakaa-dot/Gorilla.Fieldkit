# Version: 1.1.0 · updated 26-10-28-18-00
"""The merge train: a PR is merged only when its session declared it final, one at a time, never red (part 2).

    fieldkit ship ready PR [--pending TEXT | --withdraw]
                                    the session declares its PR final: label fieldkit-ready + a marker comment with
                                    the head sha. Refused while this checkout has unpushed or uncommitted work, a merge
                                    in progress, or a commit still inside the pre-commit hook. --pending: "one more
                                    commit is coming" (the train will not merge it); --withdraw: not final any more
    fieldkit ship train [--dry-run] [--only PR ...] [--max-wait MIN]
                                    merge the ready PRs one at a time: dependencies first, each brought up to date with
                                    main (GitHub's update-branch, or here with generated files regenerated and cards
                                    merged entry by entry), CI awaited, merged with gh pr merge --merge
                                    --match-head-commit <the declared head or one the train made>. Resumable: the state
                                    file says where it stopped. --dry-run reads only and shows the order.
    fieldkit ship overlap [--files F ...]   before work starts / at PR open: open PRs and live stations on the same files
    fieldkit ship freeze --reason TEXT [--checkout PATH] | unfreeze
                                    a checkout that must not move (an elevated goal runs from it): the train records
                                    "ff pending"; unfreeze fast-forwards it and runs fieldkit parity baseline --set
    fieldkit ship train --modes     the failure modes this protocol closes, each with its check
    fieldkit ship train --doctor    git 2.38+, gh signed in, the mandate, the hook, the merge driver, generated files
    fieldkit ship ci-wait PR [--merge] [--max-wait MIN]
                                    one blocking call that returns when the PR head's CI is green or red (the train's
                                    verdict: every check run AND workflow run of the head, never gh's exit code); red
                                    prints the failed jobs' log tail. Run it in the background: one wake when CI ends,
                                    no sleep loops (O4, 2026-10-28: 103 hand-written CI and sleep loops and 210 gh
                                    status polls in 41 VAIO transcripts in one week). --merge: merge on green with
                                    --match-head-commit, then check the PR reads MERGED

Owner, 2026-10-26: "make sure via deterministic tools and checks that problems like these OR SIMILAR or CLOSELY
RELATED does not happen anymore". That day #126 was merged on green CI at 18:51 while its session still had a commit
in its pre-commit hook, and #124 went CONFLICTING twice as main moved, each time costing a local merge and a full suite.

Never: force-push, rebase, merge red, merge a head nobody declared, push outside the mandate's branches (the pushes
go through fieldkit ship push and the repository's pre-push gate). Every step is logged to the state file in the
clone's git folder (<git common dir>/fieldkit/train.json). Exit 0 done, 3 something refused or left (with why), 2 bad
input.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import emit
from . import mergeproof as mp
from . import strikes

LABEL = "fieldkit-ready"
MARK = re.compile(r"<!-- fieldkit-(ready|not-ready) (\{.*?\}) -->")
HOOK_FRESH_S = 2 * 3600            # a pre-commit marker younger than this is a commit still in the hook
OLD_MARK_S = 3600                  # a marker without its checkout (an older hook) counts only this long
STALE_H = 24

FAILURE_MODES = [
    {"name": "merged-before-final", "happened": "#126 merged on green CI at 18:51 while its session still had a commit "
     "in the pre-commit hook; that commit needed a PR of its own",
     "check": "ready(): refused while a commit is in the hook or anything is unpushed; the train merges only with "
              "--match-head-commit of the declared head (or a head the train made), and re-reads the declaration "
              "just before merging", "tool": "fieldkit ship ready / ship train"},
    {"name": "head-moved-after-ready", "happened": "a push after the declaration would ride along unreviewed",
     "check": "readiness(): head != declared and not made by the train -> not ready; gh refuses a head mismatch too",
     "tool": "fieldkit ship train"},
    {"name": "generated-conflict-on-github", "happened": "#124 CONFLICTING twice, only in tools.yaml and the generated "
     "platform_index.json; GitHub cannot run the local merge driver",
     "check": "predict(): conflicts only in generated/card files -> the train merges main here, regenerates "
              "(generated.yaml, fieldkit drift) and pushes before merging", "tool": "fieldkit ship train / ship update"},
    {"name": "suite-per-merge", "happened": "each merge of main ran the full pre-commit suite again (30-40 min)",
     "check": "mergeproof.hook_decide(): a proven merge of CI-green main into a tested tree runs only the affected "
              "tests (testcost records why); anything else the full suite", "tool": "hooks/pre-commit"},
    {"name": "stale-pr", "happened": "PRs open for days with no session behind them",
     "check": "survey(): not ready and no update for 24 h -> stale", "tool": "fieldkit ship train --dry-run"},
    {"name": "orphaned-by-rotation", "happened": "a session past its rotation line and gone leaves a PR nobody "
     "declared final", "check": "survey(): not ready and no live station (fieldkit zero lease) names its branch -> "
                                "orphan: never merged, a new owner declares it", "tool": "fieldkit ship train --dry-run"},
    {"name": "parallel-edit", "happened": "two PRs editing the same card or file in parallel conflict later",
     "check": "overlap(): open PRs' files and live stations' shared ground against this branch's files, before work "
              "and at PR open", "tool": "fieldkit ship overlap"},
    {"name": "merged-while-frozen", "happened": "main merged while the main checkout is frozen (elevated goal): "
     "it silently falls behind and its parity baseline is stale",
     "check": "after every merge, a frozen checkout gets an ff-pending row; unfreeze fast-forwards it and runs "
              "fieldkit parity baseline --set", "tool": "fieldkit ship freeze / unfreeze"},
    {"name": "stacked-pr-order", "happened": "zero-command set 2 (branched from feat/zero-net) had to wait for #126 "
     "and merge main again", "check": "order(): a PR whose base or history is another PR goes after it, and is "
                                      "retargeted to main once that one merged", "tool": "fieldkit ship train"},
    {"name": "merged-red", "happened": "(never yet) a merge whose CI failed or never ran",
     "check": "wait_green(): only check runs all completed green on the exact head (artefact, not exit code)",
     "tool": "fieldkit ship train"},
    {"name": "merged-while-rerunning", "happened": "#124 (07a5791) was merged by `gh run rerun --failed` then "
     "`gh pr checks --watch --fail-fast && gh pr merge` while the windows re-run was still running",
     "check": "ci_verdict(): every workflow run of the head sha completed, not only its check runs, before green; "
              "never an exit code of gh pr checks", "tool": "fieldkit ship train"},
]


def _git(repo, *a, check=False):
    return mp._git(repo, *a, check=check)


def _ancestor(repo, a, b):
    return bool(a and b) and _git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# -- GitHub, through gh (a fake in the tests) --------------------------------------------------------------------------
class GH:
    FIELDS = "number,title,headRefName,baseRefName,headRefOid,labels,isDraft,updatedAt,state"

    def __init__(self, repo, run=subprocess.run):
        self.repo, self.run = str(repo), run
        from ..core import sync
        self.slug = sync._slug(repo)

    def _gh(self, *args, timeout=120):
        from .ship import ship_env                    # inside an autopilot run: ship's own credential (inbox #145)
        r = self.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                     cwd=self.repo, timeout=timeout, env=ship_env())
        return r.returncode, (r.stdout or ""), (r.stderr or "")

    def _json(self, *args):
        rc, out, err = self._gh(*args)
        if rc:
            raise RuntimeError(f"gh {' '.join(args[:3])}: {err.strip()[-200:]}")
        return json.loads(out or "null")

    def prs(self):
        return self._json("pr", "list", "-R", self.slug, "--state", "open", "--limit", "100", "--json", self.FIELDS)

    def pr(self, n):
        return self._json("pr", "view", str(n), "-R", self.slug, "--json", self.FIELDS)

    def markers(self, n):
        bodies = self._json("api", "--paginate", f"repos/{self.slug}/issues/{n}/comments", "--jq", "[.[].body]")
        return parse_markers(bodies if isinstance(bodies, list) else [])

    def comment(self, n, body):
        rc, _, err = self._gh("pr", "comment", str(n), "-R", self.slug, "--body", body)
        return rc == 0, err.strip()[-200:]

    def label(self, n, add=True):
        if add:
            self._gh("label", "create", LABEL, "-R", self.slug, "--color", "0E8A16",
                     "--description", "fieldkit ship ready: the session declared this PR final")
        rc, _, err = self._gh("pr", "edit", str(n), "-R", self.slug, "--add-label" if add else "--remove-label", LABEL)
        return rc == 0, err.strip()[-200:]

    def update_branch(self, n, head):
        rc, out, err = self._gh("api", "-X", "PUT", f"repos/{self.slug}/pulls/{n}/update-branch",
                                "-f", f"expected_head_sha={head}")
        return rc == 0, (out + err).strip()[-300:]

    def ci_state(self, sha):
        """-> ("green" | "pending" | "red" | "none", why) from GitHub's check runs for exactly `sha`."""
        rc, out, err = self._gh("api", f"repos/{self.slug}/commits/{sha}/check-runs", "--jq",
                                "[.check_runs[] | {name, status, conclusion}]")
        if rc:
            return "none", f"CI unreadable (gh): {err.strip()[:160]}"
        # the workflow runs too: a re-run puts its run back to queued/in_progress while the old check runs still read
        # green or red (2026-10-26: #124 merged while its windows re-run was still running)
        rc2, out2, err2 = self._gh("api", f"repos/{self.slug}/actions/runs?head_sha={sha}", "--jq",
                                   "[.workflow_runs[] | {name, status, conclusion}]")
        if rc2:
            return "none", f"workflow runs unreadable (gh): {err2.strip()[:160]}"
        return ci_verdict(json.loads(out or "[]"), sha, workflows=json.loads(out2 or "[]"), slug=self.slug)

    def wait_checks(self, n, timeout):
        try:
            self._gh("pr", "checks", str(n), "-R", self.slug, "--watch", "--fail-fast", "--interval", "30",
                     timeout=timeout)
        except subprocess.TimeoutExpired:
            pass

    def failed_log(self, sha, lines=30):
        """The last `lines` lines of `gh run view --log-failed` for each failed workflow run of `sha`."""
        rc, out, _ = self._gh("api", f"repos/{self.slug}/actions/runs?head_sha={sha}", "--jq",
                              "[.workflow_runs[] | select(.conclusion == \"failure\") | .id]")
        out_lines = []
        for rid in (json.loads(out or "[]") if rc == 0 else [])[:3]:
            rc2, log, _ = self._gh("run", "view", str(rid), "-R", self.slug, "--log-failed", timeout=300)
            out_lines += [f"-- run {rid}"] + (log.splitlines()[-lines:] if rc2 == 0 else ["(log unreadable)"])
        return out_lines

    def failed_checks(self, sha):
        """-> the names of the check runs of `sha` that failed (empty when unreadable)."""
        rc, out, _ = self._gh("api", f"repos/{self.slug}/commits/{sha}/check-runs", "--jq",
                              "[.check_runs[] | select(.conclusion == \"failure\") | .name]")
        return set(json.loads(out or "[]")) if rc == 0 else set()

    def base_head(self, base="main"):
        """-> the head commit of `base` on GitHub, or ""."""
        rc, out, _ = self._gh("api", f"repos/{self.slug}/commits/{base}", "--jq", ".sha")
        return out.strip() if rc == 0 else ""

    def merge(self, n, head):
        rc, out, err = self._gh("pr", "merge", str(n), "-R", self.slug, "--merge", "--match-head-commit", head)
        return rc == 0, (out + err).strip()[-300:]

    def files(self, n):
        return [f["path"] for f in (self._json("pr", "view", str(n), "-R", self.slug, "--json", "files")
                                    or {}).get("files") or []]

    def retarget(self, n, base):
        rc, _, err = self._gh("pr", "edit", str(n), "-R", self.slug, "--base", base)
        return rc == 0, err.strip()[-200:]


NEED_OS = ("windows", "ubuntu")


def ci_verdict(runs, sha, need=NEED_OS, workflows=(), slug=""):
    if not runs:
        return "none", f"no CI run for {sha[:10]} yet"
    rerun = [w["name"] for w in workflows or () if w.get("status") != "completed"]
    if rerun:
        return "pending", f"workflow run(s) not finished for {sha[:10]} (a re-run?): {', '.join(rerun[:3])}"
    red = [x["name"] for x in runs if x["status"] == "completed" and x["conclusion"] not in ("success", "skipped",
                                                                                              "neutral")]
    if red:
        return "red", f"CI failed for {sha[:10]}: {', '.join(red[:4])}"
    if any(x["status"] != "completed" for x in runs):
        return "pending", f"CI running for {sha[:10]}"
    from ..core import sync                          # P14 (I-11): a repository's own job names, as data
    missing = sync.missing_os(runs, need, slug)
    if missing:
        return "pending", f"CI for {sha[:10]} has no {', '.join(missing)} job yet"
    return "green", f"CI green for {sha[:10]} ({len(runs)} check runs)"


def parse_markers(bodies):
    out = []
    for b in bodies:
        for kind, data in MARK.findall(b or ""):
            try:
                out.append(dict(json.loads(data), kind=kind))
            except ValueError:
                continue
    return out


def marker(kind, **data):
    data = dict(data, at=_now())
    head = f"fieldkit ship ready: {data['sha']}" if kind == "ready" else \
        f"fieldkit ship ready: NOT final ({data.get('why', 'withdrawn')})"
    return f"{head}\n\n<!-- fieldkit-{kind} {json.dumps(data, sort_keys=True)} -->"


# -- the session's declaration -----------------------------------------------------------------------------------------
def hook_running(repo, now=None):
    """Commits still inside the pre-commit hook in this clone (hooks/pre-commit writes the marker)."""
    now = now or time.time()
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    out = []
    for p in mp.common_dir(repo).glob("pre-commit.running.*"):
        age = now - p.stat().st_mtime
        parts = p.read_text(encoding="utf-8", errors="replace").strip().split(" ", 2)
        if len(parts) == 3:                              # date time checkout: only this checkout's hook counts
            if parts[2] == top and age < HOOK_FRESH_S:
                out.append(p.name)
        elif age < OLD_MARK_S:                          # an older hook wrote no checkout: any clone-wide, while young
            out.append(p.name)
    return out


def ready(repo, n, gh, pending=None, withdraw=False):
    pr = gh.pr(n)
    rep = {"pr": n, "branch": pr["headRefName"], "head": pr["headRefOid"]}
    if pr.get("state", "OPEN") != "OPEN":
        return dict(rep, ok=False, state="not-open", next="only an open PR can be declared")
    if pending is not None or withdraw:
        why = pending or "withdrawn"
        gh.comment(n, marker("not-ready", sha=pr["headRefOid"], why=why, by=_who()))
        gh.label(n, add=False)
        return dict(rep, ok=True, state="not-final", why=why, next="declare it again when it is final: "
                                                                    f"fieldkit ship ready {n}")
    branch = _git(repo, "branch", "--show-current").stdout.strip()
    if branch != pr["headRefName"]:
        return dict(rep, ok=False, state="wrong-checkout",
                    next=f"declare it from the session's own checkout of {pr['headRefName']} (here: {branch or '-'})")
    head = mp.rev(repo, "HEAD")
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    problems = []
    if head != pr["headRefOid"]:
        problems.append(f"HEAD {head[:10]} is not the PR head {pr['headRefOid'][:10]}: push first (fieldkit ship push)")
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        problems.append("uncommitted changes")
    if (gitdir / "MERGE_HEAD").exists():
        problems.append("a merge is in progress")
    busy = hook_running(repo)
    if busy:
        problems.append(f"a commit is still in the pre-commit hook ({', '.join(busy)}): wait for it "
                        "(hooks/wait-for-hook.sh), push it, then declare")
    if problems:
        return dict(rep, ok=False, state="not-final", error="; ".join(problems), next="nothing was declared")
    ok, err = gh.comment(n, marker("ready", sha=head, by=_who()))
    if not ok:
        return dict(rep, ok=False, state="refused", error=err, next="the declaration was not written")
    gh.label(n, add=True)
    return dict(rep, ok=True, state="declared", next=f"the train merges exactly {head[:10]}; any later push "
                                                     "withdraws it until you declare again")


def _who():
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("FIELDKIT_STATION") or \
        os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"


def readiness(pr, marks, made=()):
    """-> (ready, declared sha or None, why). `made`: heads the train itself produced from the declared head."""
    labels = [x.get("name") for x in pr.get("labels") or []]
    last = marks[-1] if marks else None
    if pr.get("isDraft"):
        return False, None, "draft"
    if last and last["kind"] != "ready":
        return False, None, f"pending: {last.get('why', '')}"
    if not last or LABEL not in labels:
        return False, None, "not declared final (fieldkit ship ready)"
    declared, head = last.get("sha"), pr["headRefOid"]
    if head == declared:
        return True, declared, f"declared {declared[:10]}"
    if head in made:
        return True, declared, f"declared {declared[:10]}; brought up to date by the train ({head[:10]})"
    return False, declared, (f"head moved after the declaration ({declared[:10]} -> {head[:10]}): the session "
                             "declares again")


# -- order and what each PR needs --------------------------------------------------------------------------------------
def order(repo, prs, base="main"):
    """Dependencies first, then by number: B after A when B's base is A's branch, or A's head (not on main) is in B's
    history. -> (ordered list, {number: [numbers it waits for]})."""
    base_ref = mp.rev(repo, f"refs/remotes/origin/{base}")
    by = {p["number"]: p for p in prs}
    deps = {n: set() for n in by}
    for a in by.values():
        for b in by.values():
            if a is b:
                continue
            if b.get("baseRefName") == a["headRefName"] or (
                    a["headRefOid"] != b["headRefOid"] and _ancestor(repo, a["headRefOid"], b["headRefOid"])
                    and not _ancestor(repo, a["headRefOid"], base_ref)):
                deps[b["number"]].add(a["number"])
    out, left = [], dict(deps)
    while left:
        free = sorted(n for n, d in left.items() if not (d & set(left)))
        if not free:                                    # a cycle: equal heads; keep the number order, said in `why`
            free = [min(left)]
        out.append(by[free[0]])
        left.pop(free[0])
    return out, {n: sorted(d) for n, d in deps.items()}


def predict(repo, head, base="main", gens=None):
    """-> (need, paths): up-to-date | update-api (GitHub merges it) | update-local (generated/card files: merged here)
    | conflict (a real one: the session resolves it)."""
    base_ref = mp.rev(repo, f"refs/remotes/origin/{base}")
    if _ancestor(repo, base_ref, head):
        return "up-to-date", []
    gens = mp.generators(repo) if gens is None else gens
    special = set(gens) | set(mp.DRIVER_FILES)
    r = _git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", base_ref, head)
    if r.returncode not in (0, 1):
        return "conflict", [f"git merge-tree failed: {r.stderr.strip()[:200]}"]
    paths = [x for x in r.stdout.splitlines()[1:] if x.strip()]
    if r.returncode == 1:
        return ("update-local" if set(paths) <= special else "conflict"), paths
    mb = _git(repo, "merge-base", base_ref, head).stdout.strip()
    both = set(_git(repo, "diff", "--name-only", mb, base_ref).stdout.split()) & \
        set(_git(repo, "diff", "--name-only", mb, head).stdout.split())
    if both & set(gens):                     # a text merge of a generated file can be clean and still wrong
        return "update-local", sorted(both & set(gens))
    return "update-api", []


# -- the other failure modes -------------------------------------------------------------------------------------------
def live_stations(now=None):
    """[(station, sitrep)] whose fieldkit zero lease is alive; None when no net file exists (liveness unknown)."""
    try:
        from ..zero import net
        p = net.coord() / "net-stations.json"
        if not p.is_file():
            return None
        st = net.load_stations()["stations"]
    except Exception:  # noqa: BLE001
        return None
    now = now or time.time()
    return [(s, r.get("sitrep") or {}) for s, r in st.items() if not r.get("off") and r.get("lease_until", 0) > now]


def _age_h(iso, now=None):
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (TypeError, ValueError):
        return None
    return ((now or time.time()) - t) / 3600


def survey_flags(pr, is_ready, stations, now=None):
    """stale / orphan for a PR nobody declared final."""
    if is_ready:
        return []
    flags = []
    age = _age_h(pr.get("updatedAt"), now)
    if age is not None and age > STALE_H:
        flags.append(f"stale: no update for {age:.0f} h")
    if stations is not None and not any(pr["headRefName"] in json.dumps(s, ensure_ascii=False) for _, s in stations):
        flags.append("orphan: no live station names its branch (fieldkit zero): a new owner declares it")
    return flags


def overlap(repo, gh, base="main", files=None, prs=None, stations=None):
    """Open PRs and live stations' shared ground touching the same files as this work -> [{who, files}]."""
    if files is None:
        _git(repo, "fetch", "--quiet", "origin", base)
        files = _git(repo, "diff", "--name-only", f"refs/remotes/origin/{base}...HEAD").stdout.split()
    gens = mp.generators(repo)
    mine = {f for f in files if f not in gens and f not in mp.DRIVER_FILES}
    me = _git(repo, "branch", "--show-current").stdout.strip()
    out = []
    for pr in (gh.prs() if prs is None else prs):
        if pr["headRefName"] == me:
            continue
        hit = sorted(mine & set(gh.files(pr["number"])))
        if hit:
            out.append({"who": f"PR #{pr['number']} ({pr['headRefName']})", "files": hit})
    for s, rep in (live_stations() if stations is None else stations) or []:
        text = str(rep.get("shared") or "")
        hit = sorted(f for f in mine if f in text or Path(f).name in text.split())
        if hit and me not in str(rep.get("branch") or ""):
            out.append({"who": f"station {s}", "files": hit})
    return out


# -- frozen checkouts --------------------------------------------------------------------------------------------------
def _frozen_file(repo):
    return mp.state_dir(repo) / "frozen.json"


def main_checkout(repo):
    line = _git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
    return line[0].split(" ", 1)[1] if line and line[0].startswith("worktree ") else str(repo)


def freeze(repo, reason, checkout=None):
    f = _frozen_file(repo)
    rows = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
    co = str(Path(checkout or main_checkout(repo)))
    rows = [r for r in rows if r["checkout"] != co] + [{"checkout": co, "reason": reason, "since": _now(),
                                                         "head": mp.rev(co, "HEAD")}]
    f.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return {"ok": True, "state": "frozen", "checkout": co, "next": "the train records ff-pending for it; "
                                                                   "fieldkit ship unfreeze when the goal ended"}


def frozen(repo):
    f = _frozen_file(repo)
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []


def unfreeze(repo, base="main", checkout=None, run=subprocess.run):
    rows = frozen(repo)
    co = str(Path(checkout or main_checkout(repo)))
    if not any(r["checkout"] == co for r in rows):
        return {"ok": False, "state": "not-frozen", "checkout": co, "next": "nothing to do"}
    _git(co, "fetch", "--quiet", "origin", base)
    rep = {"checkout": co}
    if _git(co, "branch", "--show-current").stdout.strip() != base or \
            _git(co, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        return dict(rep, ok=False, state="not-on-clean-base", next=f"the checkout is not a clean {base}: look first")
    m = _git(co, "merge", "--ff-only", f"refs/remotes/origin/{base}")
    if m.returncode:
        return dict(rep, ok=False, state="not-fast-forward", error=m.stderr.strip()[-200:], next="nothing moved")
    p = run([sys.executable, "-m", "fieldkit", "parity", "baseline", "--set"], cwd=co, capture_output=True, text=True)
    _frozen_file(repo).write_text(json.dumps([r for r in rows if r["checkout"] != co], indent=1), encoding="utf-8")
    st = load_state(repo)
    st["ff_pending"] = [x for x in st.get("ff_pending", []) if x["checkout"] != co]
    save_state(repo, st)
    return dict(rep, ok=p.returncode == 0, state="unfrozen", head=mp.rev(co, "HEAD"),
                parity=(p.stdout or p.stderr or "").strip()[-200:],
                next="" if p.returncode == 0 else "fieldkit parity baseline --set failed: run it by hand")


# -- state -------------------------------------------------------------------------------------------------------------
def _state_file(repo):
    return mp.state_dir(repo) / "train.json"


def load_state(repo):
    f = _state_file(repo)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except ValueError:
        return {}


def save_state(repo, st):
    f = _state_file(repo)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def _log(repo, st, n, what, **kw):
    row = dict({"t": _now(), "pr": n, "what": what}, **kw)
    st.setdefault("log", []).append(row)
    save_state(repo, st)
    return row


# -- the train ---------------------------------------------------------------------------------------------------------
def _may_update(repo, branch):
    """Pushing to `branch` (an update of the PR from main) is inside the mandate's push patterns -> (ok, why)."""
    try:
        from ..mandate import gate, mandate as M, proofs
        name, _ = proofs.where(repo)
        p, m, cfg = gate._mandate_for(name)
        if p is None:
            return False, f"no mandate: {cfg}"
        return M.branch_ok(cfg, branch)
    except Exception as e:  # noqa: BLE001 - a broken mandate never opens anything
        return False, f"the mandate could not be read ({e})"


def _mandate(action, **kw):
    from .ship import _mandate as m
    return m(action, **kw)


def clone_for(repo):
    """The train's own clone (in the clone's git folder): there a PR branch can be checked out even while the
    session's worktree has it. Pushes go through its hooks (core.hooksPath hooks) and fieldkit ship push."""
    work = mp.state_dir(repo) / "train-clone"
    url = _git(repo, "remote", "get-url", "origin").stdout.strip()
    if not (work / ".git").is_dir():
        r = subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(mp.common_dir(repo)), str(work)],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"git clone: {r.stderr.strip()[-200:]}")
    _git(work, "remote", "set-url", "origin", url, check=True)
    hooks = _git(repo, "config", "--get", "core.hooksPath").stdout.strip()
    if hooks:
        _git(work, "config", "core.hooksPath", hooks)
    for t in _git(repo, "config", "--get-all", "fieldkit.trailer").stdout.splitlines():
        _git(work, "config", "--add", "fieldkit.trailer", t)
    _git(work, "fetch", "--quiet", "origin", check=True)
    return work


def _local_update(repo, branch, base, gens, ci, push):
    work = clone_for(repo)
    if _git(work, "status", "--porcelain").stdout.strip() or (Path(work) / ".git" / "MERGE_HEAD").exists():
        _git(work, "merge", "--abort")
        _git(work, "reset", "--hard", "--quiet")       # the train's own clone: a crash left it half-merged
    _git(work, "checkout", "--quiet", "-B", branch, f"refs/remotes/origin/{branch}", check=True)
    r = mp.update(work, base, gens=gens, ci=ci, fetch=False)
    if not r.get("ok") or r["state"] == "up-to-date":
        return r
    pr_ = push(work, base)
    if not pr_.get("ok") or not pr_.get("pushed"):
        return dict(r, ok=False, state="push-refused", error=pr_.get("error") or pr_.get("state"),
                    next=pr_.get("next", "nothing was forced"))
    return dict(r, head=mp.rev(work, "HEAD"))


def wait_green(gh, n, head, max_wait_s, sleep=time.sleep, clock=time.monotonic):
    end = clock() + max_wait_s
    while True:
        state, why = gh.ci_state(head)
        if state in ("green", "red") or clock() >= end:
            return state, why
        gh.wait_checks(n, timeout=max(60, min(1800, end - clock())))
        state, why = gh.ci_state(head)
        if state in ("green", "red") or clock() >= end:
            return state, why
        sleep(30)


def _mains_failure(gh, head, log, base):
    """-> the evidence that the red of `head` is main's own (strikes.mains_failure), "" when it is the branch's or
    main's head cannot be read (fail closed: an unreadable main counts the strike)."""
    try:
        base_sha = gh.base_head(base)
        if not base_sha or base_sha == head:
            return ""
        main_state, _ = gh.ci_state(base_sha)
        if main_state != "red":
            return ""
        return strikes.mains_failure(log, gh.failed_log(base_sha), gh.failed_checks(head), gh.failed_checks(base_sha))
    except Exception:  # noqa: BLE001 - a fake or partial GH: the strike counts
        return ""


def ci_wait(gh, n, max_wait_s=3600, merge=False, sleep=time.sleep, clock=time.monotonic, inbox=None):
    """O4: wait once for the PR head's CI verdict; on red the failed log tail, on green (with merge) the merge proven by
    the PR's state, not gh's exit code. TWO STRIKES: the second failed round parks the branch (state "parked"); a
    parked branch is not waited on at all."""
    pr = gh.pr(n)
    head = pr["headRefOid"]
    r = {"pr": n, "branch": pr["headRefName"], "head": head}
    if pr.get("state") != "OPEN":
        return dict(r, ok=pr.get("state") == "MERGED", state=pr.get("state", "?").lower(), why="the PR is not open")
    if strikes.parked_for(r["branch"]):
        return dict(r, ok=False, state="parked", why=strikes.refusal([r["branch"]]),
                    next="parked, go on with the plan")
    state, why = wait_green(gh, n, head, max_wait_s, sleep, clock)
    r.update(state=state, why=why)
    if state == "red":
        log = gh.failed_log(head)
        mains = _mains_failure(gh, head, log, pr.get("baseRefName") or "main")
        if mains:                                       # B2: not this branch's strike; main is fixed first
            return dict(r, ok=False, state="main-red", log=log, why=f"main's failure, not counted: {mains}",
                        next="fix main first (its own PR), then update this branch and run ci-wait again")
        fl = strikes.flake(log)
        if fl and not strikes.record_flake(r["branch"], head, fl, pr=n)["counted"]:   # owner 2026-10-31: not a strike
            return dict(r, ok=False, state="flake", log=log, why=f"a timing flake, not counted: {fl}",
                        next=f"re-run the failed jobs once (gh run rerun RUN_ID --failed), then ci-wait {n} again")
        s = strikes.record_failure(r["branch"], head, " ".join(log[-2:]) or why, pr=n, inbox=inbox)
        if s["parked"]:
            return dict(r, ok=False, state="parked", log=log, why=f"failed CI {s['rounds']} times: two strikes",
                        next="parked, go on with the plan")
        return dict(r, ok=False, log=log, next="fix it on the branch, push it, then run ci-wait again "
                                               "(one strike left)")
    if state != "green":
        return dict(r, ok=False, state="timeout" if state == "pending" else state,
                    next="CI did not finish in time; run ci-wait again in the background")
    if not merge:
        return dict(r, ok=True, next=f"merge: fieldkit ship ci-wait {n} --merge")
    ok, msg = gh.merge(n, head)
    after = gh.pr(n).get("state")
    if not ok or after != "MERGED":                     # the artefact, not the exit code
        return dict(r, ok=False, state="not-merged", why=msg or f"the PR reads {after}",
                    next="read the PR; nothing was forced")
    return dict(r, ok=True, state="merged", next=f"#{n} is merged at {head[:10]}")


def _ci_wait_lines(d):
    out = [f"#{d['pr']} {d['branch']} {d['head'][:10]}: {d['state'].upper()} - {d.get('why', '')}"]
    return out + d.get("log", [])


def plan(repo, gh, base="main", only=None, gens=None, stations=None, made=None):
    _git(repo, "fetch", "--quiet", "origin")
    prs = [p for p in gh.prs() if not only or p["number"] in only]
    for p in prs:                                       # the heads may be on forks or not fetched yet
        if not mp.rev(repo, p["headRefOid"]):
            _git(repo, "fetch", "--quiet", "origin", f"pull/{p['number']}/head")
    ordered, deps = order(repo, prs, base)
    stations = live_stations() if stations is None else stations
    rows = []
    for p in ordered:
        marks = gh.markers(p["number"])
        ok, declared, why = readiness(p, marks, (made or {}).get(str(p["number"]), ()))
        need, paths = predict(repo, p["headRefOid"], base, gens)
        rows.append({"pr": p["number"], "branch": p["headRefName"], "base": p.get("baseRefName"), "ready": ok,
                     "declared": declared, "head": p["headRefOid"], "why": why, "after": deps[p["number"]],
                     "need": need, "paths": paths, "flags": survey_flags(p, ok, stations)})
    return rows


def train(repo=".", gh=None, base="main", dry_run=False, only=None, max_wait_s=3600, gens=None, ci=None,
          mandate=None, may_update=None, push=None, sleep=time.sleep, stations=None):
    gh = gh or GH(repo)
    mandate = mandate or _mandate
    may_update = may_update or _may_update
    if push is None:
        from .ship import push as push
    st = load_state(repo)
    made = st.setdefault("made", {})
    rows = plan(repo, gh, base, only, gens, stations, made)
    if dry_run:
        return {"ok": True, "state": "dry-run", "order": rows, "frozen": frozen(repo),
                "ff_pending": st.get("ff_pending", []),
                "next": "nothing was changed; fieldkit ship train merges the ready ones in this order"}
    st["run"] = {"started": _now(), "order": [r["pr"] for r in rows]}
    save_state(repo, st)
    done, left = [], []
    merged = set(st.get("merged", []))
    for row in rows:
        n = row["pr"]
        if not row["ready"]:
            left.append({"pr": n, "why": row["why"]})
            continue
        waits = [d for d in row["after"] if d not in merged]
        if waits:
            left.append({"pr": n, "why": f"waits for #{', #'.join(map(str, waits))}"})
            _log(repo, st, n, "waits", on=waits)
            continue
        r = _one(repo, gh, row, base, st, max_wait_s, gens, ci, mandate, may_update, push, sleep)
        (done if r.get("merged") else left).append(r)
        if r.get("merged"):
            merged.add(n)
            st["merged"] = sorted(merged)
            save_state(repo, st)
    return {"ok": not left, "state": "done" if not left else "left", "merged": done, "left": left,
            "ff_pending": st.get("ff_pending", []),
            "next": "" if not left else "each left PR says why; nothing was forced"}


def _one(repo, gh, row, base, st, max_wait_s, gens, ci, mandate, may_update, push, sleep):
    n, branch = row["pr"], row["branch"]
    made = st.setdefault("made", {}).setdefault(str(n), [])
    _log(repo, st, n, "start", head=row["head"], declared=row["declared"])
    if row.get("base") and row["base"] != base:
        ok, err = gh.retarget(n, base)
        _log(repo, st, n, "retarget", ok=ok, error=err)
        if not ok:
            return {"pr": n, "why": f"retarget to {base} refused: {err}"}
    pr = gh.pr(n)
    _git(repo, "fetch", "--quiet", "origin")
    need, paths = predict(repo, pr["headRefOid"], base, gens)
    head = pr["headRefOid"]
    if need == "conflict":
        _log(repo, st, n, "conflict", paths=paths)
        return {"pr": n, "why": "a real conflict with main: the session resolves it (" + ", ".join(paths[:5]) + ")"}
    if need in ("update-api", "update-local"):
        ok, why = may_update(repo, branch)
        if not ok:
            _log(repo, st, n, "refused", why=why)
            return {"pr": n, "why": f"bringing {branch} up to date is outside the mandate: {why}"}
        if need == "update-api":
            ok, msg = gh.update_branch(n, head)
            _log(repo, st, n, "update-api", ok=ok, msg=msg)
            if not ok:
                need = "update-local"                   # GitHub saw a conflict merge-tree did not: try here
        if need == "update-local":
            r = _local_update(repo, branch, base, gens, ci, push)
            _log(repo, st, n, "update-local", state=r.get("state"), proof=r.get("proof"), error=r.get("error"))
            if not r.get("ok"):
                return {"pr": n, "why": f"update here: {r.get('state')}: {r.get('error') or r.get('paths') or ''}"}
        pr = gh.pr(n)
        if pr["headRefOid"] != head:
            made.append(pr["headRefOid"])
            head = pr["headRefOid"]
            save_state(repo, st)
    state, why = wait_green(gh, n, head, max_wait_s, sleep)
    _log(repo, st, n, "ci", state=state, why=why)
    if state != "green":
        return {"pr": n, "why": f"never merged {state}: {why}"}
    pr = gh.pr(n)                                      # the declaration is read again just before the merge
    ok, declared, why = readiness(pr, gh.markers(n), made)
    if not ok or pr["headRefOid"] != head:
        _log(repo, st, n, "not-ready", why=why)
        return {"pr": n, "why": f"not merged: {why}"}
    verdict, mwhy, mpath = mandate("merge_main", repo_path=repo, commit=head)
    if verdict != "allow":
        _log(repo, st, n, "refused", why=mwhy)
        return {"pr": n, "why": f"the mandate does not allow the merge: {mwhy}"}
    ok, msg = gh.merge(n, head)
    _log(repo, st, n, "merge", ok=ok, msg=msg, head=head)
    if not ok:
        return {"pr": n, "why": f"gh refused the merge: {msg}"}
    _git(repo, "fetch", "--quiet", "origin", base)
    if not _ancestor(repo, head, mp.rev(repo, f"refs/remotes/origin/{base}")):
        return {"pr": n, "why": f"{head[:10]} is not on origin/{base} after the merge"}
    if mpath is not None:
        from .ship import _mandate_log
        _mandate_log(mpath, "merge_main", mwhy, branch=branch, commit=head, how="pull-request (merge train)")
    to = mp.rev(repo, f"refs/remotes/origin/{base}")
    for fr in frozen(repo):
        st.setdefault("ff_pending", []).append({"checkout": fr["checkout"], "to": to, "pr": n, "t": _now()})
        _log(repo, st, n, "ff-pending", checkout=fr["checkout"])
    return {"pr": n, "merged": True, "head": head, "main": to}


# -- doctor ------------------------------------------------------------------------------------------------------------
def doctor(repo=".", run=subprocess.run):
    """Everything the train needs here -> [(ok, what, why)]."""
    rows = []
    v = _git(repo, "version").stdout.strip()
    nums = [int(x) for x in re.findall(r"\d+", v)[:2]] or [0, 0]
    rows.append((nums >= [2, 38], "git merge-tree --write-tree (2.38+)", v))
    try:
        r = run(["gh", "auth", "status"], capture_output=True, text=True, timeout=30)
        rows.append((r.returncode == 0, "gh signed in", (r.stdout + r.stderr).strip().splitlines()[0:1] or ["-"]))
    except (OSError, subprocess.TimeoutExpired) as e:
        rows.append((False, "gh signed in", str(e)))
    ok, why = _may_update(repo, "feat/x")
    rows.append((ok, "mandate lets the train update feat/ branches", why))
    hp = _git(repo, "config", "--get", "core.hooksPath").stdout.strip()
    rows.append((bool(hp), "the pre-commit hook is on (core.hooksPath)", hp or "not set: git config core.hooksPath hooks"))
    drv = Path(repo) / mp.DRIVER
    if drv.is_file():
        r = run([sys.executable, str(drv), "check"], capture_output=True, text=True, cwd=str(repo))
        rows.append((r.returncode == 0, "card merge driver installed", (r.stdout or r.stderr).strip()[:160]))
    for path, argv in mp.generators(repo).items():
        ok, why = mp.gen_matches(repo, path, argv)
        rows.append((ok, f"{path} equals its generator", why))
    for f in frozen(repo):
        rows.append((True, f"frozen: {f['checkout']}", f"{f['reason']} since {f['since']}"))
    for f in load_state(repo).get("ff_pending", []):
        rows.append((False, f"ff pending: {f['checkout']}", f"to {f['to'][:10]}: fieldkit ship unfreeze when it may move"))
    return [(o, w, " ".join(y) if isinstance(y, list) else y) for o, w, y in rows]


# -- the command line --------------------------------------------------------------------------------------------------
def _plan_lines(d):
    out = [f"MERGE TRAIN ({d['state']}): {sum(r['ready'] for r in d.get('order', []))} of "
           f"{len(d.get('order', []))} open PR(s) declared final; the order it merges in:"]
    for i, r in enumerate(d.get("order", []), 1):
        out.append(f"  {i}. #{r['pr']} {r['branch']}  {'READY' if r['ready'] else 'not ready'}: {r['why']}")
        out.append(f"       main: {r['need']}" + (f" ({', '.join(r['paths'][:6])})" if r["paths"] else "")
                   + (f"; after #{', #'.join(map(str, r['after']))}" if r["after"] else ""))
        out += [f"       FLAG {f}" for f in r["flags"]]
    out += [f"  FROZEN {f['checkout']} since {f['since']}: {f['reason']}" for f in d.get("frozen", [])]
    out += [f"  FF PENDING {f['checkout']} -> {f['to'][:10]} (after #{f['pr']})" for f in d.get("ff_pending", [])]
    return out


def _run_lines(d):
    out = [f"MERGE TRAIN {d['state'].upper()}: {len(d['merged'])} merged, {len(d['left'])} left"]
    out += [f"  MERGED #{r['pr']} {r['head'][:10]} -> main {r['main'][:10]}" for r in d["merged"]]
    out += [f"  LEFT   #{r['pr']}: {r['why']}" for r in d["left"]]
    out += [f"  FF PENDING {f['checkout']} -> {f['to'][:10]}" for f in d.get("ff_pending", [])]
    return out


def main(argv=None, prog="fieldkit ship"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["ready", "train", "overlap", "freeze", "unfreeze", "ci-wait"])
    ap.add_argument("pr", nargs="?", type=int, help="ready, ci-wait: the PR number")
    ap.add_argument("--merge", action="store_true", help="ci-wait: merge on green (--match-head-commit)")
    ap.add_argument("--pending", help="ready: one more commit is coming (the train will not merge it)")
    ap.add_argument("--withdraw", action="store_true", help="ready: not final any more")
    ap.add_argument("--dry-run", action="store_true", help="train: read only, show the order")
    ap.add_argument("--modes", action="store_true", help="train: the failure modes and their checks")
    ap.add_argument("--doctor", action="store_true", help="train: what the train needs on this machine")
    ap.add_argument("--only", type=int, nargs="*", help="train: only these PR numbers")
    ap.add_argument("--max-wait", type=int, default=60, help="train, ci-wait: minutes to wait for CI per PR (60)")
    ap.add_argument("--files", nargs="*", help="overlap: these files instead of the branch's diff against the base")
    ap.add_argument("--reason", help="freeze: why the checkout must not move")
    ap.add_argument("--checkout", help="freeze/unfreeze: the checkout (default: the clone's main worktree)")
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "train" and a.doctor:
            rows = doctor(a.repo)
            bad = [r for r in rows if not r[0]]
            emit({"ok": not bad, "rows": [{"ok": o, "what": w, "why": y} for o, w, y in rows]}, a.json,
                 lambda d: [f"{'ok   ' if r['ok'] else 'NEEDS'} {r['what']}: {r['why']}" for r in d["rows"]])
            return 0 if not bad else 3
        if a.action == "train" and a.modes:
            emit({"modes": FAILURE_MODES}, a.json, lambda d: [x for m in d["modes"] for x in (
                f"{m['name']}: {m['happened']}", f"    check: {m['check']} [{m['tool']}]")])
            return 0
        if a.action == "ready":
            if not a.pr:
                ap.error("ready needs the PR number: fieldkit ship ready 129")
            r = ready(a.repo, a.pr, GH(a.repo), pending=a.pending, withdraw=a.withdraw)
            emit(r, a.json, lambda d: [f"#{d['pr']} {d['branch']}: {d['state']}"]
                 + ([f"error: {d['error']}"] if d.get("error") else []))
            return 0 if r["ok"] else 3
        if a.action == "ci-wait":
            if not a.pr:
                ap.error("ci-wait needs the PR number: fieldkit ship ci-wait 166 [--merge]")
            r = ci_wait(GH(a.repo), a.pr, a.max_wait * 60, merge=a.merge)
            emit(r, a.json, _ci_wait_lines)
            return 0 if r["ok"] else strikes.PARKED_EXIT if r["state"] == "parked" else 3
        if a.action == "train":
            r = train(a.repo, base=a.base, dry_run=a.dry_run, only=a.only, max_wait_s=a.max_wait * 60)
            emit(r, a.json, _plan_lines if a.dry_run else _run_lines)
            return 0 if r["ok"] else 3
        if a.action == "overlap":
            hits = overlap(a.repo, GH(a.repo), a.base, a.files)
            r = {"ok": not hits, "overlap": hits,
                 "next": "agree who goes first (the later branch merges main after the first lands)" if hits else ""}
            emit(r, a.json, lambda d: [f"OVERLAP {h['who']}: {', '.join(h['files'])}" for h in d["overlap"]]
                 or ["no open PR or live station touches these files"])
            return 0 if r["ok"] else 3
        if a.action == "freeze":
            if not a.reason:
                ap.error("freeze needs --reason")
            r = freeze(a.repo, a.reason, a.checkout)
        else:
            r = unfreeze(a.repo, a.base, a.checkout)
        emit(r, a.json, lambda d: [f"{d['checkout']}: {d['state']}"] + ([f"error: {d['error']}"]
                                                                          if d.get("error") else []))
        return 0 if r["ok"] else 3
    except RuntimeError as e:
        emit({"ok": False, "error": str(e), "next": "nothing was forced"}, a.json, lambda d: [f"error: {d['error']}"])
        return 3


if __name__ == "__main__":
    sys.exit(main())
