# Version: 1.0.0 · updated 26-11-11
"""GitHub's runner is down -> the SAME CI runs locally and counts (plan P9; owner, 2026-11-11, verbatim: "Add a rule.
If the GitHub's remote machine is not available, we can run a CI locally. We have all the infrastructure needed. We
have all the dependencies. We have all the programs.").

    fieldkit ship ci-local PR [--os linux|windows] [--never-started-min 30] [--dry-run] [--json]
    fieldkit ci-steps --os OS --base BRANCH [--event pull_request] [--report FILE]     the step runner (a peer job)

THE RULES (fixed; no model in the path):
  1. ONLY INFRASTRUCTURE. The PR head's GitHub job for that OS (`test (ubuntu-latest)`, `test (windows-latest)`) failed
     by a known runner-infrastructure signature (fieldkit/build/signatures/ci-infra.yaml, strikes.infra: a log that
     names a failed test is never infrastructure), or never started within --never-started-min minutes. A test
     failure on GitHub is never proven locally (refused, exit 3); green, pending or unreadable: nothing to do.
  2. ONLY THE PR HEAD. The run is on the PR's exact head commit (headRefOid), in a clean scratch worktree; the head is
     read again after the run, and a proof is made and posted only when it still matches (sha_matches).
  3. THE SAME STEPS. The workflow's own job for that OS (.github/workflows/tests.yml at that commit) is read and its
     steps run in order: `actions/checkout` is the worktree (origin/BASE fetched first), `actions/setup-python` is a
     fresh venv, every `run:` step runs as written (its `if:`, `shell:`, `timeout-minutes:`; the `${{ }}` expressions
     of a pull_request event). An expression, condition or action this runner does not know refuses the whole run.
  4. WHERE. Linux on this machine; Windows on a Windows device over the cable (`orchestrate job run ci-steps`, the
     signed result and its report artefact). The OS of the job is the OS of the machine that ran it, always.
  5. GREEN -> a proof record (repo, PR, commit, OS, context, every step with its exit code and seconds, the log's
     sha256, host, Python), signed with this computer's review key (mandate.review_key), kept in
     state/ci-local/<sha>-<os>.json with its log, and posted on the commit as a status with the CI job's context, so
     `ship push --auto` / auto-merge can merge it. When main's ruleset pins that check to the GitHub Actions app, a
     status cannot satisfy it: that is said, and the owner gets one inbox item with the ruleset change.
     The other OS's GitHub green is still required (ci-local proves only the OS it ran).
  6. RED -> in a check step (anything after setup): a strike, as a GitHub red would be (strikes.record_failure).
     Red in setup (checkout, venv, pip, a provisioning step) proves nothing either way: no proof, no strike.

Exit 0 proven (and posted), 2 bad input, 3 refused or red.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import emit

MATRIX = {"linux": "ubuntu-latest", "windows": "windows-latest"}
RUNNER_OS = {"linux": "Linux", "windows": "Windows"}
WORKFLOW = ".github/workflows/tests.yml"
NEVER_STARTED_MIN = 30
STEP_TIMEOUT_MIN = 60
REPORT = "fk-ci-steps.json"
SHA = re.compile(r"[0-9a-f]{40}")
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
COND = re.compile(r"^\s*([a-z_.]+)\s*(==|!=)\s*'([^']*)'\s*$")
SETUP_RUN = re.compile(r"^\s*(?:python\d?|py)\s+-m\s+pip\s+install\b|apt-get\s+install|desktop-file-utils\.sh")


class Unsupported(ValueError):
    """The workflow uses something this runner does not reproduce: the whole run is refused (fail closed)."""


# -- the two decisions the rule rests on (pure; tests/cilocal_mutations.yaml proves each can fail) -------------------
def classify(check, log_lines=(), age_s=0.0, limit_s=NEVER_STARTED_MIN * 60):
    """-> (verdict, why). verdict: green | pending | infra | never-started | test | none.
    `check`: the check run of the job for that OS on the head ({status, conclusion}) or None (not created yet);
    `age_s`: seconds since the head's CI was asked for (the PR head's push). Only infra and never-started may run
    locally; anything not proven infrastructure is a test failure (fail closed)."""
    if not check:
        if age_s >= limit_s:
            return "never-started", f"no check run for this OS {age_s / 60:.0f} min after the push"
        return "none", "no check run yet"
    status, concl = str(check.get("status") or ""), str(check.get("conclusion") or "")
    if status != "completed":
        if status in ("queued", "waiting", "pending", "requested") and age_s >= limit_s:
            return "never-started", f"the job is still {status} {age_s / 60:.0f} min after the push"
        return "pending", f"the job is {status or 'not started'}"
    if concl in ("success", "neutral", "skipped"):
        return "green", f"GitHub's job concluded {concl}"
    from . import strikes
    inf = strikes.infra(list(log_lines or ()))
    if inf:
        return "infra", inf
    return "test", f"GitHub's job concluded {concl or 'unknown'} and its log is not a known runner outage"


def sha_matches(sha, head):
    """A proof belongs to the PR head only: both full 40-hex commit ids, equal."""
    a, b = str(sha or "").lower(), str(head or "").lower()
    return bool(SHA.fullmatch(a)) and a == b


# -- the workflow ------------------------------------------------------------------------------------------------------
def load_job(text, os_word):
    """-> (context, steps) of the job whose matrix (or runs-on) is that OS's runner, from the workflow's YAML text."""
    import yaml
    want = MATRIX[os_word]
    wf = yaml.safe_load(text) or {}
    for jid, job in (wf.get("jobs") or {}).items():
        oses = (((job.get("strategy") or {}).get("matrix") or {}).get("os")) or []
        runs_on = str(job.get("runs-on") or "")
        if want in oses or runs_on == want:
            name = str(job.get("name") or jid).replace("${{ matrix.os }}", want)
            ctx = name if want in name else f"{name} ({want})" if want in oses else name
            return ctx, list(job.get("steps") or [])
    raise Unsupported(f"no job in the workflow runs on {want}")


def _value(name, ctx):
    if name not in ctx:
        raise Unsupported(f"expression {name!r} is not one this runner reproduces")
    return ctx[name]


def expand(text, ctx):
    """`${{ name }}` -> its value for this run; an unknown expression refuses (Unsupported)."""
    return EXPR.sub(lambda m: str(_value(m.group(1), ctx)), str(text))


def condition(expr, ctx):
    """An `if:` of the forms NAME == 'v' / NAME != 'v' (optionally inside ${{ }}) -> bool; others refuse."""
    if expr is None or expr is True:
        return True
    e = str(expr).strip()
    m = EXPR.fullmatch(e)
    e = m.group(1) if m else e
    c = COND.match(e)
    if not c:
        raise Unsupported(f"condition {expr!r} is not one this runner reproduces")
    v = str(_value(c.group(1), ctx))
    return (v == c.group(3)) if c.group(2) == "==" else (v != c.group(3))


def context_for(os_word, base, event="pull_request", before=""):
    return {"github.event_name": event, "github.base_ref": base, "github.event.before": before,
            "runner.os": RUNNER_OS[os_word], "matrix.os": MATRIX[os_word]}


def plan(steps, ctx):
    """-> [{name, kind: checkout|python|run|skipped, cmd, shell, timeout, setup}] in order."""
    out, seen_check = [], False
    for i, s in enumerate(steps, 1):
        name = str(s.get("name") or s.get("uses") or str(s.get("run") or "").strip().splitlines()[0][:80] or f"step {i}")
        if not condition(s.get("if"), ctx):
            out.append({"name": name, "kind": "skipped", "why": f"if: {s.get('if')}"})
            continue
        uses = str(s.get("uses") or "")
        if uses:
            if uses.startswith("actions/checkout@"):
                out.append({"name": name, "kind": "checkout", "setup": True})
            elif uses.startswith("actions/setup-python@"):
                out.append({"name": name, "kind": "python", "setup": True,
                            "want": str((s.get("with") or {}).get("python-version") or "")})
            else:
                raise Unsupported(f"action {uses} is not one this runner reproduces")
            continue
        if "run" not in s:
            raise Unsupported(f"step {name!r} has neither run nor a known action")
        cmd = expand(s["run"], ctx)
        setup = not seen_check and bool(SETUP_RUN.search(cmd))
        seen_check = seen_check or not setup
        out.append({"name": name, "kind": "run", "cmd": cmd, "shell": s.get("shell") or "",
                    "timeout": int(s.get("timeout-minutes") or STEP_TIMEOUT_MIN), "setup": setup})
    return out


def _shell(shell, script_path, os_word):
    if (shell or ("bash" if os_word == "linux" else "pwsh")) == "bash":
        return ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script_path)]
    exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return [exe, "-NoProfile", "-NonInteractive", "-Command", f". '{script_path}'"]


def run_steps(tree, steps, os_word, log_path, python=None, run=subprocess.run):
    """Run the planned steps in `tree`, in order, stopping at the first red. -> [{name, kind, rc, seconds, setup}]."""
    tree, results = Path(tree), []
    venv = tree / ".ci-local-venv"
    env = dict(os.environ, CI="true", RUNNER_OS=RUNNER_OS[os_word], PYTHONUTF8="1")
    for k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "FIELDKIT_PARKED_FILE"):
        env.pop(k, None)
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        for i, st in enumerate(steps, 1):
            log.write(f"\n##[step {i}] {st['name']} ({st['kind']})\n")
            log.flush()
            t0, rc = time.monotonic(), 0
            if st["kind"] == "skipped":
                log.write(f"skipped: {st.get('why')}\n")
            elif st["kind"] == "checkout":
                log.write(f"the worktree at {tree} is the checkout\n")
            elif st["kind"] == "python":
                py = python or sys.executable
                r = run([py, "-m", "venv", str(venv)], stdout=log, stderr=subprocess.STDOUT, timeout=600)
                rc = r.returncode
                bindir = venv / ("Scripts" if os_word == "windows" else "bin")
                env["PATH"] = os.pathsep.join([str(bindir), env.get("PATH", "")])
                env["VIRTUAL_ENV"] = str(venv)
                ver = subprocess.run([str(bindir / "python"), "--version"], capture_output=True, text=True).stdout
                log.write(f"python wanted {st.get('want') or '?'}, this venv: {ver.strip()}\n")
            else:
                script = tree.parent / f".ci-local-step-{i}.{'sh' if (st['shell'] or 'bash') == 'bash' else 'ps1'}"
                script.write_text(st["cmd"], encoding="utf-8")
                try:
                    r = run(_shell(st["shell"], script, os_word), cwd=str(tree), env=env, stdout=log,
                            stderr=subprocess.STDOUT, timeout=st["timeout"] * 60)
                    rc = r.returncode
                except subprocess.TimeoutExpired:
                    rc = 124
                    log.write(f"\ntimed out after {st['timeout']} min\n")
            secs = round(time.monotonic() - t0, 1)
            log.write(f"##[exit {rc}] {secs}s\n")
            log.flush()
            results.append({"name": st["name"], "kind": st["kind"], "rc": rc, "seconds": secs,
                            "setup": bool(st.get("setup"))})
            if rc:
                break
    return results


def steps_verdict(results, planned):
    """-> green | red-setup | red-check."""
    bad = [r for r in results if r["rc"]]
    if not bad and len(results) == len(planned):
        return "green"
    return "red-setup" if bad and bad[0]["setup"] else "red-check"


def file_sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def steps_here(tree, os_word, base, event="pull_request", log_path=None, workflow=WORKFLOW, run=subprocess.run):
    """The whole step run in a checked-out tree. -> {context, steps, verdict, log, log_sha256, python, host}."""
    tree = Path(tree)
    ctx = context_for(os_word, base, event)
    context, raw = load_job((tree / workflow).read_text(encoding="utf-8"), os_word)
    planned = plan(raw, ctx)
    log_path = Path(log_path or tree.parent / f"ci-local-{os_word}.log")
    res = run_steps(tree, planned, os_word, log_path, run=run)
    return {"context": context, "workflow": workflow, "event": event, "base": base, "os": os_word,
            "steps": res, "planned": len(planned), "verdict": steps_verdict(res, planned), "log": str(log_path),
            "log_sha256": file_sha(log_path), "python": sys.version.split()[0], "host": socket.gethostname()}


# -- GitHub ------------------------------------------------------------------------------------------------------------
class Hub:
    """The few gh calls ci-local makes (a fake in the tests), through ship's credential (ship_env)."""

    def __init__(self, repo=".", run=subprocess.run):
        from . import train
        self.gh = train.GH(repo, run=run)
        self.slug = self.gh.slug

    def _json(self, *a):
        return self.gh._json(*a)

    def pr(self, n):
        return self._json("pr", "view", str(n), "-R", self.slug, "--json",
                          "number,headRefName,baseRefName,headRefOid,state")

    def check(self, sha, context):
        runs = self._json("api", f"repos/{self.slug}/commits/{sha}/check-runs", "--jq",
                          "[.check_runs[] | {id, name, status, conclusion, started_at, completed_at}]") or []
        mine = [c for c in runs if c.get("name") == context]
        return sorted(mine, key=lambda c: str(c.get("started_at") or ""))[-1] if mine else None

    def job_log(self, job_id, lines=200):
        rc, out, _ = self.gh._gh("run", "view", "--job", str(job_id), "-R", self.slug, "--log-failed", timeout=300)
        return out.splitlines()[-lines:] if rc == 0 else []

    def pushed_at(self, sha):
        """-> epoch seconds of the commit's committer date (when CI was asked for, at the latest), or 0."""
        rc, out, _ = self.gh._gh("api", f"repos/{self.slug}/commits/{sha}", "--jq", ".commit.committer.date")
        if rc or not out.strip():
            return 0.0
        import datetime as dt
        return dt.datetime.fromisoformat(out.strip().replace("Z", "+00:00")).timestamp()

    def post_status(self, sha, context, description, state="success"):
        rc, out, err = self.gh._gh("api", "-X", "POST", f"repos/{self.slug}/statuses/{sha}", "-f", f"state={state}",
                                   "-f", f"context={context}", "-f", f"description={description[:140]}")
        return rc == 0, (err or out).strip()[-200:]

    def pinned(self, context, base="main"):
        """-> the app id main's ruleset or branch protection pins `context` to, or None (a status can satisfy it)."""
        rc, out, _ = self.gh._gh("api", f"repos/{self.slug}/rules/branches/{base}")
        if rc:
            return None
        for rule in json.loads(out or "[]"):
            if rule.get("type") == "required_status_checks":
                for c in (rule.get("parameters") or {}).get("required_status_checks") or []:
                    if c.get("context") == context and c.get("integration_id"):
                        return int(c["integration_id"])
        return None


