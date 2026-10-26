# Version: 1.2.0 · updated 26-10-26-15-40
"""Long horizon goals: a goal handed over and worked toward for days, not a queue a person writes each night.

Owner, 2026-10-22: "by Long Horizon Tasks we really mean Long Horizon Tasks" and "eliminate THE MAIN BOTTLENECK, THE
HUMAN IN THE LOOP ... my 15 years old scripts for gentoo and debian could run for 24 to 48 hours compiling firefox
or the kernel, without me ever having to be present". DESIGN.md sections 0 and 0b, with the Manage-Execute-Audit
loop of arXiv 2608.01964 (AMAP-ML LongHorizon-Harness): a planner (manager) issues bounded jobs, a fresh worker
(executor) does one, a SEPARATE verifier (auditor) decides; a worker's claim never changes the goal's state.

  state/goals/<id>/goal.json      objective, criteria (commands: exit code or printed line decides; or judge: agent),
                                  milestones, constraints, budgets (days, cost_usd, window_usd/window_hours),
                                  owner_decisions, cwd, seed_jobs
  state/goals/<id>/GOAL.md        the same for a person (written from goal.json)
  state/goals/<id>/state.json     records: criteria (pending | completed | blocked | untrusted, with evidence),
                                  jobs, answers, rounds, spend; the harness alone writes it (hard line 3)
  state/goals/<id>/PREAPPROVALS.yaml + APPROVAL.json
                                  the owner's decisions recorded IN BULK ahead of time and approved once at a real
                                  terminal (sha256-pinned; any later change voids it, fail closed). A question a
                                  pre-approval covers is answered by the harness; an action it covers (push_branch,
                                  open_pr) the harness does. Nothing waits for a person unless nothing covers it,
                                  and even then the work that does not depend on the answer goes on.

    fieldkit goal new ID --from SPEC.json | --objective TEXT     fieldkit goal status [ID]
    fieldkit goal run [ID] [--once]   (the supervisor: every unfinished goal, lock + heartbeat)
    fieldkit goal stop ID|--all [why]    fieldkit goal approve ID    fieldkit goal supervisor-script [OUT.ps1|OUT.sh]

Loop (one round): STOP? -> paused / budget / usage window? -> plan when due (after every verified result, failure
and owner answer) -> planner says done: the verifier runs every criterion and wins -> next runnable job -> worker
(job.py: budgets, checkpoint, resume) -> verifier (the job's own checks + every goal criterion) -> re-plan.

Parallel workers (2026-10-26: goal windows-capabilities ran job 01 alone for two hours while job 03, disjoint files,
sat queued): up to goal.json "max_workers" (default 2) jobs run at once, each in its own thread. A job starts only
when its needs are verified and its touches (paths, resolved against its cwd; a parent folder covers what is under
it) are disjoint from every running job's; a job that declares no touches runs alone (fail closed). A round waits
for the FIRST running job to end, verifies it, and re-plans while the others go on. A job left "running" by a
supervisor that died is resumed from its checkpoint (paused-resumable), never left stuck.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent import futures
from pathlib import Path

import yaml

from . import budget as bud
from . import hardlines

ROOT = Path(__file__).resolve().parents[2]
GOALS = Path(os.environ.get("FIELDKIT_GOALS") or (ROOT / "state" / "goals"))
LOCK_STALE = 15 * 60                 # a supervisor whose heartbeat is older than this is dead
MAX_ROUNDS = 200                     # planner calls per goal before it is called blocked
IDLE_POLL = 10 * 60                  # how often a waiting goal looks at the inbox again
PLANNER_BUDGET = {"turns": 80, "wall_minutes": 30, "cost_usd": 3.0}
VERIFIER_BUDGET = {"turns": 60, "wall_minutes": 30, "cost_usd": 2.0}
SIGNALS = ("execute", "done", "blocked", "ask")
ACTIONS = ("push_branch", "open_pr")
PROTECTED_BRANCHES = ("main", "master", "windows-port")    # windows-port: Gorilla.Opencode's release branch
MAX_WORKERS = 2                      # jobs of one goal running at once, unless goal.json says "max_workers"
HEARTBEAT_EVERY = 60                 # the supervisor's lock heartbeat, from its own thread (workers block for hours)


# ---- files ------------------------------------------------------------------------------------------------------
def gdir(gid):
    return GOALS / gid


def _rj(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _wj(p, data):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def load(gid):
    return _rj(gdir(gid) / "goal.json"), _rj(gdir(gid) / "state.json")


def save_state(gid, st):
    _wj(gdir(gid) / "state.json", st)


def event(gid, text):
    with open(gdir(gid) / "events.log", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")


def validate(goal):
    """-> list of problems with a goal spec (empty = valid)."""
    out = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(goal.get("id", ""))):
        out.append("id: lower-case letters, digits, . _ - (max 64)")
    if not goal.get("objective"):
        out.append("objective is empty")
    crit = goal.get("criteria") or []
    if not crit:
        out.append("no criteria: a goal nobody can check is not a goal")
    ids = [c.get("id") for c in crit]
    if len(set(ids)) != len(ids) or not all(ids):
        out.append("every criterion needs a unique id")
    for c in crit:
        if c.get("judge") == "agent":
            if not c.get("question"):
                out.append(f"criterion {c.get('id')}: judge agent needs a question")
        elif not c.get("run"):
            out.append(f"criterion {c.get('id')}: no run command")
    for m in goal.get("milestones") or []:
        for c in m.get("criteria") or []:
            if c not in ids:
                out.append(f"milestone {m.get('id')}: unknown criterion {c}")
    for j in goal.get("seed_jobs") or []:
        out += [f"seed job {j.get('id')}: {p}" for p in check_job(j)]
    return out


def check_job(j):
    out = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", str(j.get("id", ""))):
        out.append("job id: lower-case letters, digits, . _ - (max 64)")
    if not j.get("brief"):
        out.append("job has no brief")
    if not j.get("cwd"):
        out.append("job has no cwd")
    bad = [k for k in (j.get("budget") or {}) if k not in bud.DEFAULT]
    if bad:
        out.append(f"unknown budget keys {bad}")
    from .job import check_fallback
    out += check_fallback(j.get("fallback"))
    for v in j.get("verify") or []:
        if not v.get("run"):
            out.append("a verify entry has no run")
    return out


def render_md(goal):
    L = [f"# Goal: {goal['id']}", "", goal["objective"], "", "## Acceptance criteria (the verifier runs these)", ""]
    for c in goal["criteria"]:
        how = (f"agent judge: {c['question']}" if c.get("judge") == "agent"
               else f"`{c['run']}` in `{c.get('cwd', goal.get('cwd', '.'))}`, expects " +
               (f"a line /{c['expect']['line']}/" if (c.get("expect") or {}).get("line") else
                f"exit {(c.get('expect') or {}).get('exit', 0)}"))
        L.append(f"- **{c['id']}** {c.get('title', '')}: {how}")
    if goal.get("milestones"):
        L += ["", "## Milestones", ""] + [f"- {m['id']}: {m.get('title', '')} ({', '.join(m.get('criteria', []))})"
                                          for m in goal["milestones"]]
    L += ["", "## Constraints", ""] + [f"- {c}" for c in goal.get("constraints") or ["(none)"]]
    b = goal.get("budget") or {}
    L += ["", "## Budget (whole goal)", "", f"- days: {b.get('days', 'no limit')}",
          f"- dollars: {b.get('cost_usd', 'no limit')}",
          f"- usage window: {b.get('window_usd', 'no limit')} $ per {b.get('window_hours', 5)} h"]
    L += ["", "## Decisions only the owner makes", ""] + [f"- {d}" for d in goal.get("owner_decisions") or ["(none)"]]
    L += ["", "Pre-approved decisions: PREAPPROVALS.yaml (approved with `fieldkit goal approve ID`).", ""]
    return "\n".join(L)


PREAPPROVAL_TEMPLATE = """\
# Decisions recorded in bulk BEFORE the goal runs (owner, 2026-10-22: "record all my decisions in bulk at once,
# i approve it and off you go"). Read all of it, then: fieldkit goal approve {gid}
# The approval pins this file's sha256: any change afterwards voids it until approved again (fail closed).
# Models can neither edit this file nor the approval (hard line 3).
#
# answers: a question the planner asks, by category, gets this answer without waiting.
#   take_default: true  -> the question's own proposed default is the answer
#   answer: TEXT        -> this answer, whatever the default
# actions: what the harness itself may do (never a model; never to main or master; never --force):
#   push_branch: {{repo: PATH, branches: GLOB, base: origin/main}}  after every criterion PASSed and the privacy
#                push-check of the commits is clean
#   open_pr:     {{repo: PATH, branches: GLOB, base: main}}         a pull request for a pushed branch
# expires: YYYY-MM-DD (after it, nothing here applies)
expires: null
answers: []
#  - {{category: scope, take_default: true, why: "small scope questions: the planner's default"}}
actions: []
#  - {{push_branch: {{repo: "D:/work/myrepo", branches: "feat/*", base: origin/main}}}}
"""


def new(spec, force=False):
    """Create a goal from a spec dict. -> goal dir. Refuses an existing goal unless force."""
    problems = validate(spec)
    if problems:
        raise ValueError("; ".join(problems))
    d = gdir(spec["id"])
    if (d / "goal.json").exists() and not force:
        raise ValueError(f"goal {spec['id']} exists ({d})")
    d.mkdir(parents=True, exist_ok=True)
    spec = dict(spec, created=spec.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"))
    _wj(d / "goal.json", spec)
    (d / "GOAL.md").write_text(render_md(spec), encoding="utf-8")
    if not (d / "PREAPPROVALS.yaml").exists():
        (d / "PREAPPROVALS.yaml").write_text(PREAPPROVAL_TEMPLATE.format(gid=spec["id"]), encoding="utf-8")
    st = {"status": "active", "started": time.time(), "rounds": 0, "needs_plan": True, "empty_plans": 0,
          "criteria": {c["id"]: {"state": "pending"} for c in spec["criteria"]},
          "jobs": {}, "questions": {}, "answers": {}, "actions": {}, "spend": []}
    for j in spec.get("seed_jobs") or []:
        st["jobs"][j["id"]] = {"spec": j, "status": "queued", "from": "seed"}
    save_state(spec["id"], st)
    event(spec["id"], f"goal created: {spec['objective'][:120]}")
    return d


# ---- pre-approvals ----------------------------------------------------------------------------------------------
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def preapprovals(gid, now=None):
    """-> (register dict, why). The register only counts when APPROVAL.json pins this exact file and it has not
    expired; otherwise ({}, the reason) - fail closed."""
    d = gdir(gid)
    p, a = d / "PREAPPROVALS.yaml", _rj(d / "APPROVAL.json")
    if not p.is_file():
        return {}, "no PREAPPROVALS.yaml"
    if not a:
        return {}, "PREAPPROVALS.yaml is not approved (fieldkit goal approve)"
    if a.get("sha256") != _sha(p):
        return {}, "PREAPPROVALS.yaml changed after its approval: void until approved again"
    try:
        reg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return {}, f"PREAPPROVALS.yaml does not parse: {e}"
    exp = reg.get("expires")
    if exp and time.strftime("%Y-%m-%d", time.localtime(now or time.time())) > str(exp):
        return {}, f"the pre-approvals expired on {exp}"
    return reg, f"approved {a.get('at')} by the owner at a terminal (sha256 {a['sha256'][:12]})"


def approve(gid, confirm=None, say=print, isatty=None):
    """The owner reads the whole register and types yes; the file's sha256 is pinned. Never from autopilot."""
    if os.environ.get("FIELDKIT_AUTOPILOT") == "1":
        say("REFUSED: pre-approvals are the owner's; never approved from inside an autopilot run")
        return 3
    p = gdir(gid) / "PREAPPROVALS.yaml"
    if not p.is_file():
        say(f"no {p}")
        return 2
    text = p.read_text(encoding="utf-8")
    try:
        reg = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        say(f"PREAPPROVALS.yaml does not parse: {e}")
        return 2
    problems = check_register(reg)
    say(text)
    for pr in problems:
        say(f"  PROBLEM: {pr}")
    if problems:
        return 2
    if confirm is None:
        if not (isatty if isatty is not None else sys.stdin.isatty()):
            say("REFUSED: approving needs the owner typing yes at a real terminal")
            return 3
        confirm = input(f"Approve every decision above for goal {gid}? Type yes: ").strip().lower() == "yes"
    if not confirm:
        say("not approved")
        return 1
    _wj(gdir(gid) / "APPROVAL.json", {"sha256": _sha(p), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "host": socket.gethostname()})
    event(gid, "pre-approvals approved by the owner")
    say(f"APPROVED: {len(reg.get('answers') or [])} answer rule(s), {len(reg.get('actions') or [])} action rule(s)")
    return 0


