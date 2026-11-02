"""Solo / Multiplayer and the model router (owner order 2026-11-02: one hour of parallel jobs and a 300k-context Opus
session burned the 5-hour quota). Deterministic: no network, no model, no third-party router (LiteLLM, Portkey,
RouteLLM and Langfuse are doors to a vendor; the same three moves are done here with a score and a table).

    choice()                  -> {mode, ceiling, accepted, fresh}: the owner's one accept per day, else the cheap defaults
    set_choice(mode, ceiling) the accept (`fieldkit route choose --mode solo --model sonnet`)
    assess(text, plan)        -> {score, rung, reasons}: the cheapest rung the brief's risk needs
    route(text, requested, pin) -> {model, rung, ceiling, action, why}; action is use | downgrade | capped | park
    cascade(model, failures, ceiling) -> the next rung after a failed round, never above the ceiling, or ""
    admit(tasks, on, now)     -> (ok, why): solo = one job at a time and nothing on another device; multi = a cap

A model is never raised above the owner's ceiling. A job whose risk needs the park_at rung (Fable) is parked in the
owner inbox and not started. Every decision is one line in state/lifecycle/routing.jsonl.
"""
import json
import os
import re
import time
from pathlib import Path

import yaml

DATA = Path(__file__).with_name("routing.yaml")
MODES = ("solo", "multi")
PATHISH = re.compile(r"[\w.\-]+[/\\][\w.\-/\\]+|\b[\w\-]+\.(?:py|yaml|yml|md|go|rs|ts|json|sh|ps1)\b")


class ModeRefused(ValueError):
    """The mode does not let this job start now; the message says why and what changes it."""


class ModelParked(ValueError):
    """The job needs the strongest model and is parked in the owner inbox, not started."""


def load(path=None):
    p = Path(path or DATA)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"routing data unreadable: {p}: {e}") from e
    miss = [k for k in ("mode", "ceiling", "ladder", "park_at", "parallel", "score", "high_words", "low_words")
            if k not in d]
    if miss or d["mode"] not in MODES or d["ceiling"] not in d.get("ladder", []):
        raise ValueError(f"routing data {p}: bad or missing {', '.join(miss) or 'mode/ceiling'}")
    return d


def state_dir():
    from .friction import state_dir as s
    return s()


def _choice_path():
    return state_dir() / "mode.json"


def _rung_of(model, ladder):
    m = (model or "").lower()
    for i, name in enumerate(ladder):
        if name in m:
            return i
    return -1


def choice(data=None, today=None):
    """-> {mode, ceiling, accepted (date or ""), fresh}. A missing, stale or damaged accept gives the data's cheap
    defaults (solo, sonnet): an unattended run is never blocked and never silently gets more than that."""
    d = data or load()
    out = {"mode": d["mode"], "ceiling": d["ceiling"], "accepted": "", "fresh": False}
    env = os.environ.get("FIELDKIT_ROUTING_MODE")
    if env in MODES:
        out["mode"] = env
    try:
        c = json.loads(_choice_path().read_text(encoding="utf-8"))
        day = time.strptime(c["accepted"], "%Y-%m-%d")
        age = ((today or time.time()) - time.mktime(day)) / 86400
        if c["mode"] in MODES and _rung_of(c["ceiling"], d["ladder"]) >= 0 and age < d.get("accept_valid_days", 1) + 1:
            out.update(mode=env if env in MODES else c["mode"], ceiling=c["ceiling"], accepted=c["accepted"],
                       fresh=True)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return out


def set_choice(mode, ceiling, data=None):
    """The owner's accept. -> the stored choice. Raises ValueError for a mode or model off the ladder."""
    d = data or load()
    if mode not in MODES:
        raise ValueError(f"mode is one of {', '.join(MODES)}")
    names = [n for n in d["ladder"] if n in (ceiling or "").lower()]
    if not names:
        raise ValueError(f"model is one of {', '.join(d['ladder'])}")
    rec = {"mode": mode, "ceiling": names[0], "accepted": time.strftime("%Y-%m-%d")}
    p = _choice_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def assess(text, plan=None, data=None):
    """-> {score, rung, reasons}: the cheapest ladder rung the brief's risk needs. Same text, same answer."""
    d = data or load()
    low = (text or "").lower()
    s = d["score"]
    score, why = 0, []
    hi = sorted({w for w in d["high_words"] if w in low})
    lo = sorted({w for w in d["low_words"] if re.search(r"\b" + re.escape(w), low)})
    if hi:
        score += 2 * len(hi)
        why.append("high: " + ", ".join(hi))
    if lo:
        score -= len(lo)
        why.append("low: " + ", ".join(lo))
    n = len(text or "") // int(s["chars_per_point"])
    if n:
        score += n
        why.append(f"length +{n}")
    paths = len({m.group(0) for m in PATHISH.finditer(text or "")}) // int(s["paths_per_point"])
    if paths:
        score += paths
        why.append(f"paths +{paths}")
    lad = d["ladder"]
    rung = (lad[0] if score <= s["haiku_max"] else lad[1] if score <= s["sonnet_max"]
            else lad[2] if score <= s["opus_max"] else lad[3])
    return {"score": score, "rung": rung, "reasons": why or ["no risk or ease words"]}