def proof_dir():
    from ..core import settings
    return Path(settings.state_root()) / "state" / "ci-local"


def make_proof(slug, n, head, os_word, run_rec, why_local):
    rec = {"kind": "ci-local", "repo": slug, "pr": int(n), "sha": head, "os": os_word,
           "context": run_rec["context"], "workflow": run_rec["workflow"], "event": run_rec["event"],
           "steps": [{k: s[k] for k in ("name", "kind", "rc", "seconds")} for s in run_rec["steps"]],
           "log_sha256": run_rec["log_sha256"], "host": run_rec["host"], "python": run_rec["python"],
           "why_local": why_local, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        from ..mandate import mandate as M
        k, fp = M.review_key()
        rec["key"] = fp
        rec["sig"] = k.sign(canon(rec)).hex()
    except Exception as e:  # noqa: BLE001 - an unsigned proof is said, never posted
        rec["sig_error"] = f"{type(e).__name__}: {e}"
    return rec


def canon(rec):
    return json.dumps({k: v for k, v in rec.items() if k not in ("sig", "sig_error")}, sort_keys=True,
                      separators=(",", ":")).encode()


def _git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a[:3])}: {(r.stderr or r.stdout).strip()[-200:]}")
    return r.stdout.strip()


def this_os():
    return "windows" if sys.platform == "win32" else "linux" if sys.platform.startswith("linux") else sys.platform


