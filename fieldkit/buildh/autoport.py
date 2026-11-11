"""I-04 for build-harness: the plug-in that turns the owner's clicks on a port into harness decisions.

The decision itself is the shared core's (fieldkit/lifecycle/autoaccept.py: every check passes AND an independent
reviewer on a higher ladder rung says yes). This module brings what is build-harness's own: the work items (an adapted
port waiting as a proposal, a model's request to skip a step, a model's request to retry a blocked check step), the
deterministic checks for each, the evidence the reviewer reads, and what happens after the answer.

sweep(task_id) runs at the top of task.advance(), when no model step is in flight and the tree is clean:
  1. deferred -> model (I-04 B): a step parked because upstream renamed or removed what it changes becomes a model
     step again, with the harness's own finding offered in the packet (`offer`);
  2. each proposal (adapted port) not yet reviewed: the diff applies, the meaning check holds, the changed files
     parse, only the step's files change -> reviewer -> task.accept as the harness, or a failed attempt that climbs;
  3. each skip request: a port step, a lesson given, and either the result already holds or nothing the hunk changes
     is left in the file -> reviewer -> done (skipped by the harness), or a failed attempt that climbs;
  4. each retry request on a blocked step: retries left and the tree changed since it failed -> reviewer -> pending.
Every decision is in the task journal (event auto-accept / auto-refuse) and the core's ledger, with the rewind line.
"""
import json
import re
import subprocess
import time
from pathlib import Path

from ..core import settings
from ..lifecycle import autoaccept as core
from . import task


def _author(s, req=None):
    return (req or {}).get("model") or (s.get("proposal") or {}).get("model") or s.get("model") or ""


def _rewind(t, s):
    return f"{settings.self_cmd()} build-harness rewind {t['id']} {s['id']}"


def _hunk_text(s):
    h = (s.get("args") or {}).get("hunk") or {}
    return "\n".join([str(h.get("header", ""))] + list(h.get("lines") or []))


def _syntax(t, files):
    from . import firefox
    try:
        probs = firefox.syntax_problems(t["workdir"], files)
    except Exception as e:                                   # noqa: BLE001 - a crash is a failed check
        probs = [f"{type(e).__name__}: {e}"]
    return not probs, "; ".join(probs)[:300]


def _climb(t, s, why, model):
    """A refusal is a failed attempt of the author's rung: the ladder decides retry, next rung, or park (blocked)."""
    if model:
        s["model"] = model
    s["attempts"] = int(s.get("attempts") or 0) + 1
    s["last_why"] = list(why)
    s["status"] = task.next_status(t, s, by="model")


def deferred_to_model(t, pol):
    if not pol.get("enabled") or not pol.get("deferred_to_model"):
        return []
    pats = [str(p).lower() for p in pol.get("deferred_patterns") or []]
    out = []
    for s in t["steps"]:
        if s["status"] != "deferred" or s["kind"] != "model":
            continue
        why = " ".join(str(x) for x in s.get("last_why") or [])
        if not any(p in why.lower() for p in pats):
            continue
        s.update(status="pending", attempts=0, auto_tried=True, offer=why[:1500], deferred_offered=True)
        task.journal(t, "deferred-to-model", step=s["id"], offer=why[:300])
        out.append(s["id"])
    return out


def _adapted(t, s, pol):
    tid, wd = t["id"], t["workdir"]
    diff = task._proposals_dir(t) / f"{s['id']}.diff"
    reason = (s.get("proposal") or {}).get("reason", "")
    checks = []
    ok = diff.is_file()
    checks.append(("the proposal's diff is on disk", ok, "" if ok else str(diff)))
    applied = False
    if ok:
        r = subprocess.run(["git", "-C", wd, "apply", str(diff)], capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["git", "-C", wd, "apply", "--3way", str(diff)], capture_output=True, text=True)
        applied = r.returncode == 0
        checks.append(("the diff applies to the tree now", applied, (r.stderr or "").strip()[:200]))
    try:
        if applied:
            changed = task.changed_files(t)
            outside = [c for c in changed if c not in set(s.get("allowed") or [])]
            checks.append(("only the step's files change", not outside, ", ".join(outside[:5])))
            probe = dict(s, hand_port=True, hand_note=reason)
            res = task._call(s["check"], t, probe, **(s.get("args") or {})) or {}
            checks.append(("meaning check (removed lines gone, added text present, nothing else removed)",
                           bool(res.get("ok")), "; ".join(res.get("why") or [])[:300]))
            checks.append(("the changed files parse (JS/Fluent/moz.build/JSON/Python)",) + _syntax(t, changed))
            patch_text = diff.read_text(encoding="utf-8", errors="replace")
        else:
            patch_text = ""
    finally:
        task.revert_to_checkpoint(t)
    evidence = (f"STEP: {s['id']}\nFILE: {(s.get('args') or {}).get('file', '')}\n\nTHE ORIGINAL HUNK (what the change "
                f"meant on the old source):\n{_hunk_text(s)}\n\nTHE AUTHOR'S REASON FOR ADAPTING IT:\n{reason}\n\n"
                f"WHAT THE LITERAL CHECK SAID:\n{'; '.join((s.get('proposal') or {}).get('literal_check_said') or [])}\n\n"
                f"THE PROPOSED DIFF ON THE NEW SOURCE:\n{patch_text}")
    author = _author(s)
    rec = core.decide("adapted", s["id"], author, checks, evidence=evidence, rewind=_rewind(t, s), project=tid, pol=pol,
                      question="Does the proposed diff carry the original hunk's meaning onto the new source (renamed "
                               "names followed correctly), and change nothing else?")
    return rec


