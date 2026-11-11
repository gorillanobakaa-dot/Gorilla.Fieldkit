"""No device or session starts an expensive model without a deterministic yes (HARNESS-PLAN I5 follow-up).

    check_model(model, role, origin)   -> {ok, model, tier, verdict, why}; every refusal and every allowed expensive
                                          start is appended to the ledger (model-spend.jsonl)
    guard_launch(argv, env, role, origin)  -> (argv, env) with --model and ANTHROPIC_MODEL set to the checked model;
                                          a headless launch naming no model gets the cheap default; raises Refused
    session_line(model)                -> the advisory line for an interactive session on a premium model, or ""
    report(...)                        -> counts per device and day (fieldkit models report)

The rules are data (models.yaml). The listener checks a remote request against ITS OWN data and mandate; the sender's
word about the model's tier is never read. cheap: any role. review (Opus): the reviewer role only. premium and
unknown: refused unless a mandate in force names the model in `models_allowed`.
"""
import json
import re
import socket
import time
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("models.yaml")
TIERS = ("cheap", "review", "premium")
SANE = re.compile(r"[\w.\-\[\]:/]{1,80}")


class Refused(ValueError):
    """The model may not be started here; the message says why and what allows it."""


def load(path=None):
    p = Path(path or DATA)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"models data unreadable: {p}: {e}") from e
    miss = [k for k in ("default", "cheap", "review", "premium", "ledger") if k not in d]
    if miss:
        raise ValueError(f"models data {p}: lacks {', '.join(miss)}")
    return d


def tier_of(model, data=None):
    """-> cheap | review | premium. Premium names win, then review, then cheap; an unknown name is premium."""
    d = data or load()
    m = (model or "").lower()
    for t in ("premium", "review", "cheap"):
        if any(str(n).lower() in m for n in d[t]):
            return t
    return "premium"


def mandate_allows(model, today=None):
    """-> (allowed, why): a mandate in force (seal, expiry, stop checked by mandate.verify) names the model."""
    try:
        from ..mandate import mandate as M
        for p in M.mandates():
            ok, _ = M.verify(p, today)
            if not ok:
                continue
            for n in (M.load(p).get("models_allowed") or []):
                if str(n).lower() and str(n).lower() in model.lower():
                    return True, f"allowed by the mandate {p.stem} (models_allowed: {n})"
    except Exception as e:  # noqa: BLE001 - no readable mandate = no allow (fail closed)
        return False, f"no mandate could be read ({type(e).__name__})"
    return False, "no mandate in force names it in models_allowed"


def ledger_path(data=None):
    from .friction import state_dir
    return state_dir() / (data or load())["ledger"]


def _append(rec, data):
    with open(ledger_path(data), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def check_model(model, role="worker", origin="local", device=None, today=None, data=None, log=True):
    """-> {ok, model, tier, verdict, why}. model None/empty: the cheap default. Never raises on a refusal."""
    d = data or load()
    asked = (model or "").strip()
    model = asked or d["default"]
    if not SANE.fullmatch(model):
        tier, ok, why = "premium", False, f"{model[:40]!r} is not a plain model name (refused)"
    else:
        tier = tier_of(model, d)
        if tier == "cheap":
            ok, why = True, "cheap tier"
        elif tier == "review" and role == "reviewer":
            ok, why = True, "review tier, reviewer role"
        else:
            ok, why = mandate_allows(model, today)
            if not ok:
                known = tier == "review" or any(str(n).lower() in model.lower() for n in d["premium"])
                why = (f"{model} is {'a reviewer-only' if tier == 'review' else 'a premium' if known else 'an unknown'}"
                       f" model for a {role}: {why}")
    verdict = "allowed" if ok else "refused"
    out = {"ok": ok, "model": model, "tier": tier, "verdict": verdict, "why": why}
    if log and (not ok or tier != "cheap"):
        try:
            from ..peers import machine
            dev = device or machine.current() or socket.gethostname()
            _append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "device": dev, "origin": origin, "model": model,
                     "role": role, "tier": tier, "verdict": verdict}, d)
        except OSError:
            if not ok:
                pass                   # the refusal stands even when the ledger cannot be written
            else:
                return dict(out, ok=False, verdict="refused",
                            why="the spend ledger cannot be written, so an expensive start is not allowed")
    return out


def require(model, role="worker", origin="local", **kw):
    """-> the checked model name, or raises Refused."""
    r = check_model(model, role, origin, **kw)
    if not r["ok"]:
        raise Refused(r["why"])
    return r["model"]


