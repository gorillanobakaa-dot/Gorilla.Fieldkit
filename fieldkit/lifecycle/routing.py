"""Solo / Multiplayer and the model router (owner order 2026-11-02: one hour of parallel jobs and a 300k-context Opus
session burned the 5-hour quota). Deterministic: no network, no model, no third-party router (LiteLLM, Portkey,
RouteLLM and Langfuse are doors to a vendor; the same three moves are done here with a score and a table).

    choice()                  -> {mode, ceiling, accepted, fresh}: the owner's one accept per day, else the cheap defaults
    set_choice(mode, ceiling) the accept (`fieldkit route choose --mode solo --model sonnet`)
    assess(text, plan)        -> {score, rung, reasons}: the cheapest rung the brief's risk needs
    route(text, requested, pin) -> {model, rung, ceiling, action, why}; action is use | downgrade | capped | park
    cascade(model, failures, ceiling) -> the next rung after a failed round, never above the ceiling, or ""
    admit(tasks, on, now)     -> (ok, why): solo = one job at a time and nothing on another device; multi = a cap;
                                 and no new job once the 5-hour window is nearly spent
    admit_goal(where, live)   the same gate for the goal supervisor's workers
    plan_round(text, requested, failures) -> route() with the cascade applied (only while the rung below really fails)
    outcome(model, ok) / failure_rate(model)   what each rung really did
    refresh_window()          the last 5 hours' dollars from the transcripts into state/lifecycle/window.json
    hook_line(call, event)    the room hook's line: the session ASKS the owner when today's accept is missing

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
        if c["mode"] in MODES and _rung_of(c["ceiling"], d["ladder"]) >= 0 and age < d.get("accept_valid_days", 1):
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
    fl = d.get("floor") or {}
    hits = sorted({w for w in fl.get("words", []) if w in low})
    floor = fl.get("rung", "") if hits and fl.get("rung") in lad else ""
    if floor:
        why.append(f"floor {floor}: " + ", ".join(hits))
        if lad.index(rung) < lad.index(floor):
            rung = floor
    return {"score": score, "rung": rung, "floor": floor, "reasons": why or ["no risk or ease words"]}


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
    w = window_state(d)
    if w and w["fraction"] >= d["window"]["squeeze_fraction"]:
        pick = 0                                       # the window is running out: the cheapest rung only
    fr = lad.index(a["floor"]) if a.get("floor") else -1
    if fr > cr:                                        # the floor is above the owner's ceiling: parked, not shrunk
        return {"model": "", "rung": a["rung"], "ceiling": ceiling, "action": "park",
                "why": f"floor {a['floor']} is above the ceiling {ceiling} ({'; '.join(a['reasons'])})"}
    pick = max(pick, fr)                               # never below the floor, not even in the squeeze
    why = f"score {a['score']} -> {a['rung']} ({'; '.join(a['reasons'])}); ceiling {ceiling}"
    # P9: a model that is out of budget is skipped: the nearest available rung below it (never below the floor); none
    # left: parked with that reason and the retry time, not a failure
    from . import models
    want = pick
    while pick >= max(fr, 0) and not models.is_available(lad[pick], data=models.load()):
        why += f"; {models.availability(lad[pick])[1]}: skipped"
        pick -= 1
    if pick < max(fr, 0):
        back = models.retry_at(lad[max(fr, 0):want + 1])
        return {"model": "", "rung": a["rung"], "ceiling": ceiling, "action": "park",
                "why": why + "; no available model for this job"
                       + (f", retry after {time.strftime('%Y-%m-%d %H:%M', time.localtime(back))}" if back else "")}
    model = ceiling if pick == cr else lad[pick]
    action = "downgrade" if pick < cr else "capped" if need > pick else "use"
    return {"model": model, "rung": a["rung"], "ceiling": ceiling, "action": action, "why": why}


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


def admit(tasks, on=None, now=None, data=None, ch=None, alive=None, window=None):
    """-> (ok, why). solo: one job at a time and none on another device; multi: at most parallel.multi; and no new
    job once the 5-hour window is nearly spent (window.json, kept by refresh_window)."""
    d = data or load()
    ch = ch or choice(d)
    w = window if window is not None else window_state(d, now)
    if w and w["fraction"] >= d["window"]["stop_fraction"]:
        return False, (f"the {d['window']['hours']}-hour window is {w['fraction']:.0%} spent (${w['usd']:.2f} of "
                       f"${w['limit']:.0f}); no new job until it falls, or raise window.usd_limit in routing.yaml")
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


def admit_goal(where, live, tasks=None, now=None, data=None, ch=None, window=None):
    """The goal supervisor's gate for one more worker: `live` workers already run (here and on peers)."""
    d = data or load()
    ch = ch or choice(d)
    if where == "peer" and ch["mode"] == "solo" and not d["parallel"].get("solo_allows_peer"):
        return False, "solo mode sends nothing to another device (`fieldkit route choose --mode multi` allows it)"
    ok, why = admit(tasks or {}, None, now, d, ch, window=window)
    if not ok:
        return ok, why
    cap = int(d["parallel"][ch["mode"]])
    if live >= cap:
        return False, f"{ch['mode']} mode runs {cap} job(s) at once and {live} worker(s) already run"
    return True, why


