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
  4. each retry request on a blocked step: retries left and the tree changed since it failed -> reviewer -> pending;
  5. each port step where the check refused one attempt and the reviewer refused another (core.disagreement): a
     tie-break by the highest allowed rung, shown the upstream change of the hunk's own lines; CHECK holds -> the
     proposal is accepted; REVIEWER holds -> the refused attempt is applied with the check's reasons waived for this
     step only (journal `override`); unsure or no tie-breaker -> blocked with both reasons.
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


def _reauthor(t, s, rec):
    """P9: the author was the top usable rung, so no rung above it can review (the one above is out of budget). The next
    usable rung down authors the step again and the top one reviews. Once per step: a second time parks it, so two
    rungs never pass one step back and forth. -> True when the step was re-offered."""
    if int(s.get("reauthored") or 0) >= 1:
        s["status"], s["last_why"] = "blocked", list(rec["why"]) + ["already re-authored once: parked"]
        return False
    s.update(model=rec["reauthor"], attempts=0, status="failed", reauthored=int(s.get("reauthored") or 0) + 1,
             last_why=list(rec["why"]))
    s.pop("review_refused", None)
    task.journal(t, "re-author", step=s["id"], author=rec["author"], reauthor=rec["reauthor"],
                 reviewer=rec["reviewer"], why=rec["why"])
    return True


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


def upstream_change(t, s):
    """The upstream change of the lines the hunk touches: each removed or context line (the OLD upstream text the patch
    was made on) next to the closest line of the new upstream release (pristine, task meta upstream.commit)."""
    import difflib
    from . import firefox
    a = s.get("args") or {}
    commit = ((t.get("meta") or {}).get("upstream") or {}).get("commit") or ""
    if not commit or not a.get("hunk") or not a.get("file"):
        return ""
    new = task._git(t, "show", f"{commit}:{a['file']}", check=False)
    if not new:
        return f"(the new upstream release {commit[:12]} has no {a['file']})"
    removed, _added, context = firefox.hunk_sides(a["hunk"])
    have = new.splitlines()
    plain = {l.strip() for l in have}
    out = []
    for line in [l for l in list(context) + list(removed) if len(l.strip()) >= firefox.SPECIFIC]:
        if line.strip() in plain:
            continue
        near = difflib.get_close_matches(line.strip(), [h.strip() for h in have if h.strip()], n=1, cutoff=0.5)
        out += [f"- {line.strip()}", f"+ {near[0]}" if near else "+ (no such line any more)"]
    head = f"upstream {a['file']}: old (the patch's own lines) -> new release {commit[:12]} (pristine)"
    return head + "\n" + ("\n".join(out) if out else "(every line the hunk touches is unchanged upstream)")