def check_register(reg):
    out = []
    for a in reg.get("answers") or []:
        if not a.get("category"):
            out.append(f"answer rule without a category: {a}")
        if not a.get("take_default") and not a.get("answer"):
            out.append(f"answer rule {a.get('category')}: needs take_default: true or answer: TEXT")
    for a in reg.get("actions") or []:
        kind = next(iter(a), None)
        if kind not in ACTIONS:
            out.append(f"unknown action {kind} (known: {ACTIONS})")
            continue
        spec = a[kind] or {}
        if not spec.get("repo") or not spec.get("branches"):
            out.append(f"{kind}: needs repo and branches")
        if any(_glob_match(spec.get("branches", ""), b) for b in PROTECTED_BRANCHES):
            out.append(f"{kind}: branches {spec.get('branches')} would cover main/master: refused")
    return out


def _glob_match(glob, name):
    import fnmatch
    return fnmatch.fnmatchcase(name, glob)


def covered_answer(reg, q):
    for a in reg.get("answers") or []:
        if a.get("category") == q.get("category"):
            if a.get("answer"):
                return a["answer"], a
            if a.get("take_default") and q.get("default"):
                return q["default"], a
    return None, None


def covered_action(reg, kind, repo, branch):
    if branch in PROTECTED_BRANCHES:
        return None
    for a in reg.get("actions") or []:
        spec = a.get(kind)
        if spec and Path(spec["repo"]).resolve() == Path(repo).resolve() and _glob_match(spec["branches"], branch):
            return spec
    return None


