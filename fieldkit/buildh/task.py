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
import re
import os
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
    """Write the task. Steps and checkpoints that another process added since this one loaded the task are kept:
    02 Oct 13:32, a `record` in one process wrote three hand steps and the build process, loaded at 13:20, saved
    its stale copy over them 32 s later. Last writer no longer wins: the on-disk steps this copy lacks are merged
    in (by id, in their on-disk order, before the first `final` step), and the merge is noted in the task."""
    d = _dir(t["id"])
    d.mkdir(parents=True, exist_ok=True)
    f = d / "task.json"
    if f.is_file():
        try:
            disk = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            disk = None
        if disk:
            have = {s["id"] for s in t.get("steps", [])}
            missing = [s for s in disk.get("steps", []) if s["id"] not in have]
            if missing:
                at = next((i for i, s in enumerate(t["steps"]) if s["id"].startswith("final")), len(t["steps"]))
                for s in missing:
                    t["steps"].insert(at, s)
                    at += 1
                t.setdefault("merged_steps", []).append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "ids": [s["id"] for s in missing]})
            # checkpoints are NOT merged back: a rewind removes them on purpose (the git commits still exist)
    f.write_text(json.dumps(t, indent=1), encoding="utf-8")


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
        # The identity is forced through the environment, not `-c`: a git hook (or anyone) can export
        # GIT_AUTHOR_NAME, which beats `-c user.name`, and the audit tells harness checkpoints from hand
        # commits by this author (found 2026-10-01: the pre-commit hook's run signed a checkpoint as the owner).
        ident = {"GIT_AUTHOR_NAME": "build-harness", "GIT_AUTHOR_EMAIL": "build-harness@localhost",
                 "GIT_COMMITTER_NAME": "build-harness", "GIT_COMMITTER_EMAIL": "build-harness@localhost"}
        subprocess.run(["git", "-C", t["workdir"], "commit", "-q", "--no-verify", "-m", f"checkpoint: {label}"],
                       check=True, capture_output=True, env={**os.environ, **ident}, timeout=3600)
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
    hr = (t.get("meta") or {}).get("harness_root")
    if t.get("workflow") == "firefox-upgrade" and hr and (Path(hr) / "intents" / "INTENTS.yaml").is_file() \
            and not (_dir(task_id) / "migrate" / "plan.json").is_file():
        # 2026-10-14: the Linux 158 port ran a day without the migration plan and intent ledger Fable built on
        # Windows, because nothing made a session look. A port with a ledger next to it goes under migration control.
        raise Refused(f"this Firefox port is not under migration control. Read {hr}/MIGRATION-PLAN.md, then run: "
                      f"{settings.self_cmd()} build-harness migrate init {task_id}")
    while True:
        s = current(t, in_flight_steps)
        # A blocked or deferred step is a person's; the rest of the plan goes on (live run 13: one parked CSS hunk
        # halted 30 steps the harness would have done itself). The run ends with the list, never with 'done'.
        if s is None:
            if in_flight_steps:
                return {"state": "WAITING"}
            blocked = [x for x in t["steps"] if x["status"] == "blocked"]
            if blocked:
                return {"state": "BLOCKED", "step": blocked[0]["id"], "why": blocked[0].get("last_why"),
                        "steps": [x["id"] for x in blocked],
                        "next": f"{len(blocked)} step(s) wait for a person: fix by hand then `build-harness submit`, "
                                "or `build-harness unblock ... retry`; the build gate stays closed"}
            proposed = [x["id"] for x in t["steps"] if x["status"] == "proposed"]
            if proposed:
                journal(t, "done-with-proposals", steps=proposed)
                return {"state": "PROPOSED", "steps": proposed,
                        "why": [f"{len(proposed)} adapted port(s) wait for the owner's acceptance, in one go"],
                        "next": f"the owner runs: {settings.self_cmd()} build-harness accept {t['id']} --all"}
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
                    if res.get("obsolete"):
                        # upstream removed the very thing the change touches and there is nothing to attach it
                        # to: resolved as OBSOLETE by default (owner's instruction 2026-10-01: a sensible default,
                        # recorded for review, instead of a question). Counted for the gate, listed by it.
                        s["status"], s["last_why"] = "obsolete", why
                        journal(t, "obsolete", step=s["id"], why=why)
                        save(t)
                        continue
                    # not a job for a model (e.g. the thing moved): park it for the owner, visibly.
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
            if s.get("obsolete_default"):              # ...unless there is nothing left to decide about
                s["status"], s["last_why"] = "obsolete", [s["title"]]
                save(t)
                journal(t, "obsolete", step=s["id"], why=[s["title"]])
                continue
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
    pos = next(i for i, x in enumerate(t["steps"]) if x["id"] == s["id"]) + 1   # ONE numbering: the place in the plan
    head = [f"TASK {t['id']} - step {pos} of {len(t['steps'])} ({done} done): {s['title']}",
            "IN WORDS: " + describe_step(t, s, with_result=False),
            f"Attempt {s['attempts'] + 1} of {s['max_attempts']}."]
    if s.get("last_why"):
        head.append("Your last attempt was put back because: " + "; ".join(s["last_why"]))
    if answer_mode:
        head += [f"The file to change: {', '.join(s.get('allowed', []))}"]
    else:
        head += ["You may change ONLY these files (anything else is undone):",
                 *[f"  - {a}" for a in s.get("allowed", [])],
                 "When you have made the change, call build_harness_submit. The harness checks it; do not claim it works.",
                 f"Test it first, free: {settings.self_cmd()} build-harness submit {t['id']} --dry-run (no attempt spent; "
                 "add the same --adapted note you would submit with).", PORT_AND_TEACH]
    from . import decisions as dec
    cited = dec.citations(t, list(s.get("allowed") or []))
    if cited:
        head += [""] + cited
    text = "\n".join(head) + "\n\n" + body
    if len(text) > budget_chars:
        text = text[:budget_chars] + "\n[... trimmed to fit the context budget]"
    journal(t, "packet", step=s["id"], chars=len(text), attempt=s["attempts"] + 1, by=by)
    return {"state": "MODEL STEP", "step": s["id"], "packet": text, "chars": len(text)}