def _skip_checks(t, s, req):
    from . import firefox
    a = s.get("args") or {}
    checks = [("a port step (not a check or script step)", s["kind"] == "model" and not s["id"].startswith("final")
               and bool(a.get("hunk")), s["kind"]),
              ("the request carries a lesson line", bool(task.LESSON.search(req.get("why") or "")), "")]
    if not a.get("hunk") or not a.get("file"):
        return checks
    target = Path(t["workdir"]) / a["file"]
    if not target.is_file():
        checks.append(("nothing left to port", True, f"{a['file']} is gone from the new source"))
        return checks
    probe = dict(s, hand_port=True, hand_note=req.get("why") or "")
    res = task._call(s["check"], t, probe, **a) or {}
    if res.get("ok"):
        checks.append(("the result already holds in the tree (meaning check)", True, ""))
        return checks
    removed, added, context = firefox.hunk_sides(a["hunk"])
    have = {l.strip() for l in target.read_text(encoding="utf-8", errors="replace").splitlines()}
    spec = lambda ls: [l.strip() for l in ls if len(l.strip()) >= firefox.SPECIFIC]
    left = [l for l in spec(removed) if l in have]
    if spec(removed):
        ok, ev = not left, ("still there: " + left[0][:120]) if left else "none of the removed lines is in the file"
    else:
        anchors = [l for l in spec(context) if l in have]
        ok = not anchors or all(l in have for l in spec(added))
        ev = "the lines it adds to are gone" if not anchors else ("its added lines are already there" if ok else
                                                                  "its anchor is still there and its lines are not")
    checks.append(("nothing the hunk changes is left in the file", ok, ev))
    return checks


def _skip(t, s, pol):
    req = s.get("skip_request") or {}
    checks = _skip_checks(t, s, req)
    evidence = (f"STEP: {s['id']}\nWHY THE STEP WAS STOPPED: {'; '.join(str(x) for x in s.get('last_why') or [])}\n\n"
                f"THE HUNK:\n{_hunk_text(s)}\n\nTHE MODEL'S REASON FOR SKIPPING (nothing to port):\n{req.get('why', '')}")
    return core.decide("skip", s["id"], _author(s, req), checks, evidence=evidence, rewind=_rewind(t, s), project=t["id"],
                       pol=pol, question="Is it right that this change has nothing left to port on the new source, so "
                                         "the step can be marked done without any edit?")


def _last_fail_time(t, sid):
    p = task._dir(t["id"]) / "journal.jsonl"
    last = None
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        if f'"step": "{sid}"' in line and ('"script-failed"' in line or '"owner-step"' in line or '"revert"' in line):
            try:
                last = json.loads(line)["t"]
            except (ValueError, KeyError):
                pass
    return time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S")) if last else None


def _retry(t, s, pol):
    req = s.get("retry_request") or {}
    n = int(s.get("auto_retries") or 0)
    cap = int(pol.get("max_retries") or 0)
    checks = [("the step is blocked", s["status"] == "blocked", s["status"]),
              (f"automatic retries left ({n} of {cap} used)", n < cap, ""),
              ("the request says what was fixed", bool((req.get("why") or "").strip()), "")]
    failed_at = _last_fail_time(t, s["id"])
    head_t = task._git(t, "log", "-1", "--format=%ct", check=False).strip()
    changed = bool(failed_at and head_t.isdigit() and int(head_t) > failed_at)
    checks.append(("the tree changed since the step failed", changed,
                   "" if changed else "no commit in the working copy after the failure"))
    log = ""
    if changed:
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(failed_at))
        log = task._git(t, "log", f"--since={since}", "--stat", "--format=%h %s", "-20", check=False)[:6000]
        log += "\n" + task._git(t, "log", f"--since={since}", "-p", "--format=%h %s", "-5", check=False)[:12000]
    evidence = (f"STEP: {s['id']} ({s.get('title', '')})\nWHAT IT REPORTED WHEN IT STOPPED:\n"
                + "\n".join(str(x) for x in s.get("last_why") or []) + f"\n\nWHAT THE MODEL SAYS IT FIXED:\n{req.get('why', '')}"
                f"\n\nTHE COMMITS SINCE THE FAILURE:\n{log}")
    return core.decide("retry", s["id"], _author(s, req), checks, evidence=evidence, rewind=_rewind(t, s), project=t["id"],
                       pol=pol, question="Do the commits plausibly fix what the step reported, without weakening or "
                                         "skipping any check, so that running the step again is the right next move?")