def guard_launch(argv, env, role="worker", origin="local", agent="claude", **kw):
    """-> (argv, env) for a headless launch. An existing --model / --model=X is read, checked and rewritten to the
    checked name; none is added (the cheap default). ANTHROPIC_MODEL in env is checked too and then set to the same
    model, so a settings or env model cannot slip past. Raises Refused. Only claude takes --model here."""
    argv, env = list(argv), dict(env)
    named = None
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            named = (i + 1, argv[i + 1])
        elif a.startswith("--model="):
            named = (i, a.split("=", 1)[1])
    asked = named[1] if named else env.get("ANTHROPIC_MODEL")
    model = require(asked, role, origin, **kw)
    if env.get("ANTHROPIC_MODEL") and env["ANTHROPIC_MODEL"] != model:
        require(env["ANTHROPIC_MODEL"], role, origin, **kw)
    if agent == "claude":
        if named and argv[named[0]].startswith("--model="):
            argv[named[0]] = f"--model={model}"
        elif named:
            argv[named[0]] = model
        else:
            argv += ["--model", model]
        env["ANTHROPIC_MODEL"] = model
    return argv, env


def session_line(model, data=None):
    """Advisory only: the owner chooses interactive models. -> one line for a premium model, else ''."""
    if not model:
        return ""
    d = data or load()
    if tier_of(model, d) == "cheap":
        return ""
    return f"model {model} is premium; jobs default to {d['default']}"


def read_ledger(data=None):
    p = ledger_path(data)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("event") != "rung":
            out.append(row)
    return out


def report(rows=None):
    """-> {(day, device, verdict): count} sorted, from the ledger."""
    counts = {}
    for r in read_ledger() if rows is None else rows:
        k = (str(r.get("at", ""))[:10], r.get("device", "?"), r.get("verdict", "?"), r.get("model", "?"))
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items()))


# ---- plan P9: escalation ladder -------------------------------------------------------------------------------------
def ladder_cfg(data=None):
    return dict((data or load()).get("escalation") or {})


def next_model(model, own_failures, data=None):
    """-> the model for the next attempt. Below `own_limit` own failures on this rung the same model retries; at
    `own_limit` the next rung up. None past the top rung (park it). A model not on the ladder: None (no guess)."""
    cfg = ladder_cfg(data)
    rungs = [str(x).lower() for x in cfg.get("ladder") or ()]
    low = (model or "").lower()
    idx = next((i for i, r in enumerate(rungs) if r in low), None)
    if idx is None:
        return None
    if own_failures < int(cfg.get("own_limit") or 2):
        return model
    return rungs[idx + 1] if idx + 1 < len(rungs) else None


def climb(model, own_failures, data=None):
    """P9: the ladder asked for the next attempt. -> {"next": model | None, "same": bool, "skipped": [rungs]}.
    next == model (same True): retry on this rung. A rung the spend guard refuses (check_model, reviewer role: Opus is
    the review tier; Fable is premium and needs a mandate) is skipped, never bypassed; no allowed rung left: next None
    (park). Ladder off or a model not on it: next None."""
    cfg = ladder_cfg(data)
    out = {"next": None, "same": False, "skipped": []}
    if not cfg.get("enabled"):
        return out
    nxt = next_model(model, own_failures, data)
    if nxt is None:
        return out
    if nxt == model:
        return dict(out, next=model, same=True)
    rungs = [str(x).lower() for x in cfg.get("ladder") or ()]
    for r in rungs[rungs.index(nxt.lower()):]:
        v = check_model(r, role="reviewer", origin="ladder", data=data, log=False)
        if v["ok"]:
            return dict(out, next=r)
        out["skipped"].append(r)
    return out


def tried_note(models_tried):
    """-> 'tried: haiku, sonnet' (each model once, in order), for a parked note."""
    seen = []
    for m in models_tried:
        if m and m not in seen:
            seen.append(m)
    return "models tried: " + (", ".join(seen) if seen else "unknown")


def needs_plan_review(job_type, data=None):
    """-> True when a job type names a risky area (guards, fan, security, releases): a top model reviews the plan once
    before cheap models start."""
    low = (job_type or "").lower()
    return any(str(w).lower() in low for w in ladder_cfg(data).get("risky") or ())


def attempts_path(data=None):
    from .friction import state_dir
    return state_dir() / (ladder_cfg(data).get("attempts") or "escalation.jsonl")


