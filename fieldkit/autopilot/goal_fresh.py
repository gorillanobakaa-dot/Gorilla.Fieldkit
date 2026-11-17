# Version: 1.0.0 · updated 26-11-02-13-30
"""The verifier judges origin/main, never a checkout somebody left on a branch (HARNESS-PLAN R13, defect 5 of
state/reports/2026-11-02-why-the-harness-failed.md).

Measured 2026-11-02: capture-core was marked FAIL because its six checks ran in the main checkout, which sat on a stale
branch; on origin/main all six exit 0. Then two goal workers switched the main checkout to feat/capture-hooks, so every
`fieldkit` command (an editable install) ran that branch's code (open problems rows 23, 36).

    verify        every criterion and job check runs in a fresh detached worktree of origin/main (fetched first),
                  made inside a `fieldkit scratch new` job folder and removed after; the checks' cwd inside the main
                  checkout is moved to the same place in that tree. local/goal-checks and fieldkit.local.json (git
                  ignored, so not in the tree) are copied in; state/ is shared by state_root. A folder that is not in
                  git, or a repository without the base ref, is judged where it is (said in the verdict).
    workers       a worker never runs in the main checkout: it gets its own worktree (worktrees.make), kept for the
                  job's later attempts; no worktree in a git repository = the job is refused, not started
    reverify      `fieldkit goal reverify GOAL JOB` runs one job's checks the same way and records the verdict
"""
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

BASE = "origin/main"
COPY = ("local/goal-checks", "fieldkit.local.json")     # git-ignored files the checks need, copied into the tree
WORKTREES = True                                          # tests of other things switch it off


class Refused(ValueError):
    """A worker would run in the main checkout, or a fresh tree could not be made."""


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _git(repo, *a, run=subprocess.run):
    from ..lifecycle import worktrees
    return worktrees._git(repo, *a, run=run)


def toplevel(path, run=subprocess.run):
    rc, out = _git(path, "rev-parse", "--show-toplevel", run=run)
    return Path(out) if rc == 0 and out else None


def is_main_checkout(path, run=subprocess.run):
    """True when `path` is inside the MAIN checkout of a repository (its .git is the folder, not a worktree's file)."""
    from ..lifecycle import worktrees
    tl, main = toplevel(path, run), worktrees.top(path, run=run)
    return bool(tl and main and _norm(tl) == _norm(main))


def worker_cwd(gid, jid, cwd, prior=None, attempt=1, make=None, run=subprocess.run):
    """-> (folder the worker runs in, why). In the main checkout of a git repository the worker gets a worktree of its
    own (the one it had before, on a later attempt). Raises Refused when it cannot have one."""
    cwd = os.path.expandvars(cwd or ".")
    if prior and Path(prior).is_dir():
        return str(prior), f"its own worktree {prior} (kept from the earlier attempt)"
    if not WORKTREES or not is_main_checkout(cwd, run):
        return cwd, "not the main checkout"
    from ..lifecycle import worktrees
    try:
        rel = os.path.relpath(cwd, toplevel(cwd, run))
        wt, why = (make or worktrees.make)(cwd, f"goal-{gid}-{jid}-{attempt}")
    except worktrees.Refused as e:
        raise Refused(f"cwd {cwd} is the main checkout and the job has no worktree of its own: {e}") from e
    if wt is None:
        raise Refused(f"cwd {cwd} is the main checkout: {why}")
    return str(Path(wt) / rel) if rel != "." else str(wt), why


class Tree:
    """A fresh detached worktree of the base ref, in a scratch job folder. Use as a context manager."""

    def __init__(self, repo, gid="goal", base=BASE, run=subprocess.run, scratch=None):
        self.repo, self.gid, self.base, self.run = Path(repo), gid, base, run
        self.top = toplevel(repo, run)
        self.main = None
        self.dir = self.path = None
        self.sha = ""
        self.note = ""
        self.scratch = scratch

    def __enter__(self):
        from ..lifecycle import worktrees
        from .. import scratch as sc
        sc = self.scratch or sc
        self.main = worktrees.top(self.repo, run=self.run)
        rc, out = _git(self.main, "fetch", "--quiet", "origin", self.base.split("/", 1)[-1], run=self.run)
        if rc != 0:
            self.note = f"fetch failed ({out[:120]}): origin/main as of the last fetch"
        rc, sha = _git(self.main, "rev-parse", "--verify", "-q", self.base, run=self.run)
        if rc != 0:
            raise Refused(f"no {self.base} in {self.main}")
        self.sha = sha[:12]
        job = f"verify-{self.gid}-{secrets.token_hex(3)}"[:80]
        self.dir, _ = sc.new(job=job, purpose=f"fresh {self.base} tree for the verifier of goal {self.gid}",
                             kind="verify")
        self.path = Path(self.dir) / "tree"
        rc, out = _git(self.main, "worktree", "add", "--detach", str(self.path), self.base, run=self.run)
        if rc != 0:
            self.close()
            raise Refused(f"git worktree add failed: {out[:200]}")
        from ..pycache.core import warm_background
        warm_background(self.path)               # P9: bytecode for the verifier's hooks, in the background
        for rel in COPY:
            src, dst = Path(self.main) / rel, self.path / rel
            try:
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            except OSError as e:
                self.note += f"; could not copy {rel}: {e}"
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        from .. import scratch as sc
        sc = self.scratch or sc
        if self.path is not None and self.main is not None:
            _git(self.main, "worktree", "remove", "--force", str(self.path), run=self.run)
            _git(self.main, "worktree", "prune", run=self.run)
        if self.dir is not None:
            sc.clean(names=[Path(self.dir).name])

    def map(self, cwd):
        """-> cwd moved from the repository into the tree (the same place in it); other folders stay."""
        cwd = os.path.expandvars(str(cwd or "."))
        try:
            rel = os.path.relpath(cwd, self.top)
        except ValueError:
            return cwd
        if rel == ".." or rel.startswith(".." + os.sep) or os.path.isabs(rel):
            return cwd
        return str(self.path / rel) if rel != "." else str(self.path)

    def remap(self, c, default_cwd):
        return dict(c, cwd=self.map(c.get("cwd") or default_cwd))

    def label(self):
        return f"{self.base} {self.sha}" + (f" ({self.note})" if self.note else "")