def do_action(gid, kind, repo, branch, spec, title="", body="", run=subprocess.run, say=print):
    """push_branch / open_pr, done by the harness (never a model) under a pre-approval. -> (ok, evidence)."""
    if os.environ.get("FIELDKIT_AUTOPILOT") == "1":
        return False, "refused inside an autopilot run (no credential there by design)"
    if branch in PROTECTED_BRANCHES:
        return False, f"refused: {branch} is protected"
    if kind == "push_branch":
        base = spec.get("base", "origin/main")
        chk = run([sys.executable, "-m", "fieldkit", "privacy", "push-check", str(repo), "--range", f"{base}..{branch}"],
                  capture_output=True, text=True)
        if chk.returncode != 0:
            return False, f"privacy push-check refused: {(chk.stdout or chk.stderr).strip()[-400:]}"
        r = run(["git", "-C", str(repo), "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}"],
                capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-400:]
    if kind == "open_pr":
        r = run(["gh", "pr", "create", "--head", branch, "--base", spec.get("base", "main"), "--title",
                 title or f"{gid}: {branch}", "--body", body or f"Opened by fieldkit goal {gid} under a pre-approval."],
                cwd=str(repo), capture_output=True, text=True)
        return r.returncode == 0, (r.stdout + r.stderr).strip()[-400:]
    return False, f"unknown action {kind}"


# ---- the verifier -----------------------------------------------------------------------------------------------
def run_check(c, default_cwd, run=subprocess.run, timeout=3600):
    """One command criterion -> {verdict PASS|FAIL, rc, evidence}. The command's exit code, or a line it printed
    (expect.line), decides; nothing a model said does."""
    cwd = os.path.expandvars(c.get("cwd") or default_cwd or ".")
    shell = (["powershell", "-NoProfile", "-Command",
              "$global:LASTEXITCODE = 0; " + c["run"] + "; if (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE } "
              "else { exit 1 } }; exit $LASTEXITCODE"] if os.name == "nt" else ["bash", "-lc", c["run"]])
    try:
        r = run(shell, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=c.get("timeout", timeout))
        out, rc = (r.stdout or "") + (r.stderr or ""), r.returncode
    except subprocess.TimeoutExpired:
        out, rc = f"TIMEOUT after {c.get('timeout', timeout)} s", 124
    except OSError as e:
        out, rc = f"could not start: {e}", 127
    exp = c.get("expect") or {}
    if exp.get("line"):
        hit = re.search(exp["line"], out, re.M)
        ok = bool(hit) and (rc == 0 or exp.get("any_exit"))
        why = f"line /{exp['line']}/ {'printed' if hit else 'NOT printed'}, exit {rc}"
    else:
        ok = rc == int(exp.get("exit", 0))
        why = f"exit {rc} (expects {exp.get('exit', 0)})"
    return {"verdict": "PASS" if ok else "FAIL", "rc": rc, "why": why, "evidence": out.strip()[-1500:]}


VERDICT = re.compile(r"^\s*VERDICT:\s*(PASS|FAIL)\b(.*)$", re.M)


def agent_check(c, goal, claude, cwd):
    """A criterion only a reader can judge: a SEPARATE fresh headless run (never the worker) that must print
    `VERDICT: PASS` or `VERDICT: FAIL` with its evidence. No verdict line = FAIL (fail closed)."""
    prompt = (f"You are the VERIFIER of goal {goal['id']}, in a fresh context. You did not do the work and you do not "
              "trust any claim of the people who did. Check by reading files, logs and running read-only commands. "
              f"Do not change anything.\n\nQuestion: {c['question']}\n\nEnd with one line: `VERDICT: PASS <evidence>` "
              "or `VERDICT: FAIL <what is missing>`.")
    r = claude(prompt, cwd=cwd, max_turns=VERIFIER_BUDGET["turns"], timeout=VERIFIER_BUDGET["wall_minutes"] * 60)
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    text = str(res.get("result", "") or r.get("stdout", ""))
    m = VERDICT.findall(text)
    if not m:
        return {"verdict": "FAIL", "rc": r.get("rc"), "why": "the verifier printed no VERDICT line",
                "evidence": text[-1500:], "usd": res.get("total_cost_usd")}
    v, ev = m[-1]
    return {"verdict": v, "rc": r.get("rc"), "why": f"verifier: {v}{ev[:200]}", "evidence": text[-1500:],
            "usd": res.get("total_cost_usd")}


