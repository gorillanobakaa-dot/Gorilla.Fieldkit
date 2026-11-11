"""I-04: the harness accepts a model's work by itself, so a long job needs no owner click (owner, 2026-11-11).

Generic: the core knows nothing about code, patches or any one project. A work item is whatever a project's plug-in
says it is (a ported hunk, a build stage retried, an essay section, a video cut, a CV line); the plug-in brings the
deterministic checks that apply to it and the evidence text. The core decides, the same way for every project:

    decide(kind, item, author, checks, question, evidence, rewind, project)
        -> {"accepted": bool, "why": [...], "author", "reviewer", "id"}

  1. the policy (autoaccept.yaml) allows the harness to accept this kind (enabled, kinds);
  2. EVERY check passes (a check is (name, ok, evidence)); one failing -> not accepted, the reviewer is not asked;
  3. an independent reviewer on a HIGHER rung of the escalation ladder than the author (models.yaml; reviewer != author;
     a rung the spend guard refuses is skipped, never bypassed) answers `VERDICT: YES`. Anything else is a no;
  4. every decision, yes or no, is one row in the ledger (state/lifecycle/auto-accepted.jsonl) with both models, the
     checks, the reviewer's reason and the one-line rewind command; an acceptance waits there for the owner's
     after-the-fact review (`fieldkit review` lists it; `fieldkit autoaccept seen ID|--all` marks it read).

The caller acts on the answer (applies the work, or counts a failed attempt and climbs the ladder). A model never
calls an owner command itself: HARD LINE 4 is unchanged; the harness, not a model, accepts.

A checker and a reviewer that disagree on one item (the check refuses work W1, the reviewer refuses work W2 that passed
the checks, each for the reason the other side's work exists) would loop until the item parks. The core breaks it:

    refusal(kind, item, side, why, project)       a plug-in records a check's refusal of an attempt (reviewer refusals
                                                  are recorded by decide itself)
    disagreement(project, item)                   -> {"check": row, "reviewer": row} when both sides refused this item
                                                  since its last tie-break, else None
    tiebreak(kind, item, author, check_said, reviewer_said, evidence, apply, ...)
      1. records the disagreement (both reasons);
      2. asks the HIGHEST ladder rung the spend guard allows a reviewer to use that is above the author's rung (never
         the author's own model);
      3. the tie-breaker may only name which side's reason holds (`HOLDS: CHECK|REVIEWER|UNSURE`) and quote the evidence
         (`EVIDENCE: <a line copied from what it was shown>`); a quote not found in the evidence is unsure;
      4. CHECK holds: the work that passed the checks is accepted. REVIEWER holds: the check's reasons are overridden for
         THIS item only (`override`, both reasons, the rewind line, listed for the owner's review); the check itself is
         not changed and still refuses every other item. Unsure or no tie-breaker: the item parks with both reasons.

    fieldkit autoaccept status         the policy and the reviewer the ladder would pick now
    fieldkit autoaccept list [--all]   acceptances the owner has not seen yet (or every decision)
    fieldkit autoaccept seen ID|--all  the owner read them (after-the-fact review)
"""
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("autoaccept.yaml")
VERDICT = re.compile(r"^\W*VERDICT\W*:?\s*(YES|NO)\b", re.I | re.M)


def policy(path=None):
    p = Path(path or os.environ.get("FIELDKIT_AUTOACCEPT_POLICY") or DATA)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"enabled": False, "why": f"policy unreadable: {p}"}          # fail closed: nothing auto-accepted
    return dict(d.get("auto_accept") or {"enabled": False})


def allows(kind, pol=None):
    """-> (allowed, why) for one kind of acceptance."""
    pol = policy() if pol is None else pol
    if not pol.get("enabled"):
        return False, "auto_accept.enabled is false (the owner turned it off): the owner accepts"
    if kind not in (pol.get("kinds") or []):
        return False, f"auto_accept.kinds does not list {kind!r}: the owner accepts"
    return True, ""


def _rung(model, rungs):
    low = (model or "").lower()
    return next((i for i, r in enumerate(rungs) if r and r in low), None)


