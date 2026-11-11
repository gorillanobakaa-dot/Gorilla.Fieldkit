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
    out = ["Stronger models the harness asked (counsel journal, every path):"]
    for p, c in s.items():
        ms = ", ".join(f"{m} {n}" for m, n in sorted(c["models"].items()))
        extra = f"; the workers consulted the advisor {c['advisor_calls']} time(s)" if p == "advise" else ""
        out.append(f"    {p:<9} {c['calls']} call(s): {ms}{extra}")
    return out