class Null:
    """No fresh tree (not in git, or no base ref): checks run where the goal says. `note` says why."""
    path = None
    note = ""
    failed = ""                 # set to the reason when a fresh tree was required and could not be made

    def __init__(self, note=""):
        self.note = note

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def map(self, cwd):
        return cwd

    def remap(self, c, default_cwd):
        return c

    def label(self):
        return ""


def tree_for(goal, gid, run=subprocess.run, scratch=None, base=None):
    """-> a context manager: a Tree of the goal's repository at origin/main (goal.json "verify_base"), or Null when
    the goal's folder is not in git or the repository has no such ref."""
    cwd = os.path.expandvars(goal.get("cwd") or "")
    if not cwd or not Path(cwd).is_dir() or toplevel(cwd, run) is None:
        return Null("not in git: judged where it is")
    b = base or goal.get("verify_base") or BASE
    from ..lifecycle import worktrees
    main = worktrees.top(cwd, run=run)
    if _git(main, "rev-parse", "--verify", "-q", b, run=run)[0] != 0:
        return Null(f"no {b}: judged where it is")
    return Tree(cwd, gid, b, run, scratch)


# ---- fieldkit goal reverify GOAL JOB -------------------------------------------------------------------------------
def reverify(gid, jid, claude=None, run=subprocess.run, say=print, scratch=None):
    """Run one job's own checks on a fresh origin/main tree and record the verdict (verdicts.jsonl). A pass marks the
    job verified: now when no supervisor runs, else the supervisor applies the marker at its next round. -> (ok, text)."""
    from . import goal
    g, st = goal.load(gid)
    if not g or not st:
        return False, f"no goal {gid}"
    j = (st.get("jobs") or {}).get(jid)
    if not j:
        return False, f"goal {gid} has no job {jid}: {sorted(st.get('jobs') or {})}"
    checks = [dict(v, id=f"{jid}:{i}") for i, v in enumerate(j["spec"].get("verify") or [], 1)]
    if not checks:
        return False, f"job {jid} has no checks to re-run"
    res = goal.verify(gid, g, st, ids=[], extra=checks, claude=claude, run=run, who=f"reverify {jid}")
    own = [x for x in res if str(x["id"]).startswith(f"{jid}:")]
    for x in own:
        say(f"  {x['verdict']:<7} {x['id']}  {x['why']}  [{x.get('tree') or 'where it is'}]")
    ok = bool(own) and all(x["verdict"] == "PASS" for x in own)
    n = sum(1 for x in own if x["verdict"] == "PASS")
    d = goal.gdir(gid) / "reverify"
    d.mkdir(exist_ok=True)
    marker = {"job": jid, "ok": ok, "passed": n, "of": len(own), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "tree": own[0].get("tree") if own else ""}
    (d / f"{jid}.json").write_text(json.dumps(marker), encoding="utf-8")
    lk = goal._rj(goal.GOALS / ".supervisor.lock")
    if not (lk and lk.get("pid") != os.getpid() and goal._holder_alive(lk)):
        _, st = goal.load(gid)
        apply_markers(gid, st)
        goal.save_state(gid, st)
    return ok, f"{'PASS' if ok else 'FAIL'} {n}/{len(own)} for job {jid} of goal {gid} on {marker['tree'] or 'where it is'}"


def apply_markers(gid, st):
    """The supervisor's (or reverify's) own write: a re-verified job that passed is verified. -> [jid]."""
    from . import goal
    d = goal.gdir(gid) / "reverify"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        m = goal._rj(f) or {}
        j = (st.get("jobs") or {}).get(m.get("job"))
        f.unlink(missing_ok=True)
        if not j or not m.get("ok") or j["status"] == "verified":
            continue
        j.update(status="verified", why=f"re-verified on {m.get('tree') or 'a fresh tree'}: {m['passed']}/{m['of']} PASS")
        st["needs_plan"] = True
        goal.event(gid, f"job {m['job']}: {j['why']}")
        out.append(m["job"])
    return out