def _fetch(repo, n, head, base):
    """origin/BASE and the PR head from GitHub; -> the local ref of the head. The PR moved: RuntimeError."""
    ref = f"refs/fieldkit/ci-local/{n}"
    for k in range(3):                                    # another session's fetch holds the ref lock: a moment later
        try:
            _git(repo, "fetch", "-q", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}",
                 f"+refs/pull/{n}/head:{ref}")
            break
        except RuntimeError as e:
            if "cannot lock ref" not in str(e) or k == 2:
                raise
            time.sleep(5)
    if _git(repo, "rev-parse", ref) != head:
        raise RuntimeError(f"origin's pull/{n}/head is not {head[:10]}: the PR moved; run ci-local again")
    return ref


def peer_tree(repo, ref, head, base, tree):
    """A git repository at `tree` checked out at `head`, with origin/BASE and the full history (the workflow diffs
    against origin/BASE: integration check, ci_tests.py), as actions/checkout with fetch-depth 0 gives. A tarball has
    no .git and its check steps would fail for that alone (a false strike)."""
    tree = Path(tree)
    _git(tree.parent, "init", "-q", str(tree))
    _git(tree, "fetch", "-q", str(Path(repo).resolve()), f"+refs/remotes/origin/{base}:refs/remotes/origin/{base}",
         f"+{ref}:{ref}")
    _git(tree, "-c", "advice.detachedHead=false", "checkout", "-q", "--detach", head)
    if _git(tree, "rev-parse", "HEAD") != head:
        raise RuntimeError("the tree is not at the head commit")
    return tree


