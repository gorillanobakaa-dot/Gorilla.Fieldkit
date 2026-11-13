"""Counsel: the ONE place every "ask a stronger model" path lives (plan P9, owner 2026-11-11: "Toate aceste piese de
tip advisor trebuie combinate intr-un singur model unificat ... Unifica-le, nu le lasa imprastiate").

Four paths, tried in this order, cheapest first; a job parks only past the top, as before:

    1. advise    in-session advice: every headless Claude Code worker starts with Claude Code's own advisor tool
                 (`--advisor RUNG`), so a worker at a hard decision asks a stronger model in the same session
                 instead of failing and restarting the whole job a rung up.   advisor_for / advisor_args / launch_args
    2. review    an independent reviewer on a higher rung reads the evidence and answers VERDICT: YES|NO (no tools,
                 no advisor: it must stay independent).                         pick_reviewer / ask (verdict=True)
    3. tiebreak  the highest allowed rung settles a check-vs-reviewer deadlock on one item.
                                                                                pick_tiebreaker / ask (verdict=False)
    4. climb     a job that failed its attempts on one rung retries on the next rung up.          next_model / climb

One rung choice for all four: `usable(rung)` = available now (models.availability: out of budget -> skipped) AND the
spend guard lets a reviewer-tier role use it (models.check_model: Opus is the review tier, Fable is premium and needs
an owner-signed mandate's models_allowed). `pick(author, how)` walks the escalation ladder (models.yaml) with it:
how="next" (advise, review) = the lowest usable rung above the author, how="top" (tiebreak) = the highest.

One journal for all four: state/lifecycle/counsel.jsonl, one row per call {at, path, author, model, outcome, why,
job, calls?}. `fieldkit overhead counsel` and `fieldkit review` read it. The old entry points (autoaccept.pick_reviewer,
ask_reviewer, ask_raw, pick_tiebreaker, models.climb, models.next_model) are thin calls into this module; a lint test
(tests/test_counsel.py) fails when a new `claude -p --model` call or rung-picking function appears outside it.
"""
import json
import os
import re
import subprocess
import time

PATHS = ("advise", "review", "tiebreak", "climb")       # the order: cheapest (no restart) first
VERDICT = re.compile(r"^\W*VERDICT\W*:?\s*(YES|NO)\b", re.I | re.M)


# ---- data ------------------------------------------------------------------------------------------------------------
def _models():
    from . import models
    return models


def cfg(data=None):
    """-> models.yaml `counsel:` (one section for every path)."""
    return dict((data or _models().load()).get("counsel") or {})


def ladder(data=None):
    return [str(x).lower() for x in (_models().ladder_cfg(data).get("ladder") or ())]


def rung_index(model, rungs):
    """-> the ladder index whose name is in `model` (claude-sonnet-5-5 -> sonnet), else None."""
    low = (model or "").lower()
    return next((i for i, r in enumerate(rungs) if r and r in low), None)


# ---- the one rung choice ---------------------------------------------------------------------------------------------
def default_check(data):
    return lambda r: _models().check_model(r, role="reviewer", origin="counsel", data=data, log=False)


def usable(rung, data=None, check=None):
    """-> (ok, why): available now AND the spend guard (+ mandate models_allowed) lets a reviewer-tier role use it."""
    m = _models()
    d = data or m.load()
    up, why = m.availability(rung, data=d)
    if not up:
        return False, why
    return (True, "") if (check or default_check(d))(rung).get("ok") else (False, f"{rung} (spend guard)")


def held_back(skipped, data=None):
    """-> the earliest epoch one of the skipped, merely-unavailable rungs is back (None: none comes back by time)."""
    return _models().retry_at([x.split(" ")[0] for x in skipped if "unavailable" in x], data=data)


def pick(author, how="next", data=None, check=None, skip_own=True):
    """-> {"model": rung | None, "why", "skipped": [..], "at": author's index | None}. how "next": the lowest usable
    rung above the author's; "top": the highest usable rung above it (an unknown author: any rung but its own name).
    Never the author's rung, never below it. The one rung-picking walk; every path calls it."""
    d = data or _models().load()
    rungs = ladder(d)
    if not rungs:
        return {"model": None, "why": "the escalation ladder is empty (models.yaml escalation.ladder)", "skipped": [],
                "at": None}
    at = rung_index(author, rungs)
    above = rungs[(at + 1 if at is not None else 0):]
    skipped = []
    for r in (reversed(above) if how == "top" else above):
        if skip_own and r in (author or "").lower():
            continue
        ok, why = usable(r, d, check)
        if ok:
            return {"model": r, "why": "", "skipped": skipped, "at": at}
        skipped.append(why or r)
    return {"model": None, "why": "", "skipped": skipped, "at": at}