def pick_reviewer(author, pol=None, data=None, check=None):
    """-> {"reviewer": rung | None, "why": str}. The lowest ladder rung above the author's that the spend guard allows a
    reviewer to use. An author off the ladder or unknown: the policy's `unknown_author` (top = the top allowed rung,
    refuse = none). Never the author's own model."""
    from . import models
    pol = policy() if pol is None else pol
    d = data or models.load()
    rungs = [str(x).lower() for x in (models.ladder_cfg(d).get("ladder") or ())]
    check = check or (lambda r: models.check_model(r, role="reviewer", origin="autoaccept", data=d, log=False))
    if not rungs:
        return {"reviewer": None, "why": "the escalation ladder is empty (models.yaml escalation.ladder)"}
    at = _rung(author, rungs)
    skipped = []
    if at is None:
        if str((pol.get("reviewer") or {}).get("unknown_author") or "refuse") != "top":
            return {"reviewer": None, "why": f"the author model is unknown ({author or 'not recorded'}): no rung is "
                                             "provably higher (auto_accept.reviewer.unknown_author: refuse)"}
        for r in reversed(rungs):
            if check(r).get("ok") and r not in (author or "").lower():
                return {"reviewer": r, "why": f"author unknown ({author or 'not recorded'}): the top rung the spend "
                                              f"guard allows reviews ({r})" + (f"; skipped {skipped}" if skipped else "")}
            skipped.append(r)
        return {"reviewer": None, "why": f"no ladder rung is allowed to review (skipped {skipped})"}
    for r in rungs[at + 1:]:
        if check(r).get("ok"):
            return {"reviewer": r, "why": f"{r} is above the author's rung ({rungs[at]})"
                                          + (f"; skipped {skipped} (spend guard)" if skipped else "")}
        skipped.append(r)
    return {"reviewer": None, "why": f"no allowed rung above the author's ({rungs[at]})"
                                     + (f"; skipped {skipped} (spend guard)" if skipped else "") + ": the owner reviews"}


def _cmd(model, pol):
    env = os.environ.get("FIELDKIT_REVIEWER_CMD")                 # tests and other agents: a JSON list
    base = json.loads(env) if env else list((pol.get("reviewer") or {}).get("cmd") or [])
    return [str(x).replace("{model}", model) for x in base]


def ask_reviewer(model, prompt, pol=None, run=subprocess.run):
    """-> {"yes": bool, "why": str, "answer": str}. The reviewer's stdout must carry `VERDICT: YES|NO`; the LAST such
    line counts. A timeout, an error, no verdict: no (fail closed)."""
    pol = policy() if pol is None else pol
    cmd = _cmd(model, pol)
    if not cmd:
        return {"yes": False, "why": "no reviewer command (auto_accept.reviewer.cmd)", "answer": ""}
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.update(ANTHROPIC_MODEL=model, FIELDKIT_ATTEMPT_MODEL=model, FIELDKIT_REVIEWER="1")
    try:
        r = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                timeout=int((pol.get("reviewer") or {}).get("timeout") or 900))
    except (OSError, subprocess.SubprocessError) as e:
        return {"yes": False, "why": f"the reviewer could not be run: {type(e).__name__}: {str(e)[:160]}", "answer": ""}
    out = (r.stdout or "").strip()
    found = VERDICT.findall(out)
    if r.returncode != 0 or not found:
        return {"yes": False, "answer": out[-2000:],
                "why": f"no verdict from the reviewer (exit {r.returncode}): {(out or r.stderr or '')[-200:]}"}
    yes = found[-1].upper() == "YES"
    reason = next((l.strip() for l in reversed(out.splitlines()) if l.strip() and not VERDICT.match(l)), "")
    return {"yes": yes, "why": reason[:400] or ("the reviewer said yes" if yes else "the reviewer said no"),
            "answer": out[-2000:]}


REVIEW_PROMPT = """You are an independent reviewer. Another model (rung {author}) did a piece of work that a person would
otherwise have to approve. Decide whether the harness may accept it WITHOUT the person. Say yes only when the work does
what the item asks, keeps its meaning, changes nothing else, and weakens no check. When unsure, say no.

KIND: {kind}
PROJECT: {project}
ITEM: {item}

DETERMINISTIC CHECKS (all passed, or you would not be asked):
{checks}

EVIDENCE:
{evidence}

QUESTION: {question}

Answer with one or two sentences of reason, then ONE final line exactly `VERDICT: YES` or `VERDICT: NO`."""