def _here(repo, n, head, base, os_word, d):
    """Linux (or Windows on Windows): a detached worktree at the head in scratch `d`, the steps, the worktree gone."""
    _fetch(repo, n, head, base)
    wt = d / "tree"
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), head)
    try:
        if _git(wt, "rev-parse", "HEAD") != head:
            raise RuntimeError("the worktree is not at the head commit")
        return steps_here(wt, os_word, base, log_path=d / f"ci-local-{os_word}.log")
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt), check=False)


def _peer(repo, n, head, base, os_word, d, project="Gorilla.Fieldkit", wait_s=90 * 60):
    """The other OS over the cable: the head's tree, `fieldkit ci-steps` as a run job, its signed report back."""
    from .. import orchestrate_load as L
    from .. import orchestrate_work as W
    from ..mandate import jobs
    want = "WINDOWS" if os_word == "windows" else "LINUX"
    pick = next((r for r in L.load_all() if not r.get("me") and r.get("ok") and str(r.get("os")).upper() == want),
                None)
    if pick is None:
        raise RuntimeError(f"no {want} device answers over the cable (fieldkit orchestrate load)")
    tree = peer_tree(repo, _fetch(repo, n, head, base), head, base, d / f"tree-{head[:12]}")
    jid, said, t = W.send("run", pick["name"], str(tree), project, wall=90, artefacts=[REPORT],
                          argv=["ci-steps", "--os", os_word, "--base", base, "--report", REPORT])
    if said != "sent":
        raise RuntimeError(f"{pick['name']} did not take the job: {said}")
    e = jobs.wait(jid, wait_s)
    r = W.verified_result(jid, project, t["digest"]) if e else None
    if r is None:
        raise RuntimeError(f"{pick['name']}: no result signed by a pinned worker key in {wait_s // 60} min")
    got = [x for x in W.artefacts_check(r) if x[0] == REPORT]
    if not got or not got[0][1]:
        raise RuntimeError(f"{pick['name']}'s report did not arrive with the signed sha256")
    rec = json.loads(Path(got[0][2]).read_text(encoding="utf-8"))
    rec["worker"] = {"name": pick["name"], "job": jid, "result_key": r.get("key")}
    return rec