# ---- 1. advise: Claude Code's own advisor on every headless worker ---------------------------------------------------
def advisor_for(worker, data=None, check=None, env=None):
    """-> {"advisor": rung | None, "why"}. The next usable rung above the worker (pick how="next"); counsel.advisor
    off, CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1, a worker off the ladder, a `never` rung (Haiku can call an advisor but is
    never one) or nothing usable above: None, never an error."""
    env = os.environ if env is None else env
    d = data or _models().load()
    a = dict(cfg(d).get("advisor") or {})
    if not a.get("enabled"):
        return {"advisor": None, "why": "counsel.advisor.enabled is false"}
    if str(env.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL") or "") not in ("", "0"):
        return {"advisor": None, "why": "CLAUDE_CODE_DISABLE_ADVISOR_TOOL is set"}
    if env.get("FIELDKIT_REVIEWER") == "1":
        return {"advisor": None, "why": "an independent reviewer gets no advisor"}
    rungs = ladder(d)
    if rung_index(worker, rungs) is None:
        return {"advisor": None, "why": f"the worker {worker or '(none)'} is not on the escalation ladder"}
    never = [str(x).lower() for x in a.get("never") or ()]
    p = pick(worker, "next", d, check)
    m = p["model"]
    if m and any(n in m for n in never):
        return {"advisor": None, "why": f"{m} is never an advisor (counsel.advisor.never)"}
    if not m:
        return {"advisor": None, "why": f"no usable rung above {worker}"
                                        + (f" (skipped {p['skipped']})" if p["skipped"] else "")}
    return {"advisor": m, "why": f"{m}: the next usable rung above {worker}"
                                 + (f" (skipped {p['skipped']})" if p["skipped"] else "")}


def advisor_args(worker, data=None, check=None, env=None):
    """-> ["--advisor", rung] or [] for a claude launch."""
    a = advisor_for(worker, data, check, env)
    return ["--advisor", a["advisor"]] if a["advisor"] else []


def launch_args(argv, env=None, data=None, check=None):
    """-> (argv + ["--advisor", rung] | argv, advisor | None, why). The worker model is read from argv's --model (the
    spend guard has already rewritten it). A launch that already names --advisor is left as it is. The advisor's start
    goes to the spend ledger like any expensive start."""
    argv = list(argv)
    if any(x == "--advisor" or str(x).startswith("--advisor=") for x in argv):
        return argv, None, "the launch names its own advisor"
    worker = next((argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "--model"), None) or \
        next((str(x).split("=", 1)[1] for x in argv if str(x).startswith("--model=")), None)
    try:
        a = advisor_for(worker, data, check, env)
    except (OSError, ValueError) as e:                       # an advisor problem never stops a job
        return argv, None, f"no advisor: {type(e).__name__}: {str(e)[:120]}"
    if not a["advisor"]:
        return argv, None, a["why"]
    try:
        _models().check_model(a["advisor"], role="reviewer", origin="advisor", data=data)   # ledger row only
    except OSError:
        pass
    at = next((i for i, x in enumerate(argv) if x == "--model" or str(x).startswith("--model=")), len(argv))
    return argv[:at] + ["--advisor", a["advisor"]] + argv[at:], a["advisor"], a["why"]   # before --model: argv ends as before


def drop_advisor(argv):
    """-> argv without its --advisor flag (the one retry after the CLI refused it)."""
    out, skip = [], False
    for x in argv:
        if skip:
            skip = False
        elif x == "--advisor":
            skip = True
        elif not str(x).startswith("--advisor="):
            out.append(x)
    return out


def run_advised(argv, env, run, worker=None):
    """Any headless claude launch outside the autopilot launcher: add the advisor (launch_args), run it with run(argv)
    -> (rc, out, err), retry once without the advisor when the CLI refused it, journal the outcome. -> run's result."""
    argv, advisor, why = launch_args(argv, env)
    worker = worker or next((argv[i + 1] for i, x in enumerate(argv[:-1]) if x == "--model"), None)
    rc, out, err = res = run(argv)
    if advisor and advisor_rejected(rc, out, err):
        journal("advise", model=advisor, author=worker, outcome="rejected",
                why=f"the CLI refused --advisor {advisor}: {((err or '') + (out or ''))[-160:]}")
        why, advisor = f"--advisor {advisor} refused by the CLI; ran without it", None
        rc, out, err = res = run(drop_advisor(argv))
    journal("advise", model=advisor or "", author=worker, outcome="on" if advisor else "off", why=why,
            calls=advisor_calls(out) if advisor else 0)
    return res


def advisor_rejected(rc, out, err):
    """-> True when a run ended at once because the CLI refused the advisor (an old claude without --advisor, a model
    it will not take as an advisor): non-zero exit, no result line, and the word "advisor" in what it said."""
    if rc == 0:
        return False
    text = f"{err or ''}\n{(out or '')[:4000]}"
    return "advisor" in text.lower() and '"type":"result"' not in (out or "").replace(" ", "")


def advisor_calls(stream):
    """-> how many times the worker consulted its advisor, from a stream-json log (each call is an assistant
    `server_tool_use` block named `advisor`; verified live 2026-11-11, claude -p --model sonnet --advisor opus)."""
    n = 0
    for line in (stream or "").splitlines():
        if '"advisor"' not in line or "server_tool_use" not in line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        for c in ((msg.get("message") or {}).get("content") or []) if isinstance(msg, dict) else []:
            if isinstance(c, dict) and c.get("type") == "server_tool_use" and c.get("name") == "advisor":
                n += 1
    return n


# ---- 2. review and 3. tiebreak --------------------------------------------------------------------------------------
def _policy(pol):
    if pol is not None:
        return pol
    from . import autoaccept
    return autoaccept.policy()


def pick_reviewer(author, pol=None, data=None, check=None):
    """-> {"reviewer": rung | None, "why", "same_rung"?, "reauthor"?, "retry_at"?}: the lowest usable rung above the
    author (pick how="next"). None above and the author's rung is usable: the author's rung reviews in a fresh session
    (auto_accept.reviewer.when_top_unavailable: same_rung) or one rung below re-authors (reauthor). Nothing usable:
    retry_at, the caller parks until then. An unknown author: auto_accept.reviewer.unknown_author (top | refuse)."""
    pol = _policy(pol)
    d = data or _models().load()
    rungs = ladder(d)
    check = check or default_check(d)
    rv = pol.get("reviewer") or {}
    if not rungs:
        return {"reviewer": None, "why": "the escalation ladder is empty (models.yaml escalation.ladder)"}
    at = rung_index(author, rungs)
    if at is None:
        if str(rv.get("unknown_author") or "refuse") != "top":
            return {"reviewer": None, "why": f"the author model is unknown ({author or 'not recorded'}): no rung is "
                                             "provably higher (auto_accept.reviewer.unknown_author: refuse)"}
        p = pick(author, "top", d, check)
        if p["model"]:
            return {"reviewer": p["model"], "why": f"author unknown ({author or 'not recorded'}): the top rung that "
                                                   f"is available and allowed reviews ({p['model']})"
                                                   + (f"; skipped {p['skipped']}" if p["skipped"] else "")}
        return {"reviewer": None, "retry_at": held_back(p["skipped"], d),
                "why": f"no ladder rung is usable to review (skipped {p['skipped']})"}
    p = pick(author, "next", d, check, skip_own=False)
    skipped = p["skipped"]
    if p["model"]:
        return {"reviewer": p["model"], "why": f"{p['model']} is above the author's rung ({rungs[at]})"
                                               + (f"; skipped {skipped}" if skipped else "")}
    note = f"; skipped {skipped}" if skipped else ""
    own_ok = usable(rungs[at], d, check)[0]
    if own_ok and str(rv.get("when_top_unavailable") or "same_rung") != "reauthor":
        return {"reviewer": rungs[at], "same_rung": True,
                "why": f"same-rung review: every higher rung unavailable (owner rule 2026-11-11){note}"}
    for r in (reversed(rungs[:at]) if own_ok else ()):           # the author's rung must itself be usable
        if usable(r, d, check)[0]:
            return {"reviewer": rungs[at], "reauthor": r,
                    "why": f"no usable rung above the author's ({rungs[at]}){note}: {r} authors, {rungs[at]} reviews"}
    return {"reviewer": None, "retry_at": held_back(skipped, d),
            "why": f"no usable rung above the author's ({rungs[at]}){note}: the owner reviews"}


def pick_tiebreaker(author, pol=None, data=None, check=None):
    """-> {"reviewer": rung | None, "why", "retry_at"?}: the HIGHEST usable rung above the author (pick how="top")."""
    d = data or _models().load()
    p = pick(author, "top", d, check or default_check(d))
    if not ladder(d):
        return {"reviewer": None, "why": p["why"]}
    if p["model"]:
        return {"reviewer": p["model"], "why": f"{p['model']}: the highest rung that is available and allowed"
                                               + (f" (skipped {p['skipped']})" if p["skipped"] else "")}
    return {"reviewer": None, "retry_at": held_back(p["skipped"], d),
            "why": f"no usable rung above the author's ({author or 'unknown'})"
                   + (f"; skipped {p['skipped']} (spend guard)" if p["skipped"] else "")}


def ask_cmd(model, pol):
    """The reviewer / tie-breaker command (auto_accept.reviewer.cmd, {model} filled). FIELDKIT_REVIEWER_CMD: a JSON
    list (tests, other agents). The ONLY `claude -p --model` for a stronger model outside a worker launch."""
    env = os.environ.get("FIELDKIT_REVIEWER_CMD")
    base = json.loads(env) if env else list((pol.get("reviewer") or {}).get("cmd") or [])
    return [str(x).replace("{model}", model) for x in base]


def mark_if_gone(model, out, r, found):
    """A call that ends in a budget / limit / quota / rate error, "max turns", or exits non-zero with nothing said
    marks the model unavailable (models.mark_unavailable). -> "until ..." text when marked, else ""."""
    m = _models()
    text = f"{out}\n{getattr(r, 'stderr', '') or ''}"
    hit = m.classify_failure(text)
    if not hit and not found and (r.returncode != 0 and not out.strip()):
        mins = float(m._avail_cfg().get("no_verdict_cooldown_minutes") or 30)
        hit = {"reason": f"no verdict, exit {r.returncode} ({(getattr(r, 'stderr', '') or '')[-100:].strip()})",
               "until": time.time() + 60 * mins}
    if not hit:
        return ""
    m.mark_unavailable(model, hit["reason"], hit["until"])
    return m.until_text(hit["until"])


def ask(model, prompt, purpose="review", pol=None, run=subprocess.run):
    """One question to a stronger model through the reviewer command (stdin in, stdout out; no tools, no advisor).
    purpose "review": -> {"yes", "why", "answer", "unavailable"?}; the LAST `VERDICT: YES|NO` line counts; no verdict,
    an error, a timeout: no (fail closed). purpose "tiebreak": -> {"ok", "answer", "why", "unavailable"?}, the whole
    answer (the caller parses it). Every call is one counsel journal row."""
    pol = _policy(pol)
    verdict = purpose == "review"
    who = "reviewer" if verdict else "tie-breaker"
    cmd = ask_cmd(model, pol)
    if not cmd:
        why = "no reviewer command (auto_accept.reviewer.cmd)"
        return {"yes": False, "why": why, "answer": ""} if verdict else {"ok": False, "answer": "", "why": why}
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    # independent: no advisor even when a settings file carries advisorModel (path 2/3 never consults path 1)
    env.update(ANTHROPIC_MODEL=model, FIELDKIT_ATTEMPT_MODEL=model, FIELDKIT_REVIEWER="1",
               CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1")
    t0 = time.time()
    try:
        r = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                timeout=int((pol.get("reviewer") or {}).get("timeout") or 900))
    except (OSError, subprocess.SubprocessError) as e:
        journal(purpose, model=model, outcome="error", why=type(e).__name__, seconds=round(time.time() - t0))
        if verdict:
            return {"yes": False, "answer": "",
                    "why": f"the reviewer could not be run: {type(e).__name__}: {str(e)[:160]}"}
        return {"ok": False, "answer": "", "why": f"the {who} could not be run: {type(e).__name__}"}
    out = (r.stdout or "").strip()
    secs = round(time.time() - t0)
    if not verdict:
        gone = mark_if_gone(model, out, r, [1]) if r.returncode != 0 or not out else ""
        res = {"ok": r.returncode == 0, "answer": out[-4000:], "unavailable": bool(gone),
               "why": "" if r.returncode == 0 else f"exit {r.returncode}"
                      + (f"; {model} marked unavailable {gone}" if gone else "")}
        journal(purpose, model=model, outcome="answered" if res["ok"] else ("unavailable" if gone else "error"),
                why=res["why"], seconds=secs)
        return res
    found = VERDICT.findall(out)
    if r.returncode != 0 or not found:
        gone = mark_if_gone(model, out, r, found)
        res = {"yes": False, "answer": out[-2000:], "unavailable": bool(gone),
               "why": f"no verdict from the reviewer (exit {r.returncode}): {(out or r.stderr or '')[-200:]}"
                      + (f"; {model} marked unavailable {gone}" if gone else "")}
        journal(purpose, model=model, outcome="unavailable" if gone else "no-verdict", why=res["why"][:200],
                seconds=secs)
        return res
    yes = found[-1].upper() == "YES"
    reason = next((l.strip() for l in reversed(out.splitlines()) if l.strip() and not VERDICT.match(l)), "")
    journal(purpose, model=model, outcome="yes" if yes else "no", why=reason[:200], seconds=secs)
    return {"yes": yes, "why": reason[:400] or ("the reviewer said yes" if yes else "the reviewer said no"),
            "answer": out[-2000:]}


# ---- 4. climb ----------------------------------------------------------------------------------------------------------
def next_model(model, own_failures, data=None):
    """-> the model for the next attempt. Below `own_limit` own failures on this rung the same model retries; at
    `own_limit` the next rung up. None past the top rung (park it). A model not on the ladder: None (no guess)."""
    m = _models()
    c = m.ladder_cfg(data)
    rungs = ladder(data)
    idx = rung_index(model, rungs)
    if idx is None:
        return None
    if own_failures < int(c.get("own_limit") or 2):
        return model
    return rungs[idx + 1] if idx + 1 < len(rungs) else None


def climb(model, own_failures, data=None):
    """-> {"next": model | None, "same": bool, "skipped": [rungs]}. next == model (same True): retry on this rung. A
    rung that is unavailable or the spend guard refuses is skipped, never bypassed; none left: next None (park). Ladder
    off or a model not on it: next None."""
    m = _models()
    out = {"next": None, "same": False, "skipped": []}
    if not m.ladder_cfg(data).get("enabled"):
        return out
    nxt = next_model(model, own_failures, data)
    if nxt is None:
        return out
    if nxt == model:
        return dict(out, next=model, same=True)
    rungs = ladder(data)
    for r in rungs[rungs.index(nxt.lower()):]:
        up, why = m.availability(r, data=data)
        if not up:
            out["skipped"].append(f"{r} ({why})")
            continue
        v = m.check_model(r, role="reviewer", origin="ladder", data=data, log=False)
        if v["ok"]:
            return dict(out, next=r)
        out["skipped"].append(r)
    return out



# ---- 5. downward: the cheapest capable model for a kind of work (plan P35) ---------------------------------------------
CHEAP_KINDS = ("gather", "search", "summarize", "classify", "compact", "report")
_TAIL = re.compile(r"(\n|^)\s*(#+\s*)?(SIDE JOBS|DONE PROTOCOL|Harness \(job)", re.I)


def strip_tail(text):
    """-> the brief without the protocol the harness appends (it names tests, commits and branches in every brief)."""
    m = _TAIL.search(text or "")
    return (text[:m.start()] if m else (text or ""))


def _has(words, low):
    return sorted(w for w in words if (w in low if not w[0].isalnum() else re.search(r"\b" + re.escape(w), low)))


def work_kind(text, data=None):
    """-> {"kind": name | "", "why"}. A brief that names a code word is `code` (sonnet at least); a design word,
    `design`; else the kind with the most word hits among the cheap kinds, the first in file order on a tie. No hit:
    "" (the router decides)."""
    wk = (data or _models().load()).get("work_kinds") or {}
    low = strip_tail(text).lower()
    hit = _has(wk.get("code_words") or (), low)
    if hit:
        return {"kind": "code", "why": "names " + ", ".join(hit[:4])}
    hit = _has(wk.get("design_words") or (), low)
    if hit:
        return {"kind": "design", "why": "names " + ", ".join(hit[:4])}
    best, bh = "", []
    for name, spec in (wk.get("kinds") or {}).items():
        h = _has(spec.get("words") or (), low) if name in CHEAP_KINDS else []
        if len(h) > len(bh):
            best, bh = name, h
    return {"kind": best, "why": ("names " + ", ".join(bh[:4])) if best else "no work-kind word"}


def model_for_kind(kind, data=None, check=None):
    """-> {"model": rung | None, "advisor": bool, "why"}: the kind's rung, else the first usable fallback."""
    d = data or _models().load()
    spec = ((d.get("work_kinds") or {}).get("kinds") or {}).get(kind)
    if not spec:
        return {"model": None, "advisor": False, "why": f"no work kind {kind}"}
    tried = []
    for r in [spec.get("rung")] + list(spec.get("fallback") or ()):
        if not r:
            continue
        up, why = _models().availability(r, data=d)
        ok = up and (check or (lambda x: _models().check_model(x, role="worker", origin="counsel", data=d,
                                                               log=False)))(r).get("ok") if up else False
        if ok:
            return {"model": r, "advisor": bool(spec.get("advisor")),
                    "why": f"{kind} -> {r}" + (f" ({'; '.join(tried)})" if tried else "")}
        tried.append(f"{r} unavailable")
    return {"model": None, "advisor": False, "why": f"{kind}: " + "; ".join(tried)}


def apply_kind(text, routed, data=None, check=None):
    """-> {"model", "kind", "why", "changed"}: `routed` (the router's model) moved to the kind's rung. A cheap kind
    lowers it to its rung (never raises, never below the router's floor: the floor words are code words); a code or
    design brief is raised to the kind's rung when the router chose less. A kind with no usable rung leaves `routed`."""
    d = data or _models().load()
    k = work_kind(text, d)
    out = {"model": routed, "kind": k["kind"], "why": k["why"], "changed": False}
    if not k["kind"]:
        return out
    m = model_for_kind(k["kind"], d, check)
    if not m["model"]:
        out["why"] = m["why"] + "; left to the router"
        return out
    rungs = ladder(d)
    cur, want = rung_index(routed, rungs), rung_index(m["model"], rungs)
    cheap = k["kind"] in CHEAP_KINDS
    if want is None or (cur is not None and ((cheap and want >= cur) or (not cheap and want <= cur))):
        out["why"] = f"{k['kind']} ({k['why']}); {routed} already fits"
        return out
    out.update(model=m["model"], changed=True, why=f"{k['kind']} ({k['why']}): {m['why']}")
    return out


def waste_note(text, model, data=None):
    """-> a sentence when `model` is pinned for a cheap kind of work (never a refusal), else ""."""
    d = data or _models().load()
    k = work_kind(text, d)
    if k["kind"] not in CHEAP_KINDS:
        return ""
    rungs = ladder(d)
    cheap = (((d.get("work_kinds") or {}).get("kinds") or {}).get(k["kind"]) or {}).get("rung")
    a, b = rung_index(model, rungs), rung_index(cheap, rungs)
    if a is None or b is None or a <= b:
        return ""
    return (f"WASTEFUL: this brief is a {k['kind']} job ({k['why']}); {cheap} does it. {model} costs several times "
            f"more per token for the same answer (models.yaml work_kinds). Pinned anyway.")


def prose_ask(prompt, data=None, run=subprocess.run):
    """-> {"ok", "text", "why", "model"}: one cheap model writes prose (`sitrep gather --words`). Never above haiku
    (CHEAP_KINDS rung), no verdict rule: a reply with no VERDICT line is an answer here, never a reason to mark the
    model unavailable. A budget / limit error still marks it (mark_if_gone)."""
    d = data or _models().load()
    m = model_for_kind("report", d)
    rungs = ladder(d)
    top = rung_index("haiku", rungs)
    at = rung_index(m["model"], rungs) if m["model"] else None
    if at is None or top is None or at > top:         # haiku (or a local model off the ladder) only, never above
        return {"ok": False, "text": "", "model": "", "why": m["why"] or "no model at or below haiku is available"}
    model = m["model"]
    pol = _policy(None)
    cmd = ask_cmd(model, pol)
    if not cmd:
        return {"ok": False, "text": "", "model": model, "why": "no model command (auto_accept.reviewer.cmd)"}
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.update(ANTHROPIC_MODEL=model, FIELDKIT_ATTEMPT_MODEL=model, CLAUDE_CODE_DISABLE_ADVISOR_TOOL="1")
    t0 = time.time()
    try:
        r = run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        journal("prose", model=model, outcome="error", why=type(e).__name__)
        return {"ok": False, "text": "", "model": model, "why": f"could not be run: {type(e).__name__}"}
    out = (r.stdout or "").strip()
    gone = mark_if_gone(model, out, r, [1]) if r.returncode != 0 or not out else ""
    journal("prose", model=model, outcome="answered" if out and not gone else "error",
            why=(f"exit {r.returncode}" if r.returncode else ""), seconds=round(time.time() - t0))
    return {"ok": bool(out) and r.returncode == 0, "text": out, "model": model,
            "why": "" if out and r.returncode == 0 else f"exit {r.returncode}" + (f"; {model} marked unavailable {gone}" if gone else "")}


# ---- the one journal ---------------------------------------------------------------------------------------------------
def journal_path(data=None):
    from .friction import state_dir
    try:
        name = cfg(data).get("journal") or "counsel.jsonl"
    except ValueError:
        name = "counsel.jsonl"
    return state_dir() / name


def journal(path, model=None, author=None, outcome="", why="", job=None, data=None, **extra):
    """One row per stronger-model call, whatever the path. Never raises (a journal problem never stops a job)."""
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "path": path, "author": author or "", "model": model or "",
           "outcome": outcome, "why": str(why or "")[:200], "job": job or os.environ.get("FIELDKIT_TASK_ID") or ""}
    rec.update({k: v for k, v in extra.items() if v is not None})
    try:
        with open(journal_path(data), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass
    return rec


def rows(data=None):
    p = journal_path(data)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def summary(rs=None):
    """-> {path: {"calls", "models": {model: n}, "advisor_calls"}} in PATHS order."""
    rs = rows() if rs is None else rs
    out = {}
    for p in PATHS:
        sel = [r for r in rs if r.get("path") == p]
        if not sel:
            continue
        ms = {}
        for r in sel:
            ms[r.get("model") or "-"] = ms.get(r.get("model") or "-", 0) + 1
        out[p] = {"calls": len(sel), "models": ms, "advisor_calls": sum(int(r.get("calls") or 0) for r in sel)}
    return out


def per_job(rs=None):
    """-> [{job, advisor, advisor_calls, runs, reviews, tiebreaks, climbs}] one per job id in the journal."""
    rs = rows() if rs is None else rs
    jobs = {}
    for r in rs:
        j = jobs.setdefault(r.get("job") or "-", {"job": r.get("job") or "-", "advisor": "", "advisor_calls": 0,
                                                  "runs": 0, "reviews": 0, "tiebreaks": 0, "climbs": 0})
        p = r.get("path")
        if p == "advise":
            j["runs"] += 1
            j["advisor"] = r.get("model") or j["advisor"]
            j["advisor_calls"] += int(r.get("calls") or 0)
        elif p in ("review", "tiebreak", "climb"):
            j[{"review": "reviews", "tiebreak": "tiebreaks", "climb": "climbs"}[p]] += 1
    return list(jobs.values())


def lines(rs=None):
    """-> one line per path for `fieldkit review` ([] when nothing was asked)."""
    s = summary(rs)
    if not s:
        return []
    out = ["Stronger models the harness used as a second opinion (information only, nothing for you to decide):"]
    plain = {"review": "checked a finished job", "tiebreak": "settled a disagreement between a check and a reviewer",
             "climb": "took over a job a cheaper model could not finish"}
    for p, c in s.items():
        ms = ", ".join(f"{m} {n}" for m, n in sorted(c["models"].items()))
        if p == "advise":
            used = c["advisor_calls"]
            out.append(f"    {c['calls']} job(s) had a stronger model ({', '.join(sorted(c['models']))}) ready to advise; "
                       + ("none needed it." if not used else f"the workers asked it {used} time(s)."))
        else:
            out.append(f"    {c['calls']} time(s) a stronger model {plain.get(p, p)} ({ms}).")
    return out