def prompt_for(kind, item, author, checks, question, evidence, project, pol=None):
    pol = policy() if pol is None else pol
    cap = int((pol.get("reviewer") or {}).get("max_evidence_chars") or 24000)
    ev = str(evidence or "")
    if len(ev) > cap:
        ev = ev[:cap] + "\n[... the evidence was cut here to fit; judge only what you were shown, unsure = no]"
    lines = "\n".join(f"  - {n}: {'PASS' if ok else 'FAIL'}{(' - ' + str(e)[:200]) if e else ''}" for n, ok, e in checks)
    return REVIEW_PROMPT.format(author=author or "unknown", kind=kind, project=project or "-", item=item,
                                checks=lines or "  (none)", evidence=ev, question=question)


def ledger_path(pol=None):
    from .friction import state_dir
    pol = policy() if pol is None else pol
    return state_dir() / (pol.get("ledger") or "auto-accepted.jsonl")


def _append(rec, pol):
    with open(ledger_path(pol), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def decide(kind, item, author, checks, question, evidence="", rewind="", project="", pol=None, reviewer=None,
           asker=None, pick=None):
    """The one decision. checks: [(name, ok, evidence)], run by the caller's plug-in before this call. reviewer: force a
    rung (tests); asker(model, prompt) -> {"yes", "why"} (tests: no model is started). Every call writes one ledger row."""
    pol = policy() if pol is None else pol
    rec = {"id": uuid.uuid4().hex[:10], "at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "project": project,
           "item": item, "author": author or "unknown", "reviewer": None, "checks": [[n, bool(ok), str(e)[:300]]
                                                                                   for n, ok, e in checks],
           "rewind": rewind, "accepted": False, "why": [], "seen": False}
    ok, why = allows(kind, pol)
    if not ok:
        rec["why"] = [why]
        return dict(rec, wait_owner=True)               # policy off: not a failure of the work, nothing is logged
    failed = [f"{n}: {e}" if e else n for n, k, e in checks if not k]
    if not checks:
        failed = ["no deterministic check was run on this item (fail closed)"]
    if failed:
        rec["why"] = ["check failed: " + x for x in failed]
        _append(rec, pol)
        return rec
    pk = {"reviewer": reviewer, "why": "given"} if reviewer else (pick or pick_reviewer)(author, pol)
    if not pk["reviewer"]:
        rec["why"] = [f"no reviewer: {pk['why']}"]
        _append(rec, pol)
        return dict(rec, wait_owner=True)
    if (author or "").lower() and pk["reviewer"].lower() in (author or "").lower():
        rec["why"] = [f"the reviewer ({pk['reviewer']}) is the author's own model: not independent"]
        _append(rec, pol)
        return dict(rec, wait_owner=True)
    rec["reviewer"] = pk["reviewer"]
    text = prompt_for(kind, item, author, checks, question, evidence, project, pol)
    ans = (asker or (lambda m, p: ask_reviewer(m, p, pol)))(pk["reviewer"], text)
    rec["review"] = str(ans.get("why") or "")[:400]
    if ans.get("yes"):
        rec["accepted"], rec["why"] = True, [f"checks passed; {pk['reviewer']} (above {author or 'unknown'}) said yes: "
                                             f"{rec['review'][:200]}"]
    else:
        rec["why"] = [f"the reviewer ({pk['reviewer']}) said no: {rec['review'][:300]}"]
    _append(rec, pol)
    return rec


def rows(pol=None):
    p = ledger_path(pol)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    seen = {r["id"] for r in out if r.get("event") == "seen"} | {i for r in out if r.get("event") == "seen"
                                                                for i in r.get("ids") or []}
    return [dict(r, seen=r["id"] in seen) for r in out if not r.get("event")]


def unseen(pol=None):
    """Acceptances (overrides included) the owner has not reviewed yet (after the fact), oldest first."""
    return [r for r in rows(pol) if r.get("accepted") and not r.get("seen")]


# --- checker vs reviewer: the tie-break ---------------------------------------------------------------------------

HOLDS = re.compile(r"^\W*HOLDS\W*:?\s*(CHECK|REVIEWER|UNSURE)\b", re.I | re.M)
QUOTE = re.compile(r"^\W*EVIDENCE\W*:\s*(.+)$", re.I | re.M)


def refusal(kind, item, side, why, project="", author="", pol=None):
    """Record one side's refusal of an attempt on an item (side: check | reviewer). Written by the plug-in when its own
    check refuses an attempt outside decide (decide records its own refusals)."""
    pol = policy() if pol is None else pol
    _append({"id": uuid.uuid4().hex[:10], "event": "refusal", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
             "project": project, "item": item, "side": side, "author": author or "unknown",
             "why": [str(w)[:400] for w in (why or [])][:8]}, pol)


def _events(pol=None):
    p = ledger_path(pol)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def disagreement(project, item, pol=None):
    """-> {"check": row, "reviewer": row} when, since this item's last tie-break (or acceptance), a check refused one
    attempt AND a reviewer refused another. Otherwise None. Only rows of this project and this item count."""
    last = {}
    for r in _events(pol):
        if r.get("project") != project or r.get("item") != item:
            continue
        if r.get("tiebreak") or r.get("accepted"):
            last = {}
        elif r.get("event") == "refusal" and r.get("side") in ("check", "reviewer"):
            last[r["side"]] = r
        elif not r.get("event") and r.get("reviewer") and not r.get("accepted") and r.get("review"):
            last["reviewer"] = r                                  # decide: the checks passed, the reviewer said no
        elif not r.get("event") and any(str(w).startswith("check failed: ") for w in r.get("why") or []):
            last["check"] = r
    return last if {"check", "reviewer"} <= set(last) else None


def pick_tiebreaker(author, pol=None, data=None, check=None):
    """-> {"reviewer": rung | None, "why"}. The HIGHEST ladder rung the spend guard allows a reviewer to use, above the
    author's rung (an unknown author: any allowed rung but its own name). Never the author's model."""
    from . import models
    d = data or models.load()
    rungs = [str(x).lower() for x in (models.ladder_cfg(d).get("ladder") or ())]
    check = check or (lambda r: models.check_model(r, role="reviewer", origin="autoaccept", data=d, log=False))
    if not rungs:
        return {"reviewer": None, "why": "the escalation ladder is empty (models.yaml escalation.ladder)"}
    at = _rung(author, rungs)
    above = rungs[(at + 1 if at is not None else 0):]
    skipped = []
    for r in reversed(above):
        if r in (author or "").lower():
            continue
        if check(r).get("ok"):
            return {"reviewer": r, "why": f"{r}: the highest rung the spend guard allows"
                                          + (f" (skipped {skipped})" if skipped else "")}
        skipped.append(r)
    return {"reviewer": None, "why": f"no allowed rung above the author's ({author or 'unknown'})"
                                     + (f"; skipped {skipped} (spend guard)" if skipped else "")}


TIEBREAK_PROMPT = """You break a tie between a deterministic check and a reviewer model that disagree on ONE work item.
Both refused a version of the work, each for the reason the other side's version exists:

  CHECK says (it refused the work WITHOUT what the reviewer objects to):
{check_said}

  REVIEWER ({reviewer}) says (it refused the work that passed the check):
{reviewer_said}

You do not edit anything and you do not judge anything else. Decide only which side's reason holds for this item:
  HOLDS: CHECK     - the reviewer's objection is wrong; the work that passed the check is right.
  HOLDS: REVIEWER  - the check's refusal is wrong for this item; the work the check refused is right.
  HOLDS: UNSURE    - the evidence does not settle it, or both reasons hold.

KIND: {kind}
PROJECT: {project}
ITEM: {item}

EVIDENCE:
{evidence}

QUESTION: {question}

Answer with one or two sentences of reason, then a line `EVIDENCE: <one line copied exactly from the EVIDENCE above
that proves your answer>`, then ONE final line exactly `HOLDS: CHECK`, `HOLDS: REVIEWER` or `HOLDS: UNSURE`."""


def _norm(x):
    return " ".join(str(x).replace("\u2019", "'").replace("\u2018", "'").split()).strip(" `\"'").lower()


def parse_tiebreak(answer, evidence):
    """-> (holds: "check" | "reviewer" | None, quote, why). The LAST `HOLDS:` line counts; CHECK or REVIEWER stands
    only with an `EVIDENCE:` quote of 12+ characters found in the evidence the tie-breaker was shown."""
    found = HOLDS.findall(answer or "")
    if not found:
        return None, "", "no HOLDS line in the answer (unsure)"
    side = found[-1].lower()
    if side == "unsure":
        return None, "", "the tie-breaker is unsure"
    quotes = QUOTE.findall(answer or "")
    q = quotes[-1].strip() if quotes else ""
    if len(_norm(q)) < 12:
        return None, q, f"the tie-breaker named {side} without quoting the evidence (unsure)"
    if _norm(q) not in _norm(evidence):
        return None, q, f"the tie-breaker's quote is not in the evidence it was shown (unsure): {q[:160]}"
    return side, q, ""


def ask_raw(model, prompt, pol=None, run=subprocess.run):
    """-> {"ok", "answer", "why"}: the reviewer command's whole answer (the tie-break parses it itself)."""
    pol = policy() if pol is None else pol
    cmd = _cmd(model, pol)
    if not cmd:
        return {"ok": False, "answer": "", "why": "no reviewer command (auto_accept.reviewer.cmd)"}
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.update(ANTHROPIC_MODEL=model, FIELDKIT_ATTEMPT_MODEL=model, FIELDKIT_REVIEWER="1")
    try:
        r = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                timeout=int((pol.get("reviewer") or {}).get("timeout") or 900))
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "answer": "", "why": f"the tie-breaker could not be run: {type(e).__name__}"}
    out = (r.stdout or "").strip()
    return {"ok": r.returncode == 0, "answer": out[-4000:], "why": "" if r.returncode == 0 else f"exit {r.returncode}"}