def ci_local(n, os_word=None, repo=".", hub=None, dry_run=False, limit_min=NEVER_STARTED_MIN, now=time.time,
             runner=None, strike=None, inbox=None, post=True):
    """-> {ok, state, why, next, ...}. The rules are in the module docstring."""
    os_word = os_word or this_os()
    if os_word not in MATRIX:
        return {"ok": False, "state": "bad-input", "why": f"--os {os_word}: linux or windows", "next": "fix the flag"}
    hub = hub or Hub(repo)
    pr = hub.pr(n)
    head, base, branch = pr.get("headRefOid") or "", pr.get("baseRefName") or "main", pr.get("headRefName") or ""
    out = {"pr": n, "branch": branch, "head": head, "os": os_word}
    if str(pr.get("state", "OPEN")).upper() != "OPEN" or not SHA.fullmatch(head):
        return dict(out, ok=False, state="refused", why=f"PR {n} is {pr.get('state')} (head {head[:10] or '?'})",
                    next="nothing to prove")
    context = f"test ({MATRIX[os_word]})"
    try:                                                  # the context the workflow at the head really gives
        text = _git(repo, "show", f"{head}:{WORKFLOW}", check=True) if runner is None else None
        if text:
            context = load_job(text, os_word)[0]
    except Exception:  # noqa: BLE001 - the head may not be fetched yet; the default name is checked again later
        pass
    chk = hub.check(head, context)
    log = hub.job_log(chk["id"]) if chk and chk.get("status") == "completed" and chk.get("conclusion") not in (
        "success", "neutral", "skipped") else []
    age = max(0.0, now() - hub.pushed_at(head)) if not chk or chk.get("status") != "completed" else 0.0
    verdict, why = classify(chk, log, age, limit_min * 60)
    out.update(context=context, github=verdict, github_why=why)
    if verdict not in ("infra", "never-started"):
        nxt = {"test": "a test failure is fixed on the branch, never proven locally",
               "green": f"nothing to prove: fieldkit ship ci-wait {n}"}.get(verdict, f"wait: fieldkit ship ci-wait {n}")
        return dict(out, ok=False, state="refused", why=f"GitHub's {context} is {verdict}: {why}", next=nxt)
    if dry_run:
        return dict(out, ok=True, state="eligible", why=why, next=f"fieldkit ship ci-local {n} --os {os_word}")
    from .. import scratch
    d, _m = scratch.new(purpose=f"ci-local PR {n} {head[:10]} {os_word}")
    d = Path(d)
    try:
        if runner is not None:
            rec = runner(repo, n, head, base, os_word, d)
        elif os_word == this_os():
            rec = _here(repo, n, head, base, os_word, d)
        else:
            rec = _peer(repo, n, head, base, os_word, d)
    except (Unsupported, RuntimeError, subprocess.CalledProcessError, OSError) as e:
        _drop(d)
        return dict(out, ok=False, state="not-run", why=f"{type(e).__name__}: {e}",
                    next="no proof and no strike; fix what is named, then ci-local again")
    out.update(steps=rec["steps"], verdict=rec["verdict"], log_sha256=rec["log_sha256"])
    pr2 = hub.pr(n)
    if not sha_matches(head, pr2.get("headRefOid")):
        return dict(out, ok=False, state="moved", why=f"the PR head moved to {str(pr2.get('headRefOid'))[:10]} "
                                                       f"during the run: no proof for {head[:10]}",
                    next=f"its CI runs on the new head: fieldkit ship ci-wait {n}")
    pd = proof_dir()
    pd.mkdir(parents=True, exist_ok=True)
    keep_log = pd / f"{head}-{os_word}.log"
    if rec.get("log") and Path(rec["log"]).is_file():
        shutil.copyfile(rec["log"], keep_log)
    _drop(d)                                              # the run's scratch job; the proof and its log are kept
    if rec["verdict"] != "green":
        bad = next((s for s in rec["steps"] if s["rc"]), {"name": "?", "rc": "?"})
        if rec["verdict"] == "red-setup":
            return dict(out, ok=False, state="red-setup", log=str(keep_log),
                        why=f"setup step {bad['name']!r} exited {bad['rc']}: proves nothing, no strike",
                        next=f"fix this machine's setup (the log: {keep_log}), then ci-local again")
        from . import strikes
        s = (strike or strikes.record_failure)(branch, head, f"ci-local {os_word}: {bad['name']} exited {bad['rc']}",
                                               pr=n)
        return dict(out, ok=False, state="red", log=str(keep_log), strike=s,
                    why=f"local {context}: step {bad['name']!r} exited {bad['rc']} (a strike, as on GitHub)",
                    next="fix it on the branch, push it; " + ("PARKED" if s.get("parked") else "ci-wait again"))
    proof = make_proof(hub.slug, n, head, os_word, rec, why)
    p = pd / f"{head}-{os_word}.json"
    p.write_text(json.dumps(proof, indent=1, ensure_ascii=False), encoding="utf-8")
    out.update(proof=str(p), log=str(keep_log))
    if "sig" not in proof:
        return dict(out, ok=False, state="unsigned", why=f"the proof could not be signed: {proof.get('sig_error')}",
                    next="nothing was posted; fix the review key (fieldkit mandate), then ci-local again")
    if not post:
        return dict(out, ok=True, state="proven", why="green locally; not posted (--no-post)", next="post it")
    desc = (f"local CI on {proof['host']} ({os_word}): {len(proof['steps'])} steps green, GitHub runner "
            f"{'down' if verdict == 'infra' else 'never started'}; proof {proof['log_sha256'][:12]}")
    ok, msg = hub.post_status(head, context, desc)
    if not ok:
        return dict(out, ok=False, state="not-posted", why=f"the status was refused: {msg}",
                    next=f"the proof is kept at {p}; post it again: fieldkit ship ci-local {n}")
    app = hub.pinned(context, base)
    out.update(posted=context, pinned=app)
    if app:
        title = f"Local CI proofs cannot merge: {base}'s ruleset pins '{context}' to app {app}"
        (inbox or _inbox)(title, f"Your rule (2026-11-11): a green local CI counts when GitHub's runner is down. "
                                 f"PR {n}'s proof is posted as a commit status, but {base}'s ruleset accepts "
                                 f"'{context}' only from the GitHub Actions app ({app}). Allowing any source for "
                                 f"the two test checks lets the posted proof count.",
                          f"gh api repos/{hub.slug}/rulesets  (edit: drop integration_id from the two test checks)")
        return dict(out, ok=True, state="proven-not-counted",
                    why=f"proof posted as {context!r}; {base}'s ruleset pins that check to app {app}, so GitHub "
                        f"will not count it until the owner allows it (inbox item added)",
                    next="go on with the plan; the owner's morning inbox has the ruleset change")
    return dict(out, ok=True, state="proven", why=f"proof posted as {context!r} on {head[:10]}",
                next=f"auto-merge merges on the other OS's green: fieldkit ship ci-wait {n}")