def sweep(task_id, pol=None, in_flight=None):
    """-> [{"step", "kind", "accepted"}]. No-op while a model step is in flight or the tree has changes."""
    pol = core.policy() if pol is None else pol
    if not pol.get("enabled") or in_flight:
        return []
    t = task.load(task_id)
    out = []
    if deferred_to_model(t, pol):
        task.save(t)
    if not Path(t["workdir"], ".git").exists() or task.changed_files(t):
        return out
    for sid in [x["id"] for x in t["steps"] if x["status"] == "proposed" and not x.get("auto_review")]:
        t = task.load(task_id)
        s = next(x for x in t["steps"] if x["id"] == sid)
        rec = _adapted(t, s, pol)
        t = task.load(task_id)
        s = next(x for x in t["steps"] if x["id"] == sid)
        s["auto_review"] = rec["id"]
        if rec.get("accepted"):
            task.save(t)
            r = task.accept(task_id, [sid], who=f"harness (author {rec['author']}, reviewer {rec['reviewer']})")
            t = task.load(task_id)
            if r["accepted"]:
                task.journal(t, "auto-accept", step=sid, kind="adapted", author=rec["author"], reviewer=rec["reviewer"],
                             ledger=rec["id"], why=rec["why"], rewind=rec["rewind"])
            else:
                task.journal(t, "auto-refuse", step=sid, kind="adapted", ledger=rec["id"],
                             why=[f"accept failed: {r['failed'][0]['why'] if r['failed'] else '?'}"])
        elif rec.get("wait_owner"):
            task.journal(t, "auto-wait-owner", step=sid, kind="adapted", why=rec["why"])
            task.save(t)
        else:
            s["review_refused"] = True
            _climb(t, s, rec["why"], rec["author"] if rec["author"] != "unknown" else None)
            task.journal(t, "auto-refuse", step=sid, kind="adapted", author=rec["author"], reviewer=rec["reviewer"],
                         ledger=rec["id"], why=rec["why"], status=s["status"])
            task.save(t)
        out.append({"step": sid, "kind": "adapted", "accepted": bool(rec.get("accepted"))})
    for kind, field, fn in (("skip", "skip_request", _skip), ("retry", "retry_request", _retry)):
        for sid in [x["id"] for x in task.load(task_id)["steps"] if x.get(field) and not x[field].get("decided")]:
            t = task.load(task_id)
            s = next(x for x in t["steps"] if x["id"] == sid)
            rec = fn(t, s, pol)
            s[field]["decided"] = rec["id"]
            if rec.get("accepted"):
                if kind == "skip":
                    s.update(status="done", skipped_by_harness=rec["id"], last_why=None)
                    task.checkpoint(t, f"{sid} (nothing to port: accepted by the harness, reviewer {rec['reviewer']})")
                else:
                    s.update(status="pending", attempts=0, last_why=None, auto_tried=False,
                             auto_retries=int(s.get("auto_retries") or 0) + 1)
                task.journal(t, "auto-accept", step=sid, kind=kind, author=rec["author"], reviewer=rec["reviewer"],
                             ledger=rec["id"], why=rec["why"], rewind=rec["rewind"])
            elif rec.get("wait_owner"):
                task.journal(t, "auto-wait-owner", step=sid, kind=kind, why=rec["why"])
            else:
                if kind == "skip" and s["kind"] == "model" and s["status"] not in ("done", "obsolete"):
                    _climb(t, s, rec["why"], rec["author"] if rec["author"] != "unknown" else None)
                else:
                    s["last_why"] = list(s.get("last_why") or []) + rec["why"]
                task.journal(t, "auto-refuse", step=sid, kind=kind, author=rec["author"], reviewer=rec["reviewer"],
                             ledger=rec["id"], why=rec["why"], status=s["status"])
            task.save(t)
            out.append({"step": sid, "kind": kind, "accepted": bool(rec.get("accepted"))})
    return out


def request(task_id, step_id, how, why, by="model"):
    """A model asks the HARNESS to skip a step (nothing to port) or retry a blocked one; the next `next` decides it with
    the checks and an independent reviewer. Nothing is changed here but the request."""
    from ..method import strikes
    if how not in ("skip", "retry"):
        raise task.Refused("request skip|retry")
    if not (why or "").strip():
        raise task.Refused("say why (--note): what makes the step need no change, or what was fixed")
    t = task.load(task_id)
    s = next((x for x in t["steps"] if x["id"] == step_id), None)
    if s is None:
        raise task.Refused(f"no step {step_id!r}")
    if s["status"] in ("done", "obsolete"):
        raise task.Refused(f"{step_id} is {s['status']}: nothing to request")
    if how == "retry" and s["status"] != "blocked":
        raise task.Refused(f"{step_id} is {s['status']}, not blocked: it is retried by itself")
    s[f"{how}_request"] = {"why": why[:2000], "by": by, "model": s.get("model") or strikes.attempt_model(),
                           "t": time.strftime("%Y-%m-%d %H:%M:%S")}
    task.save(t)
    task.journal(t, "request", step=step_id, how=how, by=by, why=why[:300])
    return {"ok": True, "step": step_id, "how": how,
            "next": f"the harness decides it at the next `build-harness next {task_id}` (checks + an independent reviewer)"}