def tiebreak(kind, item, author, check_said, reviewer_said, evidence, apply, question="", rewind="", project="",
             reviewer="", pol=None, asker=None, pick=None):
    """Break a checker-vs-reviewer deadlock on ONE item. apply(holds, override) -> (ok, why) is the plug-in's action:
    holds "check" = accept the work that passed the checks; "reviewer" = accept the work the check refused, with
    `override` (the check's reasons) waived for this item only. asker(model, prompt) -> {"ok", "answer"} (tests).
    -> the ledger row; row["park"] True when the item must wait (no tie-breaker, unsure, or apply failed)."""
    pol = policy() if pol is None else pol
    check_said = [str(w)[:400] for w in check_said or []]
    reviewer_said = [str(w)[:400] for w in reviewer_said or []]
    base = {"kind": kind, "project": project, "item": item, "author": author or "unknown", "rewind": rewind}
    _append(dict(base, id=uuid.uuid4().hex[:10], event="disagree", at=time.strftime("%Y-%m-%d %H:%M:%S"),
                 check_said=check_said, reviewer_said=reviewer_said, reviewer=reviewer), pol)
    rec = dict(base, id=uuid.uuid4().hex[:10], at=time.strftime("%Y-%m-%d %H:%M:%S"), tiebreak=True, reviewer=None,
               check_said=check_said, reviewer_said=reviewer_said, holds=None, override=[], accepted=False,
               park=True, checks=[], why=[], seen=False)
    both = f"check: {'; '.join(check_said)[:300]} | reviewer: {'; '.join(reviewer_said)[:300]}"
    ok, why = allows(kind, pol)
    if ok and not (pol.get("tiebreak") or {}).get("enabled", True):
        ok, why = False, "auto_accept.tiebreak.enabled is false: the item parks with both reasons"
    if not ok:
        rec["why"] = [why, both]
        _append(rec, pol)
        return rec
    pk = (pick or pick_tiebreaker)(author, pol)
    if not pk["reviewer"] or pk["reviewer"] in (author or "").lower():
        rec["why"] = [f"no tie-breaker: {pk['why']}", both]
        _append(rec, pol)
        return rec
    rec["reviewer"] = pk["reviewer"]
    cap = int((pol.get("reviewer") or {}).get("max_evidence_chars") or 24000)
    ev = str(evidence or "")[:cap]
    text = TIEBREAK_PROMPT.format(kind=kind, project=project or "-", item=item, evidence=ev,
                                  check_said="\n".join(f"    - {w}" for w in check_said) or "    (nothing recorded)",
                                  reviewer_said="\n".join(f"    - {w}" for w in reviewer_said) or "    (nothing recorded)",
                                  reviewer=reviewer or "reviewer", question=question or "Which side's reason holds?")
    ans = (asker or (lambda m, p: ask_raw(m, p, pol)))(pk["reviewer"], text)
    answer = str(ans.get("answer") or "")
    holds, quote, why = parse_tiebreak(answer if ans.get("ok", True) else "", ev)
    rec["review"] = next((l.strip() for l in answer.splitlines() if l.strip() and not HOLDS.match(l)
                          and not QUOTE.match(l)), "")[:400]
    rec["quote"] = quote[:400]
    if not holds:
        rec["why"] = [f"the tie-breaker ({pk['reviewer']}) did not settle it: {why or ans.get('why') or '?'}", both]
        _append(rec, pol)
        return rec
    rec["holds"] = holds
    if holds == "reviewer":
        rec["override"] = list(check_said)            # waived for THIS project + item only; the check is unchanged
    done, how = apply(holds, list(rec["override"]))
    if not done:
        rec["why"] = [f"{pk['reviewer']} ruled the {holds} holds, but the harness could not apply it: {how}", both]
        _append(rec, pol)
        return rec
    rec["accepted"], rec["park"] = True, False
    rec["why"] = [(f"override: the check's refusal is waived for this item only; {pk['reviewer']} ruled the reviewer's "
                   f"reason holds" if holds == "reviewer" else
                   f"{pk['reviewer']} ruled the check's reason holds; the reviewer's objection is overruled")
                  + f" (evidence: {quote[:160]})", both]
    _append(rec, pol)
    return rec