def _drop(d):
    """This run's own scratch job (fieldkit scratch: deleted directly, never executed from; AGENTS.md rule 2)."""
    try:
        from .. import scratch
        scratch.clean(names=[Path(d).name], mine=True)
    except Exception:  # noqa: BLE001 - left for `fieldkit scratch clean --mine`, said by the hog
        pass


def _inbox(title, why, cmd):
    try:
        from ..autopilot import inbox as ib
        ib.add(title, why, cmd)
    except Exception:  # noqa: BLE001 - said in the result either way
        pass


def lines(d):
    out = [f"CI-LOCAL  PR {d.get('pr')} {str(d.get('head', ''))[:10]} {d.get('os')}  {d.get('state')}: {d.get('why')}"]
    for s in d.get("steps") or []:
        out.append(f"  {'ok ' if not s['rc'] else 'RED'} {s['rc']:>3} {s['seconds']:>7}s  {s['name']}")
    for k in ("proof", "log"):
        if d.get(k):
            out.append(f"  {k}: {d[k]}")
    return out


def main(argv, prog="fieldkit ship ci-local"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pr", type=int)
    ap.add_argument("--os", choices=sorted(MATRIX), default=None, help="the OS whose GitHub job failed (this one)")
    ap.add_argument("--never-started-min", type=int, default=NEVER_STARTED_MIN)
    ap.add_argument("--dry-run", action="store_true", help="only say whether this PR may be proven locally")
    ap.add_argument("--no-post", action="store_true", help="prove and keep the record, post nothing")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = ci_local(a.pr, a.os, a.repo, dry_run=a.dry_run, limit_min=a.never_started_min, post=not a.no_post)
    except RuntimeError as e:
        r = {"ok": False, "state": "error", "why": str(e), "next": "fix what is named (gh signed in?)", "pr": a.pr}
    emit(r, a.json, lines)
    return 0 if r.get("ok") else 2 if r.get("state") == "bad-input" else 3


def steps_main(argv, prog="fieldkit ci-steps"):
    """The step runner alone, in the current folder (the tree): what a Windows device runs as a cable job."""
    ap = argparse.ArgumentParser(prog=prog, description="run the CI workflow's job for this OS in this tree "
                                                        "(fieldkit ship ci-local sends it over the cable)")
    ap.add_argument("--os", choices=sorted(MATRIX), default=this_os())
    ap.add_argument("--base", default="main")
    ap.add_argument("--event", default="pull_request")
    ap.add_argument("--report", default=REPORT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.os != this_os():
        print(f"not-this-platform: the {a.os} job runs on {a.os}, this is {this_os()}")
        return 3
    try:
        rec = steps_here(Path.cwd(), a.os, a.base, a.event, log_path=Path.cwd().parent / f"ci-steps-{a.os}.log")
    except Unsupported as e:
        print(f"refused: {e}")
        return 3
    Path(a.report).write_text(json.dumps(rec, indent=1), encoding="utf-8")
    for s in rec["steps"]:
        print(f"{'ok ' if not s['rc'] else 'RED'} {s['rc']:>3} {s['seconds']:>7}s  {s['name']}")
    print(f"{rec['verdict']}: {rec['context']}, log sha256 {rec['log_sha256'][:12]}")
    return 0 if rec["verdict"] == "green" else 3