def record_attempt(branch, job_type, model, outcome, own=True, data=None):
    """One attempt: outcome pass | fail; own False = the failure was main's or a flake (not the branch's, no strike)."""
    if outcome not in ("pass", "fail"):
        raise ValueError("outcome is pass or fail")
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "branch": branch, "job_type": job_type or "?",
           "model": model or "?", "outcome": outcome, "own": bool(own)}
    with open(attempts_path(data), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def record_rung(branch, job_type, frm, to, why="", skipped=(), data=None):
    """A rung change (or a park: to None) is one row in escalation.jsonl, marked event=rung (not an attempt)."""
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "event": "rung", "branch": branch, "job_type": job_type or "?",
           "from": frm or "?", "to": to or "PARK", "why": str(why)[:200], "skipped": list(skipped)}
    with open(attempts_path(data), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def read_attempts(data=None):
    p = attempts_path(data)
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def success_table(rows=None, data=None):
    """-> {(model, job_type): {"tries", "passed", "own_failed", "rate"}}. A failure that was not the branch's own
    (main's red, a flake) is left out of the rate: it says nothing about the model. rate None with no countable try."""
    t = {}
    for r in read_attempts(data) if rows is None else rows:
        if r.get("outcome") == "fail" and not r.get("own", True):
            continue
        c = t.setdefault((r.get("model", "?"), r.get("job_type", "?")), {"tries": 0, "passed": 0, "own_failed": 0})
        c["tries"] += 1
        if r.get("outcome") == "pass":
            c["passed"] += 1
        else:
            c["own_failed"] += 1
    for c in t.values():
        c["rate"] = round(c["passed"] / c["tries"], 2) if c["tries"] else None
    return dict(sorted(t.items()))


def best_model(job_type, min_tries=3, rows=None, data=None):
    """-> the cheapest ladder model whose success rate on this job type is at least 0.8 over `min_tries` countable
    tries, else None (no history: start at the ladder's second rung by default, the caller's call)."""
    cfg = ladder_cfg(data)
    tab = success_table(rows, data)
    for rung in cfg.get("ladder") or ():
        for (m, jt), c in tab.items():
            if jt == job_type and rung.lower() in m.lower() and c["tries"] >= min_tries and (c["rate"] or 0) >= 0.8:
                return m
    return None


def main(argv=None):
    argv = list(argv or [])
    if argv[:1] in (["gguf"], ["bandwidth"]):        # P21: dense or MoE, active parameters, expected speed
        from . import gguf
        rc = gguf.main(argv)
        if rc is not None:
            return rc
        print("models gguf FILE|URL [--file NAME] [--bandwidth GBPS] [--json]: dense or MoE, active parameters, expected tokens/s")
        return 2
    if argv[:1] == ["tune"]:                         # P22: host analysis, right load settings, lms load
        from . import tune
        return tune.main(argv)
    if argv[:1] == ["table"]:
        tab = success_table()
        if "--json" in argv:
            print(json.dumps([dict(model=m, job_type=j, **c) for (m, j), c in tab.items()], indent=1))
        for (m, j), c in tab.items():
            if "--json" not in argv:
                print(f"{m:<22} {j:<20} {c['passed']}/{c['tries']} passed  rate {c['rate']}")
        if not tab and "--json" not in argv:
            print("no attempt recorded")
        print("NEXT: the table decides delegation (best_model); main's reds and flakes are not counted")
        return 0
    if argv[:1] != ["report"]:
        print("models report [--json]: counts per device and day from the spend ledger\n"
              "models table [--json]: success rate per model and job type (plan P9)\n"
              "models gguf FILE|URL: before loading a local model, dense or MoE and the expected speed (P21)\n"
              "models tune MODEL [--apply]: analyse this machine and the GGUF, pick the load settings, load through lms (P22)\n"
              "              models tune --record MODEL --first-word S --tps N | --report | --host\n"
              "models bandwidth [--measure]: this machine's memory bandwidth")
        return 2
    try:
        rep = report()
    except (OSError, ValueError) as e:
        print(f"ERROR  {e}")
        return 1
    if "--json" in argv:
        print(json.dumps([dict(zip(("day", "device", "verdict", "model"), k), count=v) for k, v in rep.items()],
                         indent=1))
    else:
        for (day, dev, verdict, model), n in rep.items():
            print(f"{day}  {dev:<14} {verdict:<8} {model:<22} {n}")
        if not rep:
            print("no expensive start or refusal recorded")
    print("NEXT: a refusal is right unless the owner signs models_allowed in a mandate (fieldkit mandate approve)")
    return 0