def overridden(project, item, why, pol=None):
    """-> (left, waived): the reasons in `why` NOT waived for this item, and those an accepted override waives. An
    override names one project + item; the same reason on any other item is never waived."""
    waived = set()
    for r in _events(pol):
        if r.get("tiebreak") and r.get("accepted") and r.get("project") == project and r.get("item") == item:
            waived |= set(r.get("override") or [])
    left = [w for w in why or [] if str(w)[:400] not in waived]
    return left, [w for w in why or [] if str(w)[:400] in waived]


def mark_seen(ids, pol=None):
    _append({"id": uuid.uuid4().hex[:10], "event": "seen", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "ids": list(ids)},
            policy() if pol is None else pol)


def lines(rs):
    out = [f"{len(rs)} decision(s) the harness made by itself:"]
    for r in rs:
        tag = ("OVERRIDE" if r.get("override") and r.get("accepted") else "TIE-BREAK" if r.get("tiebreak") and
               r.get("accepted") else "ACCEPTED" if r.get("accepted") else "parked" if r.get("tiebreak") else "refused")
        out.append(f"  {r['id']}  {r['at']}  {tag}  {r['kind']}  "
                   f"{r.get('project') or '-'}  {r['item'][:70]}")
        out.append(f"      author {r.get('author')}, reviewer {r.get('reviewer') or '-'}: {'; '.join(r.get('why') or [])[:200]}")
        if r.get("tiebreak"):
            out.append(f"      check said: {'; '.join(r.get('check_said') or [])[:200]}")
            out.append(f"      reviewer said: {'; '.join(r.get('reviewer_said') or [])[:200]}")
        if r.get("rewind"):
            out.append(f"      undo: {r['rewind']}")
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit autoaccept", description=__doc__.split("\n")[0])
    ap.add_argument("action", choices=["status", "list", "seen"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--author", default="", help="status: the reviewer the ladder would pick for this author")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true", help="seen: the owner away from a terminal proves it (passphrase)")
    a = ap.parse_args(argv)
    pol = policy()
    if a.action == "status":
        pk = pick_reviewer(a.author, pol)
        r = {"enabled": bool(pol.get("enabled")), "kinds": pol.get("kinds") or [], "reviewer_for": a.author or "unknown",
             "reviewer": pk["reviewer"], "why": pk["why"], "unseen": len(unseen(pol)), "policy": str(DATA)}
        print(json.dumps(r, indent=1) if a.json else "\n".join(
            [f"auto-accept: {'ON' if r['enabled'] else 'OFF'} for {', '.join(r['kinds']) or 'nothing'} (policy {DATA})",
             f"reviewer for an author {r['reviewer_for']}: {r['reviewer'] or 'none'} ({r['why']})",
             f"{r['unseen']} acceptance(s) wait for the owner's after-the-fact review",
             "NEXT: fieldkit autoaccept list"]))
        return 0
    if a.action == "list":
        rs = rows(pol) if a.all else unseen(pol)
        print(json.dumps(rs, indent=1) if a.json else "\n".join(lines(rs) + ["NEXT: fieldkit autoaccept seen --all "
                                                                            "(after reading; undo one with its line)"]))
        return 0
    ids = [r["id"] for r in unseen(pol)] if a.all else a.ids
    if not ids:
        print("nothing to mark: give IDs or --all\nNEXT: fieldkit autoaccept list")
        return 2
    from . import ownerproof
    ok, how = ownerproof.owner_or_proof(f"autoaccept seen {len(ids)}", a.force)
    if not ok:                      # the after-the-fact review is the owner's: a model cannot clear it
        print(f"refused: marking acceptances seen is the owner's review: {how}\nNEXT: fieldkit review")
        return 1
    mark_seen(ids, pol)
    print(f"marked {len(ids)} as seen by the owner\nNEXT: fieldkit review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
