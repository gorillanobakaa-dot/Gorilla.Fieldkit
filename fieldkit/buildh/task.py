"""task - a long job cut into small checked steps, with a checkpoint after each one.

Built for small models with small context windows. The model never holds the whole job:

    start  -> a plan of steps (script steps and model steps)
    approve (the owner; there is no way to approve over MCP)
    next   -> script steps run by themselves; a model step comes back as ONE packet:
              what to do, the only files it may change, the text it needs (trimmed to the
              context budget), and how it will be checked
    submit -> the harness runs the step's check, not the model:
              pass: checkpoint (a git commit in the working copy), move on
              fail: every change is put back to the last checkpoint, attempt n+1;
                    after max_attempts the step is BLOCKED and waits for the owner
    status / log

Everything lives on disk (state/build-harness/<task>/), so a fresh chat with an empty
context carries on from the last checkpoint. Every event goes to journal.jsonl, which the
recorder reads.

A step is a dict:
    {id, kind: "script"|"model"|"owner", title,        owner: stops and waits for the owner
     run:    "module:function"            script steps: does the work, may return {"add_steps": [...]}
     packet: "module:function"            model steps: builds the packet text
     check:  "module:function"            model steps: -> {"ok": bool, "why": [..]}
     allowed: [relative paths]            model steps: the only files it may change
     args: {...}, max_attempts: 3}
Workflows (firefox.py, kernel.py) supply the steps.
"""
import hashlib
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

from ..core import settings

STATE = settings.ROOT / "state" / "build-harness"
CHARS_PER_TOKEN = 4                     # a deliberate over-estimate for code, so packets never overflow
PACKET_SHARE = 0.4                      # at most 40% of the context window goes to one packet


class Refused(Exception):
    """The request is not allowed in the current state; the message says what to do instead."""


def _call(dotted, *a, **kw):
    mod, fn = dotted.split(":")
    return getattr(importlib.import_module(mod), fn)(*a, **kw)


def _dir(task_id):
    return STATE / task_id


def load(task_id):
    f = _dir(task_id) / "task.json"
    if not f.is_file():
        raise Refused(f"no task {task_id!r}; start one with: fieldkit build-harness start firefox|kernel")
    return json.loads(f.read_text(encoding="utf-8"))


def save(t):
    d = _dir(t["id"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps(t, indent=1), encoding="utf-8")


ZERO = "0" * 16


def line_hash(line):
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def _last_line(path):
    """The journal's last non-empty line, without reading the whole file."""
    if not path.is_file():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        back = 65536
        while True:
            f.seek(max(0, size - back))
            chunk = f.read().decode("utf-8", "replace").splitlines()
            lines = [l for l in chunk if l.strip()]
            if len(lines) >= 2 or size <= back:
                return lines[-1] if lines else ""
            back *= 4


def journal(t, event, **data):
    """Append one event. Each carries the hash of the line before it, so an edit, a deletion or a
    reorder anywhere in the history breaks the chain and the audit says so."""
    p = _dir(t["id"]) / "journal.jsonl"
    prev = line_hash(_last_line(p)) if p.is_file() and p.stat().st_size else ZERO
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "task": t["id"], "event": event,
                            "prev": prev, **data}) + "\n")


def verify_journal(task_id):
    """-> (problems, count, head). Lines before the chain began (no 'prev') are legacy; after that every
    line must carry the hash of the line before it."""
    p = _dir(task_id) / "journal.jsonl"
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.is_file() else []
    problems, chained = [], False
    for i, line in enumerate(lines):
        try:
            d = json.loads(line)
        except ValueError:
            problems.append(f"line {i + 1} is not valid JSON")
            continue
        if "prev" in d:
            chained = True
            want = line_hash(lines[i - 1]) if i else ZERO
            if d["prev"] != want and not (i == 0 and d["prev"] == ZERO):
                problems.append(f"line {i + 1} ({d.get('event')}): chain broken - an earlier line was changed, removed or reordered")
        elif chained:
            problems.append(f"line {i + 1} ({d.get('event')}): no hash, but the chain had already started")
    return problems, len(lines), line_hash(lines[-1]) if lines else ZERO


def plan_hash(steps):
    keep = [{k: s.get(k) for k in ("id", "kind", "run", "check", "allowed", "args")} for s in steps]
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


# -- working copy (git) ------------------------------------------------------------------