def trial(task_id, note="", adapted="", hand=False):
    """The check of `submit`, with nothing spent and nothing changed: no attempt counted, no revert, no proposal saved.

    2026-10-14 (Linux 158 port): a submit chained after a failing hand-run check spent attempt 1 of 3 on a port the
    harness then refused. A trial costs nothing, so the packet tells every porter to run it first."""
    t = load(task_id)
    s = current(t)
    if not s or s["kind"] != "model":
        raise Refused("there is no model step waiting; call build_harness_next")
    changed = changed_files(t)
    outside = [c for c in changed if c not in set(s.get("allowed") or [])]
    if outside:
        return {"ok": False, "trial": True, "why": [f"changed files outside the step: {outside[:5]}"]}
    if not changed and not hand:
        # the same rule as submit (2026-10-14: a trial said ok, the submit then spent an attempt on 'nothing was changed')
        return {"ok": False, "trial": True, "why": ["nothing was changed"], "would_be": "an attempt refused"}
    probe = dict(s)
    if adapted or hand:
        probe["hand_port"], probe["hand_note"] = True, adapted or note
    r = _call(probe["check"], t, probe, **(s.get("args") or {})) or {}
    return {"ok": bool(r.get("ok")), "trial": True, "why": list(r.get("why") or []),
            "would_be": ("a proposal for the owner" if adapted else "done") if r.get("ok") else "an attempt refused"}


