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
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def report(rows=None):
    """-> {(day, device, verdict): count} sorted, from the ledger."""
    counts = {}
    for r in read_ledger() if rows is None else rows:
        k = (str(r.get("at", ""))[:10], r.get("device", "?"), r.get("verdict", "?"), r.get("model", "?"))
        counts[k] = counts.get(k, 0) + 1
    return dict(sorted(counts.items()))


def main(argv=None):
    argv = list(argv or [])
    if argv[:1] != ["report"]:
        print("models report [--json]: counts per device and day from the spend ledger")
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