def _git(t, *args, check=True):
    r = subprocess.run(["git", "-C", t["workdir"], *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def changed_files(t):
    out = _git(t, "status", "--porcelain", "--untracked-files=all")
    return sorted(line[3:].strip().strip('"') for line in out.splitlines() if line.strip())


def checkpoint(t, label):
    _git(t, "add", "-A")
    if changed_files(t):
        _git(t, "-c", "user.name=build-harness", "-c", "user.email=build-harness@localhost",
             "commit", "-q", "--no-verify", "-m", f"checkpoint: {label}")
    head = _git(t, "rev-parse", "HEAD").strip()
    t.setdefault("checkpoints", []).append({"label": label, "commit": head, "t": time.strftime("%H:%M:%S")})
    return head


def revert_to_checkpoint(t):
    _git(t, "reset", "-q", "--hard", "HEAD")
    _git(t, "clean", "-q", "-fd")


# -- lifecycle ------------------------------------------------------------------------------

def start(task_id, workflow, workdir, steps, budget_tokens=100_000, meta=None):
    if (_dir(task_id) / "task.json").is_file():
        raise Refused(f"task {task_id} already exists; continue it with next, or pick another id")
    t = {"id": task_id, "workflow": workflow, "workdir": str(workdir), "budget_tokens": budget_tokens,
         "created": time.strftime("%Y-%m-%d %H:%M:%S"), "approved": False, "steps": steps,
         "plan_hash": plan_hash(steps), "meta": meta or {}, "checkpoints": []}
    for s in t["steps"]:
        s.setdefault("status", "pending")
        s.setdefault("attempts", 0)
        s.setdefault("max_attempts", 3)
    _dir(task_id).mkdir(parents=True, exist_ok=True)
    save(t)
    journal(t, "start", workflow=workflow, steps=len(steps), plan_hash=t["plan_hash"])
    return t


def approve(task_id, who):
    """The owner approves the plan as it stands. Called from the command line only."""
    t = load(task_id)
    t["approved"], t["approved_by"], t["approved_hash"] = True, who, plan_hash(t["steps"])
    save(t)
    journal(t, "approve", who=who, plan_hash=t["approved_hash"])
    return t


def current(t, in_flight_steps=None):
    in_flight = in_flight_steps or set()
    in_flight_files = {f for x in t["steps"] if x["id"] in in_flight and x.get("allowed") for f in x["allowed"]}
    for s in t["steps"]:
        if s["status"] in ("pending", "failed"):
            if s["id"] in in_flight:
                continue
            if s["kind"] == "script" and in_flight:
                return None
            if s["kind"] == "owner" and in_flight:
                return None
            if s["kind"] == "model":
                if any(f in in_flight_files for f in s.get("allowed", [])):
                    continue
            return s
    return None


def advance(task_id, in_flight_steps=None):
    """Run script steps until a model step, a blocked step or the end."""
    t = load(task_id)
    if not t["approved"]:
        raise Refused("the plan is not approved; the owner runs: fieldkit build-harness approve " + task_id)
    while True:
        s = current(t, in_flight_steps)
        blocked = next((x for x in t["steps"] if x["status"] == "blocked"), None)
        if blocked:
            return {"state": "BLOCKED", "step": blocked["id"], "why": blocked.get("last_why"),
                    "next": "the owner decides: fix it by hand then `build-harness unblock`, or restore"}
        if s is None:
            if in_flight_steps:
                return {"state": "WAITING"}
            parked = [x["id"] for x in t["steps"] if x["status"] == "deferred"]
            if parked:
                journal(t, "done-with-deferred", steps=parked)
                return {"state": "DEFERRED", "step": parked[0], "why": [f"{len(parked)} step(s) are waiting for the owner "
                                                                    "(upstream removed what they change); the build gate stays closed"]}
            journal(t, "done")
            return {"state": "DONE", "checkpoints": len(t["checkpoints"])}
        if s["kind"] == "model":
            if s.get("auto") and not s.get("auto_tried"):
                # the harness's own attempt first (e.g. a transplant); the model only gets
                # what a script cannot do. The same check decides; a miss is put back.
                s["auto_tried"] = True
                res = _call(s["auto"], t, s, **(s.get("args") or {})) or {}
                why = list(res.get("why") or [])
                if res.get("defer"):
                    # not a job for a model (e.g. upstream removed the thing): park it for the owner, visibly.
                    # It is NOT done: the build gate refuses while any step is deferred.
                    s["status"], s["last_why"] = "deferred", why
                    journal(t, "deferred", step=s["id"], why=why)
                    save(t)
                    continue
                if res.get("ok"):
                    changed = changed_files(t)
                    outside = [c for c in changed if c not in set(s.get("allowed") or [])]
                    chk = _call(s["check"], t, s, **(s.get("args") or {})) if not outside else {"ok": False}
                    why = [] if chk.get("ok") and not outside else (outside and [f"changed {outside}"]) or chk.get("why", [])
                if res.get("ok") and not why:
                    s["status"], s["done_by"], s["notes"] = "done", "harness", res.get("notes") or []
                    checkpoint(t, s["id"])
                    journal(t, "auto-done", step=s["id"], notes=s["notes"])
                    save(t)
                    continue
                revert_to_checkpoint(t)
                journal(t, "auto-miss", step=s["id"], why=why)
            save(t)
            return {"state": "MODEL STEP", "step": s["id"]}
        if s["kind"] == "owner":                      # a decision no model should make
            s["status"], s["last_why"] = "blocked", [s["title"]]
            save(t)
            journal(t, "owner-step", step=s["id"], why=s["title"])
            continue
        journal(t, "script-start", step=s["id"])
        t0 = time.time()
        try:
            res = _call(s["run"], t, **(s.get("args") or {})) or {}
        except Exception as e:  # noqa: BLE001 - a crash is a failed step, reported
            res = {"ok": False, "why": [f"{type(e).__name__}: {e}"]}
        s["result"] = {k: v for k, v in res.items() if k != "add_steps"}
        if res.get("ok", True):
            s["status"] = "done"
            new = res.get("add_steps") or []
            if new:
                at = t["steps"].index(s) + 1
                for n in new:
                    n.setdefault("status", "pending"), n.setdefault("attempts", 0), n.setdefault("max_attempts", 3)
                t["steps"][at:at] = new
            checkpoint(t, s["id"]) if Path(t["workdir"], ".git").exists() else None
            journal(t, "script-done", step=s["id"], seconds=round(time.time() - t0, 1), added=len(new),
                    summary=res.get("summary"))
        else:
            s["attempts"] += 1
            s["last_why"] = res.get("why")
            s["status"] = "blocked" if s["attempts"] >= s["max_attempts"] or res.get("fatal") else "failed"
            journal(t, "script-failed", step=s["id"], why=res.get("why"), attempt=s["attempts"])
            save(t)
            if s["status"] == "failed":
                continue
        save(t)


def packet(task_id, by="cli", answer_mode=False, in_flight_steps=None):
    """The one thing the model sees: the current model step, trimmed to the context budget.

    answer_mode: the model answers in text (answer.py) and the harness applies it; the packet
    then says nothing about tools, so there are no conflicting instructions."""
    state = advance(task_id, in_flight_steps)
    if state["state"] != "MODEL STEP":
        return state
    t = load(task_id)
    s = next(x for x in t["steps"] if x["id"] == state["step"])
    budget_chars = int(t["budget_tokens"] * PACKET_SHARE * CHARS_PER_TOKEN)
    body = _call(s["packet"], t, s, budget_chars=budget_chars, answer_mode=answer_mode, **(s.get("args") or {}))
    done = sum(1 for x in t["steps"] if x["status"] == "done")
    head = [f"TASK {t['id']} - step {done + 1} of {len(t['steps'])}: {s['title']}",
            f"Attempt {s['attempts'] + 1} of {s['max_attempts']}."]
    if s.get("last_why"):
        head.append("Your last attempt was put back because: " + "; ".join(s["last_why"]))
    if answer_mode:
        head += [f"The file to change: {', '.join(s.get('allowed', []))}"]
    else:
        head += ["You may change ONLY these files (anything else is undone):",
                 *[f"  - {a}" for a in s.get("allowed", [])],
                 "When you have made the change, call build_harness_submit. The harness checks it; do not claim it works."]
    text = "\n".join(head) + "\n\n" + body
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n[... trimmed to fit the context budget]"
    journal(t, "packet", step=s["id"], chars=len(text), attempt=s["attempts"] + 1, by=by)
    return {"state": "MODEL STEP", "step": s["id"], "packet": text, "chars": len(text)}


def submit(task_id, note="", by="cli", step_id=None):
    t = load(task_id)
    if not t["approved"]:
        raise Refused("the plan is not approved yet")
    s = next((x for x in t["steps"] if x["id"] == step_id), None) if step_id else current(t)
    if not s or s["kind"] != "model":
        raise Refused("there is no model step waiting; call build_harness_next")
    changed = changed_files(t)
    allowed = set(s.get("allowed") or [])
    outside = [c for c in changed if c not in allowed]
    why = []
    if outside:
        why.append(f"changed files outside the step: {outside[:5]}")
    if not changed:
        why.append("nothing was changed")
    if not why:
        res = _call(s["check"], t, s, **(s.get("args") or {}))
        why = [] if res.get("ok") else list(res.get("why") or ["the check failed"])
    journal(t, "submit", step=s["id"], changed=changed, outside=outside, ok=not why, why=why, note=note[:500], by=by)
    if not why:
        s["status"], s["last_why"] = "done", None
        head = checkpoint(t, s["id"])
        save(t)
        return {"ok": True, "step": s["id"], "checkpoint": head[:12],
                "next": "call build_harness_next for the next step"}
    s["attempts"] += 1
    s["last_why"] = why
    revert_to_checkpoint(t)
    s["status"] = "blocked" if s["attempts"] >= s["max_attempts"] else "failed"
    save(t)
    journal(t, "revert", step=s["id"], attempt=s["attempts"], blocked=s["status"] == "blocked")
    return {"ok": False, "step": s["id"], "why": why, "attempts": f"{s['attempts']} of {s['max_attempts']}",
            "reverted": True,
            "next": ("BLOCKED: the owner takes over this step" if s["status"] == "blocked"
                     else "your changes were put back; call build_harness_next and try again")}


def owner_terminal():
    """True only at a real terminal: an agent running shell commands has none."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def unblock(task_id, step_id, how):
    """Owner only: 'retry' (fresh attempts) or 'skip' (mark done by the owner, with a checkpoint)."""
    t = load(task_id)
    s = next(x for x in t["steps"] if x["id"] == step_id)
    if how == "retry":
        s["status"], s["attempts"], s["last_why"] = "pending", 0, None     # a fresh start carries no old advice
        s["auto_tried"] = False                                           # ...and the harness gets its own go again
    elif how == "skip":
        # Overnight 2026-10-01 a supervising agent skipped ~70 steps, incl. the failing final-checks.
        if s["kind"] == "script" or s["id"].startswith("final"):
            raise Refused("a check step cannot be skipped: fix what it reports, then retry")
        if not owner_terminal():
            raise Refused("skip is for the owner at a real terminal; an agent's shell is not one")
        s["status"] = "done"
        s["skipped_by_owner"] = True
        checkpoint(t, f"{step_id} (done by the owner)")
    save(t)
    journal(t, "unblock", step=step_id, how=how)
    return t


def status(task_id):
    t = load(task_id)
    counts = {}
    for s in t["steps"]:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    cur = current(t)
    return {"task": t["id"], "workflow": t["workflow"], "approved": t["approved"], "steps": len(t["steps"]),
            "counts": counts, "current": cur and {"id": cur["id"], "kind": cur["kind"], "title": cur["title"],
                                                  "attempts": cur["attempts"]},
            "checkpoints": len(t["checkpoints"]), "workdir": t["workdir"]}


def fail_attempt(task_id, why, by="driver", step_id=None):
    """Count a failed attempt that never reached a check (e.g. an answer not in the required form)."""
    t = load(task_id)
    s = next((x for x in t["steps"] if x["id"] == step_id), None) if step_id else current(t)
    if not s or s["kind"] != "model":
        raise Refused("there is no model step waiting")
    revert_to_checkpoint(t)
    s["attempts"] += 1
    s["last_why"] = [why]
    s["status"] = "blocked" if s["attempts"] >= s["max_attempts"] else "failed"
    save(t)
    journal(t, "submit", step=s["id"], changed=[], outside=[], ok=False, why=[why], note="", by=by)
    return {"ok": False, "step": s["id"], "why": [why], "attempts": f"{s['attempts']} of {s['max_attempts']}"}


def rewind(task_id, step_id):
    """Owner only: undo a step that passed but is wrong. The working copy goes back to the
    checkpoint before it, and that step and every step after it are pending again.

    Live run 4 (2026-09-30): a port passed a check that was too weak; the fix to the check
    is only half the job, the bad result must also leave the working copy."""
    t = load(task_id)
    ids = [s["id"] for s in t["steps"]]
    if step_id not in ids:
        raise Refused(f"no step {step_id!r}")
    k = next((i for i, c in enumerate(t["checkpoints"]) if c["label"].startswith(step_id)), None)
    if k is None:
        raise Refused(f"{step_id} has no checkpoint: it never passed, so there is nothing to rewind")
    target = _git(t, "rev-parse", f"{t['checkpoints'][k]['commit']}~1").strip()
    _git(t, "reset", "-q", "--hard", target)
    _git(t, "clean", "-q", "-fd")
    t["checkpoints"] = t["checkpoints"][:k]
    for s in t["steps"][ids.index(step_id):]:
        if s["status"] != "pending" or s.get("attempts"):
            s.update(status="pending", attempts=0, last_why=None)
            s.pop("skipped_by_owner", None)
    save(t)
    journal(t, "rewind", step=step_id, to=target[:12])
    return {"step": step_id, "working copy at": target[:12], "checkpoints kept": len(t["checkpoints"])}