def drop(task_id, patches, reason, who="owner", keep=(), patchset_root=None):
    """Drop whole patches from a running port, the way their author said to, with the reason recorded.

    2026-10-14, Linux 158: patch 078 (21.PORT.FIXES.157) says "For a later Firefox: drop the three 16.SNAPSHOT
    tokens-*.css patches and this one; keep step 2". The port had already ported most of them, and the policy's
    `exclude` list only shapes the NEXT plan. So: every step of the dropped patches ends as obsolete (with the
    reason, who, and the author's words), a waiting proposal of theirs is withdrawn (moved, not deleted), the files
    they touch go back to the pristine release, and the `keep` patches that share those files are applied again
    (patch(1), no fuzz). Refused when a step of another patch already changed one of those files: dropping would
    erase that work. -> {"dropped": [...], "files": [...], "reapplied": [...]}"""
    import shutil
    from . import firefox
    t = load(task_id)
    root = Path(patchset_root) if patchset_root else firefox._policy(t["meta"]["harness_root"])[0]
    files = set()
    for p in list(patches) + list(keep):
        pf = root / p
        if not pf.is_file():
            raise Refused(f"no patch {p} under {root}")
        if p in patches:
            files |= {f["file"] for f in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace"))}
    drop_steps = [x for x in t["steps"] if (x.get("args") or {}).get("patch") in set(patches)]
    others = [x["id"] for x in t["steps"] if x["kind"] == "model" and x["status"] == "done" and x not in drop_steps
              and (x.get("args") or {}).get("file") in files and (x.get("args") or {}).get("patch") not in set(keep)]
    if others:
        raise Refused(f"other ported steps changed these files; dropping would erase them: {others[:5]}")
    if changed_files(t):
        raise Refused("the work tree has uncommitted changes; submit or revert the current step first")
    pristine = _git(t, "rev-list", "--max-parents=0", "HEAD").split()[0]
    for f in sorted(files):
        _git(t, "checkout", pristine, "--", f)
    reapplied = []
    for p in keep:
        r = subprocess.run(["patch", "-p1", "--fuzz=0", "--no-backup-if-mismatch", "-i", str(root / p)],
                           cwd=t["workdir"], capture_output=True, text=True)
        if r.returncode != 0:
            revert_to_checkpoint(t)
            raise Refused(f"the kept patch {p} does not apply on the pristine files: {(r.stdout + r.stderr)[-300:]}")
        reapplied.append(p)
    withdrawn = _proposals_dir(t) / "withdrawn"
    for x in drop_steps:
        if x["status"] == "proposed":
            withdrawn.mkdir(exist_ok=True)
            for ext in (".diff", ".json"):
                src = _proposals_dir(t) / f"{x['id']}{ext}"
                if src.is_file():
                    shutil.move(str(src), str(withdrawn / src.name))
        x["status"], x["last_why"] = "obsolete", [f"dropped by {who}: {reason}"]
        x["dropped_by"] = who
    checkpoint(t, f"drop {len(patches)} patch(es): {reason[:80]}")
    journal(t, "dropped", patches=list(patches), keep=list(keep), files=sorted(files), steps=[x["id"] for x in drop_steps],
            by=who, reason=reason)
    save(t)
    return {"dropped": [x["id"] for x in drop_steps], "files": sorted(files), "reapplied": reapplied}


def _proposals_dir(t):
    d = _dir(t["id"]) / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    return d


LESSON = re.compile(r"^\s*lesson:\s*(\S.*)$", re.I | re.M)
PORT_AND_TEACH = (
    "PORT AND TEACH: porting and teaching the harness happen together, at every step. When the harness could not do "
    "this step alone, your submit note (or --adapted reason) must carry one line `lesson: <what the harness should "
    "learn so the next port does this alone>`. An adapted or hand port without it is refused. The lessons are "
    "collected (`build-harness lessons TASK`) and each becomes a harness rule with a test.")


RESULT_WORDS = {"done": "ported", "obsolete": "nothing to port", "proposed": "waiting for the owner's acceptance",
                "deferred": "waiting for the owner (upstream removed what it changes)", "blocked": "waiting for a person",
                "pending": "not done yet", "failed": "last try refused, tries left"}


def describe_step(t, s, with_result=True):
    """A step in words a person can hold: where it is, what it does, the result.

    Owner, 2026-10-14 (Miller 1956: about seven items; Cowan 2001: about four): "Step 120: nothing to port" means nothing
    to anyone who does not hold the whole plan in mind, and this harness is for people with ADHD and dyslexia. So every
    step is said in full: its place in the plan and in the migration stage, its group, what it does in plain words (the
    patch author's own note when there is one), the file, then the result."""
    steps = t["steps"]
    pos = next((i for i, x in enumerate(steps) if x["id"] == s["id"]), 0) + 1
    a = s.get("args") or {}
    patch = a.get("patch") or ""
    group = a.get("group") or (patch.split("/")[0] if "/" in patch else "")
    stage = ""
    pj = _dir(t["id"]) / "migrate" / "plan.json"
    if pj.is_file():
        try:
            st = json.loads(pj.read_text(encoding="utf-8")).get("stage")
            from ..migrate import plan as mplan
            stage = f"stage {st} ({mplan.STAGE[st]['name']})" if st in mplan.STAGE else f"stage {st}"
        except (ValueError, KeyError, ImportError):
            stage = ""
    what = ""
    if patch:
        from . import firefox
        note = firefox.author_note(t, patch, limit=400)
        for lead in ("Gorilla 157 port fix ", "Gorilla 157 privacy cut ", "Gorilla 157 "):
            if note.startswith(lead):
                note = note[len(lead):]
        what = note.split(" Files:")[0].strip()
    if not what:
        what = s.get("title", "")
    parts = [f"Step {pos} of {len(steps)}"] + ([stage] if stage else []) + ([f"group {group}"] if group else [])
    if len(what) > 260:
        what = what[:260].rsplit(" ", 1)[0] + " ..."          # cut at a word, never inside one
    line = ", ".join(parts) + f": {what}"
    if a.get("file"):
        line += f" (file {a['file']})"
    if with_result:
        word = RESULT_WORDS.get(s.get("status"), s.get("status"))
        if s.get("skipped_by_owner"):
            word = "nothing to port, marked so by the owner"
        elif s.get("done_by") == "harness":
            word = "ported by the harness itself"
        elif s.get("accepted_by"):
            word = f"ported, an adapted port accepted by {s['accepted_by']}"
        line += f". Result: {word}"
        if s.get("status") == "obsolete" and s.get("last_why"):
            line += f" ({str(s['last_why'][0])[:160]})"
    return line


def lessons(task_id):
    """-> [{t, step, lesson, by}] recorded with submits, oldest first."""
    f = _dir(task_id) / "lessons.jsonl"
    if not f.is_file():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def _record_lessons(t, s, text, by):
    found = [m.group(1).strip() for m in LESSON.finditer(text or "")]
    if found:
        with open(_dir(t["id"]) / "lessons.jsonl", "a", encoding="utf-8", newline="\n") as fh:
            for x in found:
                fh.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M"), "step": s["id"], "lesson": x, "by": by}) + "\n")
        journal(t, "lesson", step=s["id"], lessons=found)
    return found


def submit(task_id, note="", by="cli", step_id=None, adapted="", require_lesson=True):
    """`adapted`: the model declares that the hunk cannot be put in by the letter (a symbol renamed upstream,
    the code moved) and that its edit carries the hunk's MEANING. The owner, 2026-10-14: "I wanna approve it
    all in one go; I can't be sitting the whole day in front of this laptop approving things". So: the edit is
    judged by meaning the way a person's declared port is; when it holds, it is SAVED as a proposal (the diff
    and the reason, under proposals/), the tree is put back, no attempt is spent, and the run goes on. The
    owner accepts the whole list later with `accept --all`; each accepted proposal is applied and recorded as a
    hand port. When the meaning does not hold, it is an ordinary failed attempt."""
    t = load(task_id)
    if not t["approved"]:
        raise Refused("the plan is not approved yet")
    s = next((x for x in t["steps"] if x["id"] == step_id), None) if step_id else current(t)
    if not s or s["kind"] != "model":
        raise Refused("there is no model step waiting; call build_harness_next")
    if require_lesson and (adapted or by == "hand") and not LESSON.search(adapted or note or ""):
        # owner, 2026-10-14: "porting and teaching the harness happen together, at every step" - an adapted or hand
        # port is exactly the place the harness failed; the porter says what it should learn, or the port is refused
        raise Refused("PORT AND TEACH: an adapted or hand port must carry one line `lesson: <what the harness should "
                      "learn so the next port does this alone>` in its note")
    _record_lessons(t, s, "\n".join(x for x in (adapted, note) if x), by)
    changed = changed_files(t)
    allowed = set(s.get("allowed") or [])
    outside = [c for c in changed if c not in allowed]
    why = []
    if outside:
        why.append(f"changed files outside the step: {outside[:5]}")
    if not changed and by != "hand":
        why.append("nothing was changed")
    if not changed and by == "hand":
        s["hand_port"] = True
        chk = _call(s["check"], t, s, **(s.get("args") or {}))
        if chk.get("ok"):
            s["status"], s["done_by"], s["notes"] = "done", "hand", ["already in the tree: nothing to change, the check passes"]
            save(t)
            journal(t, "submit", step=s["id"], ok=True, by=by, note="already in the tree")
            return {"ok": True, "step": s["id"], "note": "already in the tree; recorded as done by hand"}
        why.append("nothing was changed, and the tree does not hold the result: " + "; ".join(chk.get("why") or [])[:200])
    if not why and adapted and by != "hand":
        literal = _call(s["check"], t, s, **(s.get("args") or {}))
        s["hand_port"], s["hand_note"] = True, adapted           # judged by meaning, as a declared port is; the reason
        res = _call(s["check"], t, s, **(s.get("args") or {}))   # may carry `keeps:` / `removes:` declarations
        s["hand_port"] = False
        s.pop("hand_note", None)
        if res.get("ok"):
            _git(t, "add", "-A")
            diff = subprocess.run(["git", "-C", t["workdir"], "diff", "--cached", "--binary", "HEAD"],
                                  capture_output=True, timeout=600).stdout
            _git(t, "reset", "-q")
            pdir = _proposals_dir(t)
            (pdir / f"{s['id']}.diff").write_bytes(diff)
            meta = {"step": s["id"], "reason": adapted[:1000], "files": changed, "by": by,
                    "literal_check_said": list(literal.get("why") or [])[:8],
                    "meaning_check": "holds", "t": time.strftime("%Y-%m-%d %H:%M:%S")}
            (pdir / f"{s['id']}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
            s["status"], s["last_why"], s["proposal"] = "proposed", None, meta
            revert_to_checkpoint(t)
            save(t)
            journal(t, "proposed", step=s["id"], reason=adapted[:500], files=changed,
                    literal=meta["literal_check_said"])
            return {"ok": True, "proposed": True, "step": s["id"], "proposal": str(pdir / f"{s['id']}.diff"),
                    "next": "saved for the owner's acceptance; the tree was put back; call build_harness_next"}
        why = ["adapted port declared, but the meaning does not hold: " + "; ".join(res.get("why") or [])[:300]]
    if not why:
        s["hand_port"] = by == "hand"                       # a person's declared port is judged by meaning, anything else by the letter
        if by == "hand" and note:
            s["hand_note"] = note                           # `keeps: <line>` declarations live here, journaled with the submit
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


def proposals(task_id):
    """Every adapted port waiting for the owner, in plan order, with the reason and what the literal check said."""
    t = load(task_id)
    return [dict(s["proposal"], title=s.get("title", "")) for s in t["steps"] if s["status"] == "proposed"]


def accept(task_id, step_ids=None, who="owner"):
    """The owner accepts adapted ports in ONE go (`--all`) or by step id: each proposal's diff is applied to the
    tree and submitted as a hand port (judged by meaning, checkpointed, recorded as the owner's). A diff that no
    longer applies (a later checkpoint moved the lines) is reported and the step goes back to failed for a fresh
    attempt; nothing is guessed. Called from the command line only, like approve."""
    t = load(task_id)
    wanted = [s for s in t["steps"] if s["status"] == "proposed" and (not step_ids or s["id"] in step_ids)]
    out = {"accepted": [], "failed": [], "by": who}
    for s in wanted:
        diff = _proposals_dir(t) / f"{s['id']}.diff"
        if changed_files(t):
            out["failed"].append({"step": s["id"], "why": "the working copy has uncommitted changes; put it back first"})
            continue
        r = subprocess.run(["git", "-C", t["workdir"], "apply", "--index", str(diff)], capture_output=True, text=True)
        if r.returncode != 0:
            s["status"], s["last_why"] = "failed", [f"the accepted proposal no longer applies: {(r.stderr or '').strip()[:200]}"]
            save(t); journal(t, "accept-failed", step=s["id"], who=who, why=s["last_why"])
            revert_to_checkpoint(t)
            out["failed"].append({"step": s["id"], "why": s["last_why"][0]})
            continue
        _git(t, "reset", "-q")                                # the files are in the tree; submit judges the tree
        s["status"] = "pending"
        res = submit(task_id, note=f"accepted adapted port: {s['proposal'].get('reason', '')[:300]}", by="hand", step_id=s["id"],
                     require_lesson=False)               # the lesson came with the proposal; the owner only accepts
        t = load(task_id)
        s = next(x for x in t["steps"] if x["id"] == s["id"])
        if res.get("ok"):
            s["accepted_by"], s["done_by"] = who, "hand"         # the owner's acceptance IS the hand decision
            save(t); journal(t, "accept", step=s["id"], who=who)
            out["accepted"].append(s["id"])
        else:
            out["failed"].append({"step": s["id"], "why": "; ".join(res.get("why") or [])[:200]})
    return out


def owner_terminal():
    """True only when a PERSON can type into this process: an agent running shell commands has no terminal on stdin.

    Only stdin is judged (2026-10-14): the terminal opener (`fieldkit terminal ask`) shows a command in a window, the
    owner presses Enter, and the window copies the output into its log, so stdout is a pipe. Requiring a terminal on
    stdout as well refused the owner's own skip in the window built for exactly that."""
    return sys.stdin.isatty()


def unblock(task_id, step_id, how):
    """Owner only: 'retry' (fresh attempts) or 'skip' (mark done by the owner, with a checkpoint)."""
    t = load(task_id)
    s = next(x for x in t["steps"] if x["id"] == step_id)
    if how == "retry":
        s["status"], s["attempts"], s["last_why"] = "pending", 0, None     # a fresh start carries no old advice
        s["auto_tried"] = False                                           # ...and the harness gets its own go again
    elif how == "hand":
        # a person ports it: the step waits for `submit --hand` without the harness tiers running again (they
        # would defer or park it the same way) and without a model being asked
        if s["kind"] != "model":
            raise Refused("only a port step can be taken by hand")
        if s["status"] in ("done", "obsolete"):
            raise Refused(f"{step_id} is {s['status']}: nothing to take by hand")
        s["status"], s["attempts"], s["last_why"] = "pending", 0, None
        s["auto_tried"], s["hand_port"] = True, True
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