def window_state(data=None, now=None):
    """-> {usd, limit, fraction, at} from window.json, or None when it is missing, unreadable or older than
    window.max_age_min (a stale reading is no reading: the gate never blocks on old news)."""
    d = data or load()
    try:
        w = json.loads((state_dir() / "window.json").read_text(encoding="utf-8"))
        if (now or time.time()) - float(w["at"]) > d["window"]["max_age_min"] * 60:
            return None
        lim = float(d["window"]["usd_limit"])
        return {"usd": float(w["usd"]), "limit": lim, "fraction": float(w["usd"]) / lim, "at": float(w["at"])}
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def _window_usd(hours, now):
    from datetime import datetime, timezone
    from ..tokens import session_cost as sc
    since = datetime.fromtimestamp(now - hours * 3600, tz=timezone.utc)
    total = 0.0
    for ad, path in sc.touched_since(sc.adapters_for("all"), since):
        calls, _ = sc.measure(ad, path, None, since, None)
        total += sum(float(c.get("usd") or 0) for c in calls)
    return total


def refresh_window(data=None, now=None, spend=None, force=False):
    """Read the last window.hours of dollars from every session transcript (tokens/session_cost.py, read-only) into
    window.json, at most every window.refresh_s seconds. -> the state, or None when it could not be read."""
    d = data or load()
    now = now or time.time()
    if not force:
        cur = window_state(d, now)
        if cur and now - cur["at"] < d["window"]["refresh_s"]:
            return cur
    try:
        usd = float(spend) if spend is not None else _window_usd(d["window"]["hours"], now)
        p = state_dir() / "window.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"at": now, "usd": round(usd, 4), "hours": d["window"]["hours"]}), encoding="utf-8")
    except Exception:  # noqa: BLE001 - a meter that cannot be read leaves the old reading to expire
        return None
    return window_state(d, now)


def outcome(model, ok):
    """One line per finished round: what this model did. The cascade reads them (failure_rate)."""
    log({"action": "outcome", "model": model, "ok": bool(ok)})


def failure_rate(model, data=None):
    """-> (rate, n) over the last 100 outcomes of this rung; (None, n) when n < cascade.min_samples."""
    d = data or load()
    rung = _rung_of(model, d["ladder"])
    rows = []
    try:
        for line in (state_dir() / "routing.jsonl").read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("action") == "outcome" and _rung_of(r.get("model"), d["ladder"]) == rung:
                rows.append(bool(r.get("ok")))
    except OSError:
        pass
    rows = rows[-100:]
    if len(rows) < int(d["cascade"]["min_samples"]):
        return None, len(rows)
    return round(1 - sum(rows) / len(rows), 3), len(rows)