def route(text, requested=None, pin=False, plan=None, data=None, ch=None):
    """-> {model, rung, ceiling, action, why}. requested (the owner's --model, else the accepted ceiling) is the
    ceiling. pin keeps exactly that model. action: use (rung = ceiling), downgrade (the risk needs less), capped
    (the risk needs more than the ceiling; the ceiling stands, the cascade and the reviewer may lift it), park (the
    risk needs park_at: not started)."""
    d = data or load()
    lad = d["ladder"]
    ceiling = (requested or (ch or choice(d))["ceiling"]).strip()
    cr = _rung_of(ceiling, lad)
    if cr < 0:
        return {"model": ceiling, "rung": "", "ceiling": ceiling, "action": "use",
                "why": "a model off the ladder is left to the cost guard (models.py)"}
    a = assess(text, plan, d)
    need = lad.index(a["rung"])
    if pin:
        return {"model": ceiling, "rung": a["rung"], "ceiling": ceiling, "action": "use", "why": "pinned by the owner"}
    if lad[need] == d["park_at"]:
        return {"model": "", "rung": a["rung"], "ceiling": ceiling, "action": "park",
                "why": f"risk score {a['score']} needs {d['park_at']} ({'; '.join(a['reasons'])})"}
    top = _rung_of(d.get("worker_top", ceiling), lad)
    pick = min(need, cr, top if top >= 0 else cr)
    model = ceiling if pick == cr else lad[pick]
    action = "downgrade" if pick < cr else "capped" if need > pick else "use"
    return {"model": model, "rung": a["rung"], "ceiling": ceiling, "action": action,
            "why": f"score {a['score']} -> {a['rung']} ({'; '.join(a['reasons'])}); ceiling {ceiling}"}


def cascade(model, failures, ceiling, data=None):
    """-> the model for the next round after `failures` failed rounds: one rung up per failed round, never above the
    ceiling or worker_top. "" = nothing cheaper-than-the-reviewer is left: the reviewer role repairs it."""
    d = data or load()
    lad = d["ladder"]
    cur, cap = _rung_of(model, lad), _rung_of(ceiling, lad)
    top = _rung_of(d.get("worker_top", ceiling), lad)
    if cur < 0 or cap < 0 or int(failures) < int(d.get("escalate_after_failures", 1)):
        return model
    nxt = min(cur + int(failures) // int(d.get("escalate_after_failures", 1)), cap, top if top >= 0 else cap)
    return lad[nxt] if nxt > cur else ""


def running(tasks, now=None, alive=None):
    """The tasks still running: started, inside their wall time, and (here) with a live process."""
    if alive is None:
        from ..peers.peers import pid_alive as alive
    now = now or time.time()
    out = []
    for t in tasks.values():
        if t.get("status") != "started":
            continue
        try:
            began = time.mktime(time.strptime(t["at"], "%Y-%m-%d %H:%M:%S"))
        except (KeyError, ValueError):
            continue
        if now > began + (int(t.get("wall") or 120) + 15) * 60:
            continue
        if t.get("on") == "here" and t.get("pid"):
            try:
                if not alive(int(t["pid"])):
                    continue
            except (OSError, ValueError):
                continue
        out.append(t)
    return out


def admit(tasks, on=None, now=None, data=None, ch=None, alive=None):
    """-> (ok, why). solo: one job at a time and none on another device; multi: at most parallel.multi."""
    d = data or load()
    ch = ch or choice(d)
    if ch["mode"] == "solo" and on and not d["parallel"].get("solo_allows_peer"):
        return False, (f"solo mode sends nothing to another device (--on {on}); "
                       "`fieldkit route choose --mode multi` allows it")
    cap = int(d["parallel"][ch["mode"]])
    live = running(tasks, now, alive)
    if len(live) >= cap:
        ids = ", ".join(t["id"] for t in live)
        return False, (f"{ch['mode']} mode runs {cap} job(s) at once and {len(live)} still run ({ids}); wait for one "
                       "(`fieldkit task watch ID`) or `fieldkit route choose --mode multi`")
    return True, f"{ch['mode']} mode, {len(live)} of {cap} running"


def log(rec):
    try:
        with open(state_dir() / "routing.jsonl", "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(dict(rec, at=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")
    except OSError:
        pass                              # the decision stands; the ledger is a record, not a gate


def park(title, why):
    """The one exception: a job that needs the strongest model waits in the owner inbox."""
    from ..autopilot import inbox
    inbox.add(f"Parked: {title[:80]}", why, kind="approval")


def main(argv=None):
    argv = list(argv or [])
    cmd = argv[0] if argv else "status"
    d = load()
    if cmd == "choose":
        mode = ceil = None
        it = iter(argv[1:])
        for a in it:
            if a == "--mode":
                mode = next(it, None)
            elif a == "--model":
                ceil = next(it, None)
        try:
            rec = set_choice(mode, ceil, d)
        except ValueError as e:
            print(f"ERROR  {e}")
            return 2
        print(f"ACCEPTED  mode {rec['mode']}, strongest model {rec['ceiling']}, on {rec['accepted']}")
        print("NEXT: jobs now route themselves under this ceiling; one accept per day")
        return 0
    if cmd == "assess":
        text = Path(argv[1]).read_text(encoding="utf-8") if len(argv) > 1 and Path(argv[1]).is_file() else " ".join(argv[1:])
        r = route(text, data=d)
        print(f"{r['action'].upper()}  {r['model'] or 'PARKED'}  {r['why']}")
        print("NEXT: fieldkit task start BRIEF.md   (routes by itself; --model M --pin keeps M)")
        return 0
    if cmd == "status":
        ch = choice(d)
        from . import tasks
        ok, why = admit(tasks.ledger(), None, data=d, ch=ch)
        print(f"MODE  {ch['mode']}  strongest {ch['ceiling']}  accept {ch['accepted'] or 'none (cheap defaults)'}"
              f"{'' if ch['fresh'] else '  STALE'}")
        print(f"ROOM  {why}")
        print("NEXT: fieldkit route choose --mode solo|multi --model haiku|sonnet|opus|fable")
        return 0
    print("route status | choose --mode solo|multi --model M | assess BRIEF.md")
    return 2