def verify(gid, goal, st, ids=None, extra=(), claude=None, run=subprocess.run, who="goal"):
    """Run criteria (all, or ids) and extra job checks; record every verdict in verdicts.jsonl and the criteria
    records. -> list of results."""
    out = []
    for c in [c for c in goal["criteria"] if ids is None or c["id"] in ids] + list(extra):
        if c.get("judge") == "agent":
            r = agent_check(c, goal, claude, c.get("cwd") or goal.get("cwd") or str(gdir(gid)))
            if r.get("usd"):
                st["spend"].append({"t": time.time(), "usd": float(r["usd"]), "who": f"verifier {c['id']}"})
        else:
            r = run_check(c, goal.get("cwd"), run=run)
        r.update(id=c.get("id") or c.get("run", "")[:60], at=time.strftime("%Y-%m-%d %H:%M:%S"), who=who)
        with open(gdir(gid) / "verdicts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if c.get("id") in st["criteria"]:
            st["criteria"][c["id"]] = {"state": "completed" if r["verdict"] == "PASS" else "pending",
                                       "verdict": r["verdict"], "why": r["why"], "at": r["at"]}
        out.append(r)
    return out


def all_pass(st):
    return all(c.get("state") == "completed" for c in st["criteria"].values())


# ---- the planner ------------------------------------------------------------------------------------------------
def _job_lines(gid, st):
    L = []
    for jid, j in st["jobs"].items():
        h = gdir(gid) / "jobs" / jid / "HANDOFF.md"
        L.append(f"- {jid}: {j['status']}" + (f" ({j.get('why', '')[:200]})" if j.get("why") else ""))
        if h.is_file() and j["status"] not in ("verified",):
            L.append("    handoff: " + h.read_text(encoding="utf-8", errors="replace")[-600:].replace("\n", "\n    "))
    return L or ["(no jobs yet)"]


def planner_prompt(gid, goal, st, plan_file, reg, reg_why):
    crit = [f"- {c['id']}: {st['criteria'][c['id']].get('state')} {st['criteria'][c['id']].get('why', '')}"
            for c in goal["criteria"]]
    answers = [f"- {q}: {a['answer']} ({a['by']})" for q, a in st["answers"].items()] or ["(none)"]
    open_q = [f"- {q}: {v['title']}" for q, v in st["questions"].items() if q not in st["answers"]] or ["(none)"]
    cats = sorted({a.get("category") for a in reg.get("answers") or []}) or ["(none)"]
    return f"""You are the PLANNER (manager) of the long horizon goal {gid}. You do not do the work yourself and you do
not judge it: workers do one bounded job each in a fresh session; a separate verifier runs the acceptance criteria.
Only a verified result counts; a worker's own claim never does.

GOAL (goal.json; you may not change it):
{json.dumps(goal, indent=1, ensure_ascii=False)[:12000]}

CRITERIA STATE (verifier records):
{chr(10).join(crit)}

JOBS SO FAR:
{chr(10).join(_job_lines(gid, st))}

OWNER ANSWERS: {chr(10).join(answers)}
OPEN OWNER QUESTIONS: {chr(10).join(open_q)}
PRE-APPROVED ANSWER CATEGORIES ({reg_why}): {', '.join(cats)}

Write the next plan as JSON to this file and nothing else there:
PLAN_FILE: {plan_file}

Shape: {{"signal": "execute" | "done" | "blocked" | "ask", "why": "...",
 "jobs": [{{"id": "lower-case-id", "title": "...", "brief": "the whole job for a worker who knows nothing else",
   "cwd": "absolute folder", "budget": {{"turns": 10000, "wall_minutes": 240, "cost_usd": 20}},
   "touches": ["paths it changes"], "needs": ["job ids that must be verified first"],
   "waits_on": ["question ids it cannot start without"],
   "verify": [{{"run": "a command whose exit code proves this job", "cwd": "...", "expect": {{"exit": 0}}}}]}}],
 "questions": [{{"id": "q-id", "title": "...", "why": "...", "category": "short word", "default": "what you would do",
   "options": ["..."]}}]}}
Rules: keep ids of unfinished jobs stable (a job already verified is never re-run); one job = one bounded piece with
its own verify commands; jobs that touch the same paths are ordered with needs; up to {max_workers(goal)} jobs whose
touches are disjoint run AT THE SAME TIME, so list every path a job changes (a job with no touches runs alone); a job that failed or was paused gets a
better brief (say what the gap is) or is split, never repeated blindly. Ask a question ONLY for a decision listed as
the owner's, and always give a default and a category; plan every job that does not depend on the answer. Say "done"
only when you believe every criterion passes: the verifier will check, and it decides.
"""


def check_plan(plan):
    out = []
    if plan.get("signal") not in SIGNALS:
        out.append(f"signal must be one of {SIGNALS}")
    for j in plan.get("jobs") or []:
        out += [f"job {j.get('id')}: {p}" for p in check_job(j)]
    for q in plan.get("questions") or []:
        if not q.get("id") or not q.get("title") or not q.get("category"):
            out.append(f"question needs id, title and category: {q}")
    return out


def plan(gid, goal, st, claude, say=print):
    """One planner run. -> (plan dict | None, why)."""
    reg, reg_why = preapprovals(gid)
    st["rounds"] += 1
    rd = gdir(gid) / "rounds" / f"{st['rounds']:03d}"
    rd.mkdir(parents=True, exist_ok=True)
    pf = rd / "plan.json"
    b = bud.merge(PLANNER_BUDGET, goal.get("planner_budget"))
    r = claude(planner_prompt(gid, goal, st, pf, reg, reg_why), cwd=goal.get("cwd") or str(gdir(gid)),
               max_turns=b["turns"], timeout=b["wall_minutes"] * 60)
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    if res.get("total_cost_usd"):
        st["spend"].append({"t": time.time(), "usd": float(res["total_cost_usd"]), "who": "planner"})
    kind, detail = bud.classify(r)
    if kind in ("usage_limit", "rate_limit"):
        return None, f"{kind}: {detail}"
    p = _rj(pf)
    if not isinstance(p, dict):
        return None, f"the planner wrote no readable {pf} ({kind}: {detail[:200]})"
    problems = check_plan(p)
    if problems:
        return None, "plan refused: " + "; ".join(problems)
    return p, "ok"


def apply_plan(gid, goal, st, p, reg, inbox_add, say=print):
    """Merge the plan into the state. Verified/done jobs never re-open; questions go through the pre-approvals
    first and to the inbox only when nothing covers them."""
    for j in p.get("jobs") or []:
        cur = st["jobs"].get(j["id"])
        if cur and cur["status"] in ("verified", "running"):
            continue
        status = cur["status"] if cur and cur["status"] in ("paused-resumable",) else "queued"
        st["jobs"][j["id"]] = {"spec": j, "status": status, "from": f"round {st['rounds']}",
                               "attempts": (cur or {}).get("attempts", 0)}
    for q in p.get("questions") or []:
        if q["id"] in st["answers"] or q["id"] in st["questions"]:
            continue
        ans, rule = covered_answer(reg, q)
        if ans is not None:
            st["answers"][q["id"]] = {"answer": ans, "by": f"pre-approval ({rule.get('category')})"}
            event(gid, f"question {q['id']} answered by pre-approval: {ans[:120]}")
            continue
        item = inbox_add(f"goal {gid}: {q['title']}", f"{q.get('why', '')} Default if you agree: {q.get('default')}. "
                         f"Answer: fieldkit inbox done N \"your answer\" (empty = the default).",
                         kind="decision")
        st["questions"][q["id"]] = {**q, "inbox": item["n"]}
        event(gid, f"question {q['id']} -> inbox #{item['n']} (no pre-approval covers category {q.get('category')})")


def collect_answers(gid, st, items):
    """Inbox answers to this goal's questions -> st['answers']; True when there is a new one."""
    new_ = False
    done = {it["n"]: it for it in items if it.get("state") == "done"}
    for qid, q in st["questions"].items():
        if qid not in st["answers"] and q.get("inbox") in done:
            note = (done[q["inbox"]].get("note") or "").strip()
            st["answers"][qid] = {"answer": note or q.get("default") or "", "by": f"the owner (inbox #{q['inbox']})"}
            event(gid, f"question {qid} answered by the owner: {st['answers'][qid]['answer'][:120]}")
            new_ = True
    return new_


# ---- budgets for the whole goal ---------------------------------------------------------------------------------
def spent(st, since=0):
    return sum(s["usd"] for s in st["spend"] if s["t"] >= since)


def budget_block(goal, st, now):
    """-> (state, why, until) : ("ok"|"pause"|"blocked", reason, epoch)."""
    b = goal.get("budget") or {}
    if b.get("days") and now - st["started"] > float(b["days"]) * 86400:
        return "blocked", f"the goal's {b['days']} days are used up", None
    if b.get("cost_usd") and spent(st) >= float(b["cost_usd"]):
        return "blocked", f"the goal's ${b['cost_usd']} are spent (${spent(st):.2f})", None
    if b.get("window_usd"):
        win = float(b.get("window_hours", 5)) * 3600
        recent = [s for s in st["spend"] if s["t"] >= now - win]
        if sum(s["usd"] for s in recent) >= float(b["window_usd"]):
            until = min(s["t"] for s in recent) + win
            return "pause", f"${b['window_usd']} per {b.get('window_hours', 5)} h reached: spreading the usage", until
    if b.get("cost_usd") and b.get("days"):
        per_day = float(b["cost_usd"]) / float(b["days"])
        day0 = now - (now - st["started"]) % 86400
        if spent(st, day0) >= per_day * 1.5:
            return "pause", f"today's share (${per_day:.2f}/day x1.5) is spent: the budget must last {b['days']} days", day0 + 86400
    return "ok", "", None


# ---- one round --------------------------------------------------------------------------------------------------
def max_workers(goal):
    return max(1, int((goal or {}).get("max_workers") or MAX_WORKERS))


def _touches(s):
    """A job's touches as normalized absolute paths; None = it declared none (it may touch anything)."""
    t = s.get("touches") or []
    if not t:
        return None
    base = os.path.expandvars(s.get("cwd") or ".")
    return {os.path.normcase(os.path.normpath(os.path.join(base, os.path.expandvars(str(x))))) for x in t}


def _overlap(a, b):
    """Two touch sets collide when one path is the other or lies under it; None (undeclared) collides with all."""
    if a is None or b is None:
        return True
    for x in a:
        for y in b:
            try:
                if os.path.commonpath([x, y]) in (x, y):
                    return True
            except ValueError:          # different drives
                continue
    return False


def startable(st, t=None, limit=1):
    """-> up to limit job ids that may start now: needs verified, owner answers in, not paused, and touches disjoint
    from every running job and from each other."""
    t = t or time.time()
    done = {j for j, v in st["jobs"].items() if v["status"] == "verified"}
    busy = [_touches(v["spec"]) for v in st["jobs"].values() if v["status"] == "running"]
    out = []
    for jid, j in st["jobs"].items():
        if len(out) >= limit:
            break
        if j["status"] not in ("queued", "paused-resumable"):
            continue
        s = j["spec"]
        if any(n not in done for n in s.get("needs") or []):
            continue
        if any(q not in st["answers"] for q in s.get("waits_on") or []):
            continue
        if (j.get("paused_until") or 0) > t:
            continue
        mine = _touches(s)
        if any(_overlap(mine, b) for b in busy):
            continue
        out.append(jid)
        busy.append(mine)
    return out


def runnable(st, t=None):
    return (startable(st, t, 1) or [None])[0]


# ---- workers: one thread per running job ------------------------------------------------------------------------
_POOL = None
_WORKERS = {}                        # gid -> {jid: {"future", "spend": [..]}} (this process only)
_IO = threading.Lock()               # inbox numbering and the lock file are shared by the worker threads


def _pool():
    global _POOL
    if _POOL is None:
        _POOL = futures.ThreadPoolExecutor(max_workers=32, thread_name_prefix="goal-worker")
    return _POOL


def inflight(gid):
    """Dollars the running workers of this goal spent so far (merged into state.json when each one ends)."""
    return [x for w in list(_WORKERS.get(gid, {}).values()) for x in list(w["spend"])]


def running(gid):
    return sorted(_WORKERS.get(gid, {}))


def _recover_orphans(gid, st):
    """A job "running" in state.json with no worker in this process belongs to a supervisor that died (the lock
    makes this one the only one): it resumes from its checkpoint."""
    mine = _WORKERS.get(gid, {})
    for jid, j in st["jobs"].items():
        if j["status"] == "running" and jid not in mine:
            j.update(status="paused-resumable", why="its supervisor stopped while it ran: resumes from its checkpoint",
                     paused_until=None)
            event(gid, f"job {jid}: no worker alive for it - paused-resumable (resumes from its checkpoint)")


def _start_job(gid, goal, st, jid, run_job, sleep, now, say, inbox_add):
    j = st["jobs"][jid]
    s = j["spec"]
    j["status"] = "running"
    j["attempts"] = j.get("attempts", 0) + 1
    event(gid, f"job {jid} starts (attempt {j['attempts']})")
    brief = (f"{s['brief']}\n\n---\nGoal {gid}: {goal['objective'][:600]}\nConstraints:\n" +
             "\n".join(f"- {c}" for c in goal.get("constraints") or []) +
             "\nYour result is judged by a separate verifier running: " +
             "; ".join(v["run"] for v in s.get("verify") or []) +
             ". Your own claim of success does not count. What only the owner may decide goes to "
             "`fieldkit inbox add`; carry on with what does not depend on it.")
    answers = {q: st["answers"][q]["answer"] for q in s.get("waits_on") or [] if q in st["answers"]}
    if answers:
        brief += "\nOwner answers you need: " + json.dumps(answers, ensure_ascii=False)
    spend_log = []

    def spend(usd, who):
        spend_log.append({"t": time.time(), "usd": usd, "who": f"job {who}"})

    def add(*a, **k):
        with _IO:
            return inbox_add(*a, **k)

    fut = _pool().submit(run_job, gdir(gid) / "jobs" / jid, brief, s["cwd"],
                         budget=bud.merge(goal.get("worker_budget"), s.get("budget")), job_id=f"{gid}-{jid}",
                         max_continuations=int(s.get("max_continuations", 3)), fallback=s.get("fallback"),
                         model=s.get("model"), sleep=sleep, now=now, say=say, inbox_add=add, on_spend=spend)
    _WORKERS.setdefault(gid, {})[jid] = {"future": fut, "spend": spend_log}


def _end_job(gid, goal, st, jid, claude, run):
    """A finished worker -> its spend into state, the verifier on its checks, the job's new status."""
    w = _WORKERS[gid].pop(jid)
    st["spend"] += w["spend"]
    try:
        r = w["future"].result()
    except Exception as e:  # noqa: BLE001
        r = {"status": "failed", "why": f"the worker crashed: {type(e).__name__}: {e}"}
    j = st["jobs"][jid]
    s = j["spec"]
    if r["status"] == "done":
        checks = [dict(v, id=f"{jid}:{i}") for i, v in enumerate(s.get("verify") or [], 1)]
        res = verify(gid, goal, st, extra=checks, claude=claude, run=run, who=f"job {jid}")
        own = [x for x in res if str(x["id"]).startswith(f"{jid}:")]
        if all(x["verdict"] == "PASS" for x in own):
            j.update(status="verified", why=f"verifier PASS on {len(own)} check(s)")
        else:
            bad = [f"{x['id']} {x['why']}" for x in own if x["verdict"] != "PASS"]
            j.update(status="failed-verify", why=f"the worker said done; the verifier says FAIL: {bad}")
            if j["attempts"] < 3:
                j["status"] = "queued"          # the planner gets the gap and a better brief may follow
    elif r["status"] == "paused-resumable":
        cp = r.get("checkpoint") or {}
        j.update(status="paused-resumable", why=r.get("why", ""), paused_until=cp.get("paused_until"))
    else:
        j.update(status="failed", why=r.get("why", "")[:400])
    event(gid, f"job {jid}: {j['status']} - {j.get('why', '')[:200]}")
    st["needs_plan"] = True
    return f"job {jid}: {j['status']}"


def _end_finished(gid, goal, st, claude, run):
    return [_end_job(gid, goal, st, jid, claude, run)
            for jid, w in list(_WORKERS.get(gid, {}).items()) if w["future"].done()]


def _all_done(st):
    return all_pass(st) and not any(v["status"] in ("queued", "running", "paused-resumable") for v in st["jobs"].values())


def step(gid, claude=None, run_job=None, run=subprocess.run, inbox_add=None, inbox_items=None, now=time.time,
         sleep=time.sleep, say=print, action=None):
    """One round of one goal. -> {"status", "why", "wait_until"}."""
    if claude is None:
        from .run import claude
    if run_job is None:
        from .job import run_job
    from . import inbox
    inbox_add = inbox_add or inbox.add
    inbox_items = inbox_items or (lambda: inbox.items(None))
    action = action or do_action
    goal, st = load(gid)
    if not goal or not st:
        return {"status": "missing", "why": f"no goal {gid}"}
    if st["status"] in ("done", "blocked", "stopped"):
        return {"status": st["status"], "why": st.get("why", "")}
    if (gdir(gid) / "STOP").exists() or hardlines.STOP_FILE.exists():
        st.update(status="stopped", why="STOP file")
        save_state(gid, st)
        event(gid, "stopped by a STOP file")
        return {"status": "stopped", "why": "STOP file"}
    _recover_orphans(gid, st)
    t = now()
    if (st.get("paused_until") or 0) > t:
        return {"status": "waiting", "why": st.get("pause_why", ""), "wait_until": st["paused_until"]}
    state, why, until = budget_block(goal, dict(st, spend=st["spend"] + inflight(gid)), t)
    if state == "blocked":
        return _finish(gid, st, "blocked", why, inbox_add)
    if state == "pause":
        st.update(paused_until=until, pause_why=why)
        save_state(gid, st)
        event(gid, f"paused: {why}")
        return {"status": "waiting", "why": why, "wait_until": until}
    ended = _end_finished(gid, goal, st, claude, run)
    if collect_answers(gid, st, inbox_items()):
        st["needs_plan"] = True
    reg, _ = preapprovals(gid)
    if ended and _all_done(st):
        _actions(gid, goal, st, reg, action, say)
        return _finish(gid, st, "done", "every criterion PASSed (verifier)", inbox_add)
    if st.get("needs_plan"):
        if st["rounds"] >= int(goal.get("max_rounds", MAX_ROUNDS)):
            return _finish(gid, st, "blocked", f"{st['rounds']} planner rounds without reaching the criteria", inbox_add)
        p, why = plan(gid, goal, st, claude, say)
        if p is None:
            st["plan_failures"] = st.get("plan_failures", 0) + 1
            if why.startswith(("usage_limit", "rate_limit")):
                tt, how = bud.reset_at(why, t) if why.startswith("usage_limit") else (t + 600, "rate limit")
                st.update(paused_until=tt + 60, pause_why=f"planner: {why[:150]} ({how})")
                save_state(gid, st)
                return {"status": "waiting", "why": st["pause_why"], "wait_until": st["paused_until"]}
            event(gid, f"planner round {st['rounds']} failed: {why}")
            if st["plan_failures"] >= 3:
                return _finish(gid, st, "blocked", f"the planner failed 3 times: {why}", inbox_add)
            save_state(gid, st)
            return {"status": "active", "why": why}
        st["plan_failures"] = 0
        st["needs_plan"] = False
        apply_plan(gid, goal, st, p, reg, inbox_add, say)
        event(gid, f"plan round {st['rounds']}: {p['signal']} - {p.get('why', '')[:150]}; "
                   f"jobs {[j['id'] for j in p.get('jobs') or []]}")
        if (p["signal"] == "done" or (p["signal"] == "blocked" and not p.get("jobs"))) and not running(gid):
            verify(gid, goal, st, claude=claude, run=run, who="final")
            if all_pass(st):
                _actions(gid, goal, st, reg, action, say)
                return _finish(gid, st, "done", "every criterion PASSed (verifier)", inbox_add)
            failing = [k for k, v in st["criteria"].items() if v.get("state") != "completed"]
            st["needs_plan"] = True
            st["verifier_note"] = f"the planner said {p['signal']}; the verifier says FAIL on {failing}"
            event(gid, f"verifier wins: {st['verifier_note']}")
            if p["signal"] == "blocked":
                return _finish(gid, st, "blocked", p.get("why", "the planner sees no way on"), inbox_add)
            save_state(gid, st)
            return {"status": "active", "why": st["verifier_note"]}
        if not p.get("jobs"):
            st["empty_plans"] = st.get("empty_plans", 0) + 1
    for jid in startable(st, t, max_workers(goal) - len(running(gid))):
        _start_job(gid, goal, st, jid, run_job, sleep, now, say, inbox_add)
    if not running(gid):
        waiting_q = [q for q in st["questions"] if q not in st["answers"]]
        if waiting_q:
            save_state(gid, st)
            return {"status": "waiting", "why": f"only owner questions are left: {waiting_q}", "wait_until": t + IDLE_POLL}
        pending = [j for j, v in st["jobs"].items() if v["status"] in ("queued", "paused-resumable")]
        if pending:
            until = min((st["jobs"][j].get("paused_until") or t + IDLE_POLL) for j in pending)
            save_state(gid, st)
            return {"status": "waiting", "why": f"jobs not runnable yet: {pending}", "wait_until": until}
        if st.get("empty_plans", 0) >= 2:
            return _finish(gid, st, "blocked", "the planner returned no work twice and the criteria are not met", inbox_add)
        st["needs_plan"] = True
        save_state(gid, st)
        return {"status": "active", "why": "nothing queued: re-plan"}
    st["empty_plans"] = 0
    save_state(gid, st)
    futures.wait([w["future"] for w in _WORKERS[gid].values()], return_when=futures.FIRST_COMPLETED)
    ended = _end_finished(gid, goal, st, claude, run)
    if _all_done(st):
        _actions(gid, goal, st, reg, action, say)
        return _finish(gid, st, "done", "every criterion PASSed (verifier)", inbox_add)
    save_state(gid, st)
    left = running(gid)
    return {"status": "active", "why": "; ".join(ended) + (f"; still running: {left}" if left else "")}


def expand_branches(repo, branch, run=subprocess.run):
    """A branch name, or a glob (night/*) -> the local branches it names (job branch names are not known when the
    goal is written)."""
    if not any(ch in branch for ch in "*?["):
        return [branch]
    r = run(["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", f"refs/heads/{branch}"],
            capture_output=True, text=True)
    return [b for b in (r.stdout or "").split() if b]


def _actions(gid, goal, st, reg, action, say):
    """At the end: the pre-approved harness actions for the goal's branches; the rest to the inbox."""
    todo = []
    for a in goal.get("on_done") or []:
        for b in expand_branches(a.get("repo", "."), a.get("branch", "")) if a.get("repo") else []:
            todo.append(dict(a, branch=b))
    for a in todo:
        kind, repo, branch = a.get("kind"), a.get("repo"), a.get("branch")
        key = f"{kind}:{repo}:{branch}"
        if st["actions"].get(key, {}).get("ok"):
            continue
        spec = covered_action(reg, kind, repo, branch) if kind in ACTIONS else None
        if spec is None:
            st["actions"][key] = {"ok": False, "why": "no pre-approval covers it: in the inbox"}
            from .inbox import add
            add(f"goal {gid}: {kind} {branch}", f"the goal is done; {kind} of {branch} in {repo} is not pre-approved",
                a.get("command", ""), kind="approval")
            continue
        ok, ev = action(gid, kind, repo, branch, spec, title=a.get("title", ""), body=a.get("body", ""))
        st["actions"][key] = {"ok": ok, "why": ev, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        event(gid, f"action {key}: {'done' if ok else 'FAILED'} - {ev[:200]}")


def _finish(gid, st, status, why, inbox_add):
    st.update(status=status, why=why, finished=time.strftime("%Y-%m-%d %H:%M:%S"))
    save_state(gid, st)
    event(gid, f"goal {status}: {why}")
    report(gid)
    inbox_add(f"goal {gid}: {status}", f"{why}. Report: {gdir(gid) / 'REPORT.md'}", kind="report")
    return {"status": status, "why": why}


def _overhead(gid):
    """fieldkit overhead's line for this goal (WARN over overhead.max_ratio); a broken meter says so, never raises."""
    try:
        from .. import overhead
        return overhead.goal_line(gid, goals=GOALS)
    except Exception as e:  # noqa: BLE001
        return f"OVERHEAD: not measured ({type(e).__name__})"


def report(gid):
    goal, st = load(gid)
    L = [f"# Goal {gid}: {st['status']}", "", f"Written {time.strftime('%Y-%m-%d %H:%M:%S')} by fieldkit goal from "
         "the verifier's records (no model wrote it).", "", f"Why: {st.get('why', '')}", "",
         f"Spent: ${spent(st):.2f} in {len(st['spend'])} run(s); planner rounds {st['rounds']}", ""]
    oh = _overhead(gid)
    if oh:
        L += [oh, ""]
    L += ["| criterion | state | verdict |", "|---|---|---|"]
    for k, v in st["criteria"].items():
        L.append(f"| {k} | {v.get('state')} | {v.get('why', '')[:120]} |")
    L += ["", "| job | state | why |", "|---|---|---|"]
    for k, v in st["jobs"].items():
        L.append(f"| {k} | {v['status']} | {str(v.get('why', ''))[:160]} |")
    if st["answers"]:
        L += ["", "Decisions:"] + [f"- {q}: {a['answer']} ({a['by']})" for q, a in st["answers"].items()]
    if st["actions"]:
        L += ["", "Harness actions:"] + [f"- {k}: {'done' if v['ok'] else 'not done'} ({v['why'][:160]})"
                                         for k, v in st["actions"].items()]
    (gdir(gid) / "REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ---- the supervisor ---------------------------------------------------------------------------------------------
def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        return True


def lock(now=time.time):
    """-> (True, path) or (False, why). One supervisor at a time; a dead or silent one is taken over."""
    GOALS.mkdir(parents=True, exist_ok=True)
    p = GOALS / ".supervisor.lock"
    cur = _rj(p)
    if cur and cur.get("pid") != os.getpid() and _alive(cur.get("pid")) and now() - cur.get("heartbeat", 0) < LOCK_STALE:
        return False, f"another supervisor runs (PID {cur['pid']}, heartbeat {int(now() - cur['heartbeat'])} s ago)"
    _wj(p, {"pid": os.getpid(), "host": socket.gethostname(), "started": now(), "heartbeat": now()})
    return True, str(p)


def heartbeat(now=time.time, note=None):
    p = GOALS / ".supervisor.lock"
    with _IO:
        cur = _rj(p) or {}
        if cur.get("pid") == os.getpid():
            cur["heartbeat"] = now()
            if note is not None:
                cur["note"] = note
            cur["running"] = {g: sorted(w) for g, w in _WORKERS.items() if w}
            _wj(p, cur)


def _heartbeat_thread(now, every=HEARTBEAT_EVERY):
    """Keeps the lock fresh while the main thread blocks on a worker, the planner or the verifier (seen 2026-10-26:
    7,134 s stale while a worker ran, which reads as a hang and lets a second supervisor take over after 15 min).
    -> the Event that stops it."""
    stop_ev = threading.Event()

    def beat():
        while not stop_ev.wait(every):
            try:
                heartbeat(now)
            except OSError:
                pass
    threading.Thread(target=beat, name="goal-heartbeat", daemon=True).start()
    return stop_ev


def unlock():
    p = GOALS / ".supervisor.lock"
    if (_rj(p) or {}).get("pid") == os.getpid():
        p.unlink(missing_ok=True)


def unfinished():
    return sorted(d.name for d in GOALS.iterdir() if (d / "goal.json").is_file()
                  and (_rj(d / "state.json") or {}).get("status") in ("active", "waiting")) if GOALS.is_dir() else []


def supervise(ids=None, once=False, sleep=time.sleep, now=time.time, say=print, hold=True, **kw):
    """Run every unfinished goal until none can go on. A goal that waits does not stop the others."""
    out = say

    def say(msg):                      # the scheduled task's window may be closed: the log keeps every line
        out(msg)
        try:
            GOALS.mkdir(parents=True, exist_ok=True)
            with open(GOALS / "supervisor.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  PID {os.getpid()}  {msg}\n")
        except OSError:
            pass
    ok, why = lock(now)
    if not ok:
        say(f"SUPERVISOR: {why}; this one exits")
        return 0
    beat = _heartbeat_thread(now)
    if hold:
        try:
            from ..buildh.window import hold_awake
            hold_awake(os.getpid())
        except Exception as e:  # noqa: BLE001
            say(f"  [WARN] stay awake: not held ({e})")
    try:
        while True:
            goals = [g for g in (ids or unfinished())]
            if not goals:
                say("SUPERVISOR: no unfinished goal")
                return 0
            waits, progressed = [], False
            for gid in goals:
                heartbeat(now, f"goal {gid}")
                r = step(gid, sleep=lambda s: (heartbeat(now), sleep(s)), now=now, say=say, **kw)
                say(f"[{time.strftime('%H:%M:%S')}] {gid}: {r['status']} - {r.get('why', '')[:200]}")
                if r["status"] == "waiting":
                    waits.append(r.get("wait_until") or now() + IDLE_POLL)
                elif r["status"] == "active":
                    progressed = True
            if once:
                return 0
            live = [g for g in goals if (load(g)[1] or {}).get("status") in ("active", "waiting")]
            if not live:
                say("SUPERVISOR: every goal is finished (done, blocked or stopped)")
                return 0
            if ids:
                ids = live
            if not progressed and waits:
                until = min(waits)
                say(f"SUPERVISOR: every goal waits; sleeping until {time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}")
                while now() < until:
                    if hardlines.STOP_FILE.exists():
                        break
                    heartbeat(now, "sleeping")
                    sleep(min(60, max(0.0, until - now())))
                for g in live:
                    _, s = load(g)
                    if s.get("paused_until") and s["paused_until"] <= now():
                        s.pop("paused_until", None)
                        save_state(g, s)
    finally:
        beat.set()
        unlock()


def stop(gid=None, why="the owner said stop"):
    targets = [gid] if gid else unfinished()
    for g in targets:
        (gdir(g) / "STOP").write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {why}\n", encoding="utf-8")
    return targets


def status_lines(gid=None):
    out = []
    for g in [gid] if gid else sorted(d.name for d in GOALS.iterdir() if (d / "goal.json").is_file()) if GOALS.is_dir() else []:
        goal, st = load(g)
        if not goal:
            out.append(f"GOAL {g}: missing")
            continue
        if not st:      # 2026-10-25: a goal folder copied between machines without its state.json crashed status
            out.append(f"GOAL {g}: not started on this machine (goal.json without state.json); the supervisor "
                       "leaves it alone")
            continue
        passed = sum(1 for c in st["criteria"].values() if c.get("state") == "completed")
        _, reg_why = preapprovals(g)
        out.append(f"GOAL {g}: {st['status']} - criteria {passed}/{len(st['criteria'])} PASS, ${spent(st):.2f} spent, "
                   f"round {st['rounds']}" + (f", paused until {time.strftime('%Y-%m-%d %H:%M', time.localtime(st['paused_until']))}"
                                              if st.get("paused_until") else ""))
        out.append(f"  pre-approvals: {reg_why}")
        oh = _overhead(g)
        if oh:
            out.append("  " + oh)
        for k, v in st["criteria"].items():
            out.append(f"  [{v.get('state', 'pending'):<9}] {k}: {v.get('why', '')[:100]}")
        for k, v in st["jobs"].items():
            out.append(f"  job {k:<24} {v['status']:<17} {str(v.get('why', ''))[:90]}")
        for q, v in st["questions"].items():
            if q not in st["answers"]:
                out.append(f"  WAITS for the owner: inbox #{v.get('inbox')} {v['title'][:90]}")
    lk = _rj(GOALS / ".supervisor.lock")
    if lk:
        out.append(f"SUPERVISOR: PID {lk['pid']} on {lk.get('host')}, heartbeat {int(time.time() - lk['heartbeat'])} s ago"
                   + (" (alive)" if _alive(lk["pid"]) else " (dead: the next start takes over)"))
    return out or ["no goals"]


# ---- the scheduled task (the owner registers it once) -----------------------------------------------------------
TASK_NAME = "Fieldkit goal supervisor"


def task_xml(python, fieldkit_root, user=None):
    """Task Scheduler XML: at logon and on resume from sleep (Power-Troubleshooter event 1), the user's own context
    (InteractiveToken: no stored password, not SYSTEM), one instance, no time limit, runs on battery too (the run
    itself pauses at checkpoints)."""
    from xml.sax.saxutils import escape
    user = user or f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    q = "&lt;QueryList&gt;&lt;Query Id=\"0\" Path=\"System\"&gt;&lt;Select Path=\"System\"&gt;*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Resumes every unfinished Fieldkit goal (fieldkit goal run). Written by fieldkit goal supervisor-script.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId><Delay>PT2M</Delay></LogonTrigger>
    <EventTrigger><Enabled>true</Enabled><Subscription>{q}</Subscription><Delay>PT2M</Delay></EventTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(python)}</Command><Arguments>-m fieldkit goal run</Arguments><WorkingDirectory>{escape(str(fieldkit_root))}</WorkingDirectory></Exec></Actions>
</Task>
"""


UNIT = "fieldkit-goal"


def systemd_units(python, fieldkit_root):
    """Linux (2026-10-25): systemd USER units, no root. The service runs the supervisor once; the timer starts it 2 min
    after boot (with linger, before anyone logs in) and on the quarter hour by the wall clock, so it also fires after a
    resume from sleep (a monotonic timer does not count suspended time) and a missed one runs at once (Persistent).
    A second start while one runs exits at the lock. The supervisor holds idle sleep off itself (hold_awake:
    systemd-inhibit), so the unit does not wrap it."""
    service = f"""[Unit]
Description=Fieldkit goal supervisor: resumes every unfinished goal (fieldkit goal run)
Documentation=file://{fieldkit_root}/fieldkit/autopilot/goal.py

[Service]
Type=simple
WorkingDirectory={fieldkit_root}
ExecStart={python} -m fieldkit goal run
Nice=10
"""
    timer = f"""[Unit]
Description=Start the Fieldkit goal supervisor after boot and every 15 minutes (after resume too)

[Timer]
OnBootSec=2min
OnCalendar=*:0/15
Persistent=true
Unit={UNIT}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def supervisor_script_linux(out, python=None, fieldkit_root=None):
    """The Linux twin of the scheduled task: one bash script that checks first, installs the two user units, enables
    the timer and linger, and ends with a REGISTER line. -> (script, service, timer) paths."""
    python = python or sys.executable
    fieldkit_root = fieldkit_root or ROOT
    out = Path(out)
    service, timer = systemd_units(python, fieldkit_root)
    svc_p, tmr_p = out.with_name(f"{UNIT}.service"), out.with_name(f"{UNIT}.timer")
    svc_p.write_text(service, encoding="utf-8")
    tmr_p.write_text(timer, encoding="utf-8")
    sh = f"""#!/bin/sh
# Registers the Fieldkit goal supervisor on Linux (written by: fieldkit goal supervisor-script). Run once, as yourself.
# What it does: systemd USER units {UNIT}.service + {UNIT}.timer in ~/.config/systemd/user (no root, not system-wide)
#   that run "{python} -m fieldkit goal run" in {fieldkit_root}
#   2 minutes after boot and every 15 minutes (a missed start, e.g. while asleep, runs at once).
# loginctl enable-linger lets it start after a reboot before you log in; if it is refused, it starts at your login.
# Who can act through it: only code already in that folder.
# Undo: systemctl --user disable --now {UNIT}.timer; rm ~/.config/systemd/user/{UNIT}.service ~/.config/systemd/user/{UNIT}.timer
set -u
cd "{fieldkit_root}" || {{ echo "REGISTER: REFUSED - {fieldkit_root} is missing"; exit 3; }}
"{python}" -m fieldkit goal status || {{ echo "REGISTER: REFUSED - fieldkit goal does not run from this folder"; exit 3; }}
command -v systemctl >/dev/null || {{ echo "REGISTER: REFUSED - no systemctl (not a systemd system)"; exit 3; }}
D="${{XDG_CONFIG_HOME:-$HOME/.config}}/systemd/user"
mkdir -p "$D" && cp "{svc_p}" "{tmr_p}" "$D/" || {{ echo "REGISTER: FAILED - cannot write $D"; exit 1; }}
systemctl --user daemon-reload && systemctl --user enable --now {UNIT}.timer || {{ echo "REGISTER: FAILED - systemctl --user"; exit 1; }}
loginctl enable-linger "$(id -un)" 2>/dev/null || echo "  linger: refused here (starts at your login instead)"
echo "  linger: $(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)"
systemctl --user list-timers {UNIT}.timer --no-pager | head -3
systemctl --user is-enabled {UNIT}.timer >/dev/null && systemctl --user is-active {UNIT}.timer >/dev/null \\
  && echo "REGISTER: OK - the supervisor starts after boot and every 15 minutes, after resume too" \\
  || {{ echo "REGISTER: FAILED - the timer is not enabled and active"; exit 1; }}
"""
    out.write_text(sh, encoding="utf-8", newline="\n")
    out.chmod(0o755)
    return out, svc_p, tmr_p


def supervisor_script(out, python=None, fieldkit_root=None):
    """The one script the owner runs (PowerShell, no elevation needed for a task in the owner's own context; if
    Windows refuses, the same script in an elevated window = one UAC click). It checks before it registers and
    ends with a verdict line."""
    python = python or sys.executable
    fieldkit_root = fieldkit_root or ROOT
    xml_path = Path(out).with_suffix(".xml")
    xml_path.write_text(task_xml(python, fieldkit_root), encoding="utf-16")
    ps = f"""# Registers the Fieldkit goal supervisor (written by: fieldkit goal supervisor-script). Run once.
# What it does: a scheduled task "{TASK_NAME}" in YOUR user context (not SYSTEM, no stored password) that runs
#   "{python}" -m fieldkit goal run
# in {fieldkit_root} at logon and after resume from sleep. Who can act through it: only code already in that folder.
# Undo: schtasks /Delete /TN "{TASK_NAME}" /F
$ErrorActionPreference = 'Stop'
cd "{fieldkit_root}"
& "{python}" -m fieldkit goal status | Out-Host
if ($LASTEXITCODE -ne 0) {{ Write-Host 'REGISTER: REFUSED - fieldkit goal does not run from this folder (is the branch merged?)'; exit 3 }}
schtasks /Create /TN "{TASK_NAME}" /XML "{xml_path}" /F
if ($LASTEXITCODE -ne 0) {{ Write-Host 'REGISTER: FAILED - if it said Access is denied, run this same script in an elevated PowerShell'; exit $LASTEXITCODE }}
schtasks /Query /TN "{TASK_NAME}" /V /FO LIST | Select-String 'TaskName|Status|Run As User|Task To Run' | Out-Host
Write-Host 'REGISTER: OK - the supervisor starts at logon and after resume from sleep'
"""
    Path(out).write_text(ps, encoding="utf-8-sig")
    return Path(out), xml_path