def plan_round(text, requested=None, failures=0, pin=False, plan=None, data=None, ch=None):
    """route() for round number failures+1: the router's pick, climbed one rung per failed round, but only while the
    rung below really fails (failure_rate >= cascade.climb_at, or too few samples to know). -> the route dict."""
    d = data or load()
    r = route(text, requested, pin, plan, d, ch)
    if pin or r["action"] == "park" or not r["model"] or int(failures) < 1:
        return r
    rate, n = failure_rate(r["model"], d)
    if rate is not None and rate < float(d["cascade"]["climb_at"]):
        return dict(r, why=r["why"] + f"; stays on {r['model']} (it fails {rate:.0%} of {n}, below the climb line)")
    up = cascade(r["model"], failures, r["ceiling"], d)
    if up and up != r["model"]:
        return dict(r, model=up, action="escalate", why=r["why"] + f"; round {int(failures) + 1} climbs to {up}")
    return dict(r, why=r["why"] + "; the ceiling is reached: the reviewer role repairs it")


def hook_line(call, event, data=None, ch=None):
    """The room hook's line on a prompt when today's accept is missing: the session asks the owner, once, in its first
    reply. Never inside a headless job. Also keeps window.json fresh. Never raises."""
    try:
        if event != "UserPromptSubmit" or os.environ.get("FIELDKIT_JOB_DIR") or os.environ.get("FIELDKIT_AUTOPILOT"):
            return ""
        if "wt-job-" in str(call.get("cwd") or ""):
            return ""
        d = data or load()
        w = refresh_window(d)
        ch = ch or choice(d)
        out = []
        if not ch["fresh"]:
            out.append("ROUTE: the owner has not chosen today's mode. Ask them ONCE, first thing in your reply, in "
                       "plain words: 'Solo (one job at a time, cheapest) or Multiplayer (several jobs, other "
                       "devices)? And the strongest model a job may use: Fable, Opus, Sonnet or Haiku?' Then run "
                       "`fieldkit route choose --mode solo|multi --model NAME`. Until they answer: solo + "
                       f"{ch['ceiling']}. Jobs are routed to the cheapest model that fits, never above that choice; a "
                       "job that needs Fable is parked for them.")
        if w and w["fraction"] >= d["window"]["warn_fraction"]:
            out.append(f"ROUTE: the {d['window']['hours']}-hour window is {w['fraction']:.0%} spent (${w['usd']:.2f}); "
                       "start no job unless it is needed, and keep this session's replies short.")
        return "\n".join(out)
    except Exception:  # noqa: BLE001 - a courtesy line never breaks the hook
        return ""


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
    if cmd in ("-h", "--help", "help"):
        print("route status | choose --mode solo|multi --model haiku|sonnet|opus|fable | assess BRIEF.md | "
              "window [--refresh]")
        return 0
    d = load()
    if cmd == "window":
        w = refresh_window(d, force="--refresh" in argv) if "--refresh" in argv else window_state(d)
        print(f"WINDOW  {w['fraction']:.0%} of ${w['limit']:.0f} spent (${w['usd']:.2f}, last {d['window']['hours']} h)"
              if w else "WINDOW  no fresh reading: fieldkit route window --refresh")
        print("NEXT: jobs squeeze to the cheapest model at "
              f"{d['window']['squeeze_fraction']:.0%} and stop at {d['window']['stop_fraction']:.0%}")
        return 0
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
        from . import models
        for line in models.unavailable_lines() or ["MODELS  every model is available"]:
            print(line)
        print("NEXT: fieldkit route choose --mode solo|multi --model haiku|sonnet|opus|fable")
        return 0
    print("route status | choose --mode solo|multi --model M | assess BRIEF.md")
    return 2