def _apply_diff(t, diff):
    r = subprocess.run(["git", "-C", t["workdir"], "apply", str(diff)], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run(["git", "-C", t["workdir"], "apply", "--3way", str(diff)], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def _apply_refused(task_id, sid, override, rewind, who):
    """REVIEWER holds: put the attempt the check refused back, re-run the same check; done only when the check's
    remaining reasons are all in `override` (waived for this step), only the step's files change and they parse."""
    t = task.load(task_id)
    s = next(x for x in t["steps"] if x["id"] == sid)
    pdir = task._proposals_dir(t)
    meta = json.loads((pdir / f"{sid}.refused.json").read_text(encoding="utf-8"))
    ok, err = _apply_diff(t, pdir / f"{sid}.refused.diff")
    if not ok:
        task.revert_to_checkpoint(t)
        return False, f"the refused attempt no longer applies: {err}"
    changed = task.changed_files(t)
    outside = [c for c in changed if c not in set(s.get("allowed") or [])]
    probe = dict(s, hand_port=bool(meta.get("adapted")), hand_note=meta.get("adapted") or "")
    res = task._call(s["check"], t, probe, **(s.get("args") or {})) or {}
    left = [w for w in (res.get("why") or []) if str(w)[:400] not in set(override)] if not res.get("ok") else []
    syn_ok, syn = _syntax(t, changed)
    problems = ([f"files outside the step: {outside[:5]}"] if outside else []) + left + ([syn] if not syn_ok else [])
    if not changed:
        problems.append("the refused attempt changes nothing")
    if problems:
        task.revert_to_checkpoint(t)
        return False, "; ".join(problems)[:300]
    s.update(status="done", done_by="harness-override", last_why=None, override_by=who)
    task.checkpoint(t, f"{sid} (override by tie-break: {who})")
    task.save(t)
    task.journal(t, "override", step=sid, waived=list(override), who=who, rewind=rewind)
    return True, ""


def tiebreaks(task_id, pol, asker=None, pick=None):
    """Step 5 of the sweep: settle checker-vs-reviewer deadlocks on port steps."""
    out = []
    t = task.load(task_id)
    pdir = task._proposals_dir(t)
    for s in [x for x in t["steps"] if x["kind"] == "model" and x["status"] in ("failed", "blocked")]:
        sid = s["id"]
        if not ((pdir / f"{sid}.diff").is_file() and (pdir / f"{sid}.refused.json").is_file()):
            continue
        d = core.disagreement(t["id"], sid, pol)
        if not d:
            continue
        refused = json.loads((pdir / f"{sid}.refused.json").read_text(encoding="utf-8"))
        rv = d["reviewer"]
        reviewer_said = [str(rv.get("review") or "; ".join(rv.get("why") or []))]
        check_said = list(d["check"].get("why") or refused.get("why") or [])
        evidence = (f"STEP: {sid}\nFILE: {(s.get('args') or {}).get('file', '')}\n\nTHE UPSTREAM CHANGE OF THE LINES THE "
                    f"HUNK TOUCHES:\n{upstream_change(t, s)}\n\nTHE ORIGINAL HUNK (the project's change on the old "
                    f"source):\n{_hunk_text(s)}\n\nTHE WORK THE CHECK REFUSED (author's note: "
                    f"{(refused.get('adapted') or refused.get('note') or '')[:600]}):\n"
                    f"{(pdir / f'{sid}.refused.diff').read_text(encoding='utf-8', errors='replace')[:6000]}\n\n"
                    f"THE WORK THE REVIEWER REFUSED (passed every check; author's reason: "
                    f"{((s.get('proposal') or {}).get('reason') or '')[:600]}):\n"
                    f"{(pdir / f'{sid}.diff').read_text(encoding='utf-8', errors='replace')[:6000]}")
        rewind = _rewind(t, s)

        def apply(holds, override, sid=sid, rewind=rewind):
            who = f"tie-break ({holds} holds)"
            if holds == "check":
                tt = task.load(task_id)
                ss = next(x for x in tt["steps"] if x["id"] == sid)
                ss["review_refused"] = True                       # task.accept takes a reviewer-refused proposal
                task.save(tt)
                r = task.accept(task_id, [sid], who=f"harness {who}")
                return bool(r["accepted"]), "; ".join(f["why"] for f in r["failed"])[:300]
            return _apply_refused(task_id, sid, override, rewind, f"harness {who}")

        rec = core.tiebreak("adapted", sid, _author(s), check_said, reviewer_said, evidence, apply, rewind=rewind,
                            project=t["id"], reviewer=str(rv.get("reviewer") or ""), pol=pol, asker=asker, pick=pick,
                            question="The project's change (the hunk) must be carried onto the new upstream release. "
                                     "Does the check's reason hold (the work must carry the hunk's added text), or "
                                     "the reviewer's (that text was dropped upstream on purpose and must not come back)?")
        t = task.load(task_id)
        s = next(x for x in t["steps"] if x["id"] == sid)
        if rec.get("park"):
            s["status"], s["last_why"] = "blocked", list(rec["why"])
            task.revert_to_checkpoint(t)
        task.journal(t, "tie-break", step=sid, holds=rec.get("holds"), tiebreaker=rec.get("reviewer"),
                     ledger=rec["id"], check_said=check_said, reviewer_said=reviewer_said, why=rec["why"],
                     status=s["status"], rewind=rewind)
        task.save(t)
        out.append({"step": sid, "kind": "tiebreak", "accepted": bool(rec.get("accepted")), "holds": rec.get("holds")})
    return out


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
        elif rec.get("reauthor"):
            _reauthor(t, s, rec)
            task.save(t)
        elif rec.get("wait_owner"):
            task.journal(t, "auto-wait-owner", step=sid, kind="adapted", why=rec["why"],
                         retry_at=rec.get("retry_at"))
            if rec.get("retry_at"):                       # parked, not refused: asked again once a model is back
                s.pop("auto_review", None)
                s["retry_at"] = rec["retry_at"]
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
            if not rec.get("retry_at") or rec.get("accepted"):
                s[field]["decided"] = rec["id"]           # parked until a model is back: not decided, asked again
            if rec.get("reauthor"):
                _reauthor(t, s, rec)
                s[field]["decided"] = rec["id"]
            elif rec.get("accepted"):
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
    if (pol.get("tiebreak") or {}).get("enabled", True):
        out += tiebreaks(task_id, pol)
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
