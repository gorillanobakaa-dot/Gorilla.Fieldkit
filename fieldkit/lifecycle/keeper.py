# Version: 1.0.0 · updated 26-11-12
"""THE KEEPER (plan P9): work never waits for a model turn that is not running.

    fieldkit keeper tick [--dry-run] [--scheduled] [--json]   one pass: read tasks, open PRs and the queue; act by the rule table
    fieldkit keeper status [--json]                             the timer, what is open, the last actions
    fieldkit keeper install | remove                            the 5-minute timer (it also installs itself on `task start`)

2026-11-12, 05:47 to 11:17: Zero ended its turn with two jobs started and no watcher of its own. A model session acts
only inside a turn, and a turn starts on a user message or when a background command of that session ends; nothing was
left to wake it. A PR went BEHIND, a job died, one blocked, one PR went red, and all of it waited 5.5 hours for the
owner to type "sitrep". Nothing in the harness moved work on by itself: `task watch` needs a session running it (and
its timer was never installed), the goal supervisor covers goals only.

The keeper is a deterministic pass, no model, idempotent, started by a timer (systemd user timer, Task Scheduler,
LaunchAgent: fieldkit/zero/schedule.py) while anything is open. The rules are DATA (keeper.yaml), the code is the port
of each one:

    behind_update   PR with auto-merge on, CI green, BEHIND main        -> GitHub update-branch, once per head
    red_fix         PR red by its OWN fault (strikes), under TWO STRIKES -> one fix job per (branch, head), brief
                                                                            generated from the failing test names
    died_restart    task died/blocked with work in its worktree          -> restart once (--again, RESUME note naming
                                                                            the worktree and its HANDOFF); second death: parked
    queue_start     first unstarted brief of the queue file + a free slot -> task start
    wake            only a model can decide (a blocked task without a handoff, a red nobody can attribute, a stalled
                    job): said in the room and in Zero's hook file now; a headless Sonnet job with the situation as its
                    brief once the finding is headless_after_min old. Never waits silently.

Every action is a line in state/keeper/keeper.jsonl and in the room ("keeper did X"). When nothing is open the tick
removes its own timer. Never: merge, push, delete, tag, touch main; a parked branch or a strike limit is never overridden.

The queue file (state/coordination/queue.yaml) is a list; the keeper only reads it and remembers what it started:
    - brief: state/briefs/some-job.md      # a path (relative to the checkout) of a brief that names its plan item
      on: peer-name                        # optional, as `task start --on`
      title: optional title
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from . import tasks, watch

DATA = Path(__file__).with_name("keeper.yaml")
BRANCHES = ("feat/", "fix/", "release-", "night/")          # the owner-wide mandate's branches: the PRs the keeper looks after
LOCK_STALE_S = 600


def load(path=None):
    d = yaml.safe_load(Path(path or DATA).read_text(encoding="utf-8")) or {}
    d.setdefault("every_min", 5)
    d.setdefault("hours", 24)
    d.setdefault("rules", {})
    return d


def state_dir():
    from ..core import settings
    d = Path(os.environ.get("FIELDKIT_KEEPER_DIR") or (settings.state_root() / "state" / "keeper"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path():
    return state_dir() / "keeper.json"


def load_state():
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    for k in ("done", "restarts", "roots", "wake", "woken", "queue", "parked"):
        d.setdefault(k, {})
    return d


def save_state(d):
    _state_path().write_text(json.dumps(d, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _stamp(now):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))


@contextlib.contextmanager
def lock(now=None):
    """One tick at a time (the timer and a hand-run tick must not both start a fix job). Yields True when held."""
    p = state_dir() / "keeper.lock"
    now = time.time() if now is None else now
    held = False
    try:
        if p.exists() and now - p.stat().st_mtime > LOCK_STALE_S:
            p.unlink()
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        held = True
    except (FileExistsError, OSError):
        pass
    try:
        yield held
    finally:
        if held:
            with contextlib.suppress(OSError):
                p.unlink()


# ---- the ports: everything the rules touch, so a test hands in fakes -------------------------------------------------

class Ports:
    """The real ports. A test passes its own object with the same method names."""

    def __init__(self, repo=None):
        from ..core import settings
        self.repo = str(repo or settings.state_root())
        self._gh = None

    # tasks
    def ledger(self):
        with contextlib.suppress(Exception):
            tasks.ingest()
            tasks.collect_jobs(tasks.ledger())
        led = tasks.ledger()
        tasks.settle(led)
        return led

    def start(self, brief, **kw):
        return tasks.start(brief, cwd=self.repo, **kw)

    def work_left(self, t):
        """-> why a died/blocked task has work to resume ('' when there is nothing): a HANDOFF.md or a dirty worktree."""
        from ..core import settings
        wt = t.get("worktree") or ""
        if not wt or not Path(wt).is_dir():
            return ""
        for h in (settings.state_root() / "state" / "jobs" / t["id"] / "work" / "HANDOFF.md", Path(wt) / "HANDOFF.md"):
            if h.is_file():
                return f"HANDOFF {h}"
        r = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True, timeout=60)
        return "uncommitted work in the worktree" if r.returncode == 0 and r.stdout.strip() else ""

    def handoff_of(self, t):
        from ..core import settings
        for h in (settings.state_root() / "state" / "jobs" / t["id"] / "work" / "HANDOFF.md",
                  Path(t.get("worktree") or "-") / "HANDOFF.md"):
            if h.is_file():
                return str(h)
        return ""

    # GitHub
    def gh(self):
        if self._gh is None:
            from ..method import train
            self._gh = train.GH(self.repo)
        return self._gh

    def prs(self):
        g = self.gh()
        return g._json("pr", "list", "-R", g.slug, "--state", "open", "--limit", "100", "--json",
                       "number,title,headRefName,headRefOid,isDraft,mergeStateStatus,autoMergeRequest")

    def ci_state(self, sha):
        return self.gh().ci_state(sha)

    def update_branch(self, n, head):
        return self.gh().update_branch(n, head)

    def failed_log(self, sha):
        return self.gh().failed_log(sha)

    # strikes
    def parked(self, branch):
        from ..method import strikes
        return strikes.parked_for(branch)

    def own_red_heads(self, branch):
        from ..method import strikes
        return [r["head"] for r in strikes.load()["strikes"].get(branch, {}).get("rounds", [])]

    def count_red(self, branch):
        from ..method import strikes
        return strikes.check(branch, inbox=lambda *a, **k: None)

    # the queue
    def queue(self, rel):
        p = Path(rel)
        p = p if p.is_absolute() else Path(self.repo) / rel
        try:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or []
        except (OSError, yaml.YAMLError):
            return []

    def admit(self, led, on=None):
        from . import routing
        return routing.admit(led, on)

    # the room, the owner's hook file, the timer
    def room(self, obj, detail):
        tasks._room(obj, detail)

    def watch_findings(self, led):
        return watch.findings(led, notes=(), stalled_min=60)   # a worker thinking for 20 minutes is not stalled

    def watch_act(self, found, led):
        return watch.act(found, led=led)

    def timer_state(self):
        from ..zero import schedule
        return schedule.scheduled(job=JOB)[0]

    def timer_on(self):
        return install()

    def timer_off(self):
        return remove()


JOB = {"task": "Fieldkit keeper", "unit": "fieldkit-keeper", "label": "com.fieldkit.keeper",
       "args": ["-m", "fieldkit", "keeper", "tick", "--scheduled"], "every": 5,
       "what": "fieldkit keeper tick: moves open work on (update-branch, fix job, restart, queue, wake Zero) with no model"}


def _plan(remove_=False):
    from ..core import settings
    from ..zero import schedule
    if remove_:
        return schedule.remove_plan(job=dict(JOB, every=load()["every_min"]))
    from ..capture.switch import python_exe
    return schedule.plan(python=python_exe(), root=settings.state_root(), job=dict(JOB, every=load()["every_min"]))


def install(apply_fn=None):
    """-> "installed" | "not-this-platform" | "skipped (...)" | "ERROR ...". A schedule problem never stops a task from starting."""
    if os.environ.get("FIELDKIT_KEEPER_NO_SCHEDULE"):
        return "skipped (FIELDKIT_KEEPER_NO_SCHEDULE)"
    try:
        from ..zero import schedule
        steps = _plan()
        if not steps:
            return "not-this-platform"
        return "installed" if (apply_fn or (lambda s: schedule.apply(s, say=lambda *_: None)))(steps) == 0 else "ERROR install failed"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR {type(exc).__name__}: {str(exc)[:100]}"


def remove(apply_fn=None):
    if os.environ.get("FIELDKIT_KEEPER_NO_SCHEDULE"):
        return "skipped (FIELDKIT_KEEPER_NO_SCHEDULE)"
    try:
        from ..zero import schedule
        steps = _plan(remove_=True)
        if not steps:
            return "not-this-platform"
        (apply_fn or (lambda s: schedule.apply(s, say=lambda *_: None)))(steps)
        return "removed"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR {type(exc).__name__}: {str(exc)[:100]}"


def ensure_timer(port=None):
    """Called by `task start` after a real launch: the keeper timer is there while a task runs. -> the install answer."""
    try:
        if (port or Ports()).timer_state():
            return "already"
    except Exception:  # noqa: BLE001
        pass
    return install()


# ---- the rules -------------------------------------------------------------------------------------------------------

def _act(rule, key, what, ok=True):
    return {"rule": rule, "key": key, "what": what, "ok": ok}


def rule_behind_update(p, cfg, st, ctx):
    out = []
    for pr in ctx["prs"]:
        n, head = pr["number"], pr["headRefOid"]
        if pr.get("isDraft") or pr.get("mergeStateStatus") != "BEHIND" or not pr.get("autoMergeRequest"):
            continue
        key = f"behind:{n}:{head}"
        if key in st["done"] or ctx["ci"](head)[0] != "green":
            continue
        ok, msg = p.update_branch(n, head)
        st["done"][key] = ctx["stamp"]
        out.append(_act("behind_update", key, f"PR {n} was green and BEHIND main: update-branch " + ("sent" if ok else f"FAILED ({msg[:100]})"), ok))
    return out


def _brief_dir():
    d = state_dir() / "briefs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plan_of(led, branch):
    """The plan id of the job that made `branch` (its ledger row), '' when none says."""
    for t in sorted(led.values(), key=lambda x: x.get("at") or x.get("reported") or "", reverse=True):
        if t.get("branch") == branch and t.get("plan"):
            return t["plan"]
    return ""


def fix_brief(pr, plan, tests, log_tail):
    names = "\n".join(f"- {t}" for t in sorted(tests)) or "- (no pytest ids in the log: read the tail below)"
    return (f"# Fix the red CI of PR {pr['number']} ({pr['headRefName']}), generated by the keeper\n\n"
            f"The head {pr['headRefOid'][:10]} of branch {pr['headRefName']} failed CI by its own change (strikes counted it).\n"
            f"Failing tests:\n{names}\n\nLog tail:\n```\n" + "\n".join(log_tail[-25:]) + "\n```\n\n"
            f"Do: `git fetch origin {pr['headRefName']} && git switch -C {pr['headRefName']} origin/{pr['headRefName']}`, fix the cause "
            "(never weaken the test), run only the tests that failed, commit, `fieldkit ship push --auto`. This is the second "
            "and last try: if the fix fails CI again the branch is parked (TWO STRIKES).\n"
            f"PLAN ITEM: {plan}\n")


def rule_red_fix(p, cfg, st, ctx):
    out = []
    for pr in ctx["prs"]:
        n, head, branch = pr["number"], pr["headRefOid"], pr["headRefName"]
        if pr.get("isDraft") or not branch.startswith(BRANCHES) or p.parked(branch):
            continue
        if ctx["ci"](head)[0] != "red":
            continue
        key = f"fix:{branch}:{head}"
        if key in st["done"]:
            continue
        if head not in p.own_red_heads(branch):
            ckey = f"counted:{branch}:{head}"
            if ckey not in st["done"]:                   # ci-wait counts reds; nobody was waiting: the same judge counts it now
                st["done"][ckey] = ctx["stamp"]
                try:
                    p.count_red(branch)
                except Exception as e:  # noqa: BLE001 - unproven stays a finding
                    out.append(_act("red_fix", ckey, f"PR {n} red: strikes could not judge it ({str(e)[:80]})", False))
            if p.parked(branch):
                out.append(_act("red_fix", key, f"PR {n} ({branch}) parked by the second own red: TWO STRIKES, left for a stronger model"))
                continue
            if head not in p.own_red_heads(branch):
                ctx["wake"].append({"key": f"red-unproven:{n}:{head}", "state": "RED",
                                    "line": f"PR {n} ({branch}) is red and not provably its own fault (main's red or unproven)",
                                    "next": f"fieldkit strikes check {branch}; read `gh pr checks {n}`"})
                continue
        plan = _plan_of(ctx["led"], branch)
        if not plan:
            ctx["wake"].append({"key": f"red-noplan:{n}:{head}", "state": "RED",
                                "line": f"PR {n} ({branch}) is red by its own fault and no job row names its plan item",
                                "next": f"write a fix brief with a PLAN ITEM line for {branch}"})
            continue
        log = p.failed_log(head)
        from ..method import strikes
        bp = _brief_dir() / f"fix-pr{n}-{head[:8]}.md"
        bp.write_text(fix_brief(pr, plan, strikes.failed_tests(log), log), encoding="utf-8")
        st["done"][key] = ctx["stamp"]
        out.append(_start(p, "red_fix", key, bp, f"fix job for red PR {n} ({branch}, {head[:8]})", ctx))
    return out


def _start(p, rule, key, brief, what, ctx, **kw):
    from . import routing
    try:
        rec = p.start(str(brief), **kw)
    except routing.ModeRefused as e:
        ctx["retry"].append(key)                         # no free slot: the next tick tries again
        return _act(rule, key, f"{what}: no free slot ({str(e)[:80]})", False)
    except Exception as e:  # noqa: BLE001 - refusals are named, never retried blind
        return _act(rule, key, f"{what}: not started ({type(e).__name__}: {str(e)[:120]})", False)
    return _act(rule, key, f"{what}: task {rec.get('id')} {rec.get('status')}", rec.get("status") == "started")


def _root(st, t):
    return st["roots"].get(t["id"]) or t.get("hash") or t["id"]


def rule_died_restart(p, cfg, st, ctx):
    out, limit = [], int(cfg.get("max_restarts", 1))
    for t in ctx["led"].values():
        if t.get("status") not in ("died", "blocked") or t.get("on") != "here" or watch.closed(t):
            continue
        ended = watch._t(t.get("reported")) or watch._t(t.get("at"))
        if ctx["now"] - ended > ctx["hours"] * 3600 or watch._followed(t, ctx["led"]):
            continue
        key = f"restart:{t['id']}"
        if key in st["done"]:
            continue
        why = p.work_left(t)
        if not why:
            continue                                     # nothing to resume: wake (watch.findings reports it)
        root = _root(st, t)
        if st["restarts"].get(root, 0) >= limit:
            st["done"][key] = ctx["stamp"]
            st["parked"][root] = ctx["stamp"]
            out.append(_act("died_restart", key, f"{t['id']} died again after its restart: parked, no third try ({why})"))
            continue
        try:
            text = Path(t["brief"]).read_text(encoding="utf-8").strip()
        except (OSError, KeyError):
            continue
        h = p.handoff_of(t)
        note = (f"\n\nRESUME (keeper, {ctx['stamp']}): task {t['id']} {t['status']} before it finished. Its worktree {t['worktree']} "
                f"still holds its work ({why}). Read " + (f"{h}" if h else "its git log and status") +
                " first, carry on from there in that worktree, do not start over, commit what passes.")
        plan = t.get("plan") or ""
        if plan and "PLAN ITEM" not in text:
            note += f"\nPLAN ITEM: {plan}"
        bp = _brief_dir() / f"resume-{t['id']}.md"
        bp.write_text(text + note + "\n", encoding="utf-8")
        wt = Path(t["worktree"])
        st["done"][key] = ctx["stamp"]
        a = _start(p, "died_restart", key, bp, f"restarted {t['id']} ({t['status']}, {why})", ctx, again=True,
                   worktree=lambda repo, tid, _w=wt: (_w, f"resumed worktree of {t['id']}"))
        if a["ok"]:
            st["restarts"][root] = st["restarts"].get(root, 0) + 1
            m = re.search(r"task (T-\S+) ", a["what"])
            if m:
                st["roots"][m.group(1)] = root
        else:
            st["done"].pop(key, None)
        out.append(a)
    return out


def rule_queue_start(p, cfg, st, ctx):
    items = p.queue(cfg.get("file", "state/coordination/queue.yaml"))
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("brief"):
            continue
        key = f"queue:{it['brief']}"
        if key in st["queue"] or key in st["done"]:
            continue
        ok, why = p.admit(ctx["led"], it.get("on", it.get(True)))
        if not ok:
            ctx["retry"].append(key)
            return [_act("queue_start", key, f"queued {it['brief']}: no free slot ({why[:100]})", False)]
        bp = Path(it["brief"])
        bp = bp if bp.is_absolute() else Path(getattr(p, "repo", ".")) / bp
        a = _start(p, "queue_start", key, bp, f"started queued brief {it['brief']}", ctx, on=it.get("on", it.get(True)), title=it.get("title"))
        if a["ok"] or "not started" in a["what"]:
            st["queue"][key] = ctx["stamp"]              # a refusal is said once, never retried blind
        return [a]                                        # one per tick: the router sees the new job next time
    return []


def rule_wake(p, cfg, st, ctx):
    """Whatever a rule could not decide, plus watch's findings no rule handled. Said at once; a headless job when old."""
    out, found = [], list(ctx["wake"])
    handled = {a["key"].split(":", 1)[1] for a in ctx["actions"] if a["rule"] == "died_restart"}
    for f in ctx["watch"]:
        if f["state"] == "UNREAD" or f["id"] in handled or (f["state"] in ("DIED", "UNHANDLED") and ctx["led"].get(f["id"], {}).get("status") in ("died", "blocked") and f"restart:{f['id']}" in st["done"]):
            continue
        found.append({"key": f"{f['state'].lower()}:{f['id']}", "state": f["state"], "line": f["line"], "next": f["next"]})
    keys = {f["key"] for f in found}
    st["wake"] = {k: v for k, v in st["wake"].items() if k in keys}          # a finding that went away is forgotten
    new = [f for f in found if f["key"] not in st["wake"]]
    for f in found:
        st["wake"].setdefault(f["key"], ctx["now"])
    if new and not ctx["dry"]:
        with contextlib.suppress(Exception):
            p.watch_act([{"state": f["state"] if f["state"] in ("UNHANDLED", "DIED", "STALLED") else "STALLED", "id": f["key"],
                          "line": f["line"], "next": f["next"]} for f in found], ctx["led"])
        out += [_act("wake", f["key"], "needs a model, said in the room and Zero's hook file: " + f["line"][:160]) for f in new]
    after = float(cfg.get("headless_after_min", 45)) * 60
    day = int(cfg.get("max_per_day", 3))
    spent = sum(1 for v in st["woken"].values() if ctx["now"] - (watch._t(v) or 0) < 86400)
    for f in found:
        age = ctx["now"] - st["wake"][f["key"]]
        if age < after or f["key"] in st["woken"]:
            continue
        if spent >= day:                                  # a model costs money: the cap is per day, the rest stays said, not started
            out.append(_act("wake", f["key"], f"{day} headless wake jobs in 24 h: {f['key']} stays for Zero or the owner", False))
            st["woken"][f["key"]] = "capped"
            continue
        spent += 1
        bp = _brief_dir() / f"wake-{re.sub(r'[^A-Za-z0-9._-]+', '_', f['key'])}.md"
        bp.write_text(f"# Decide what only a model can: {f['key']}, generated by the keeper\n\n"
                      f"Nobody acted for {int(age / 60)} minutes on this finding:\n\n{f['line']}\n\nSuggested next step: {f['next']}\n\n"
                      "Do the smallest thing that moves the work on (or park it with the reason), then `fieldkit task report` your result.\n"
                      f"PLAN ITEM: {cfg.get('plan', 'P9')}\n", encoding="utf-8")
        st["woken"][f["key"]] = ctx["stamp"]
        out.append(_start(p, "wake", f["key"], bp, f"woke a headless {cfg.get('model', 'sonnet')} job for {f['key']}", ctx,
                          model=cfg.get("model", "sonnet")))
    return out


#: the rule table: name (keeper.yaml) -> function. Order matters: the wake rule runs last and reads what the others left.
RULES = {"behind_update": rule_behind_update, "red_fix": rule_red_fix, "died_restart": rule_died_restart,
         "queue_start": rule_queue_start, "wake": rule_wake}


def is_open(p, cfg, led, prs, st):
    """Is anything open: a running task, a PR of a mandate branch not parked, an unstarted queue item?"""
    if any(t.get("status") in ("started", "waiting-auth") for t in led.values()):
        return True
    if any(not pr.get("isDraft") and pr["headRefName"].startswith(BRANCHES) and not p.parked(pr["headRefName"]) for pr in prs):
        return True
    q = cfg["rules"].get("queue_start") or {}
    if q.get("enabled"):
        items = p.queue(q.get("file", "state/coordination/queue.yaml"))
        return any(isinstance(i, dict) and i.get("brief") and f"queue:{i['brief']}" not in st["queue"] for i in (items if isinstance(items, list) else []))
    return False


def tick(p=None, cfg=None, now=None, dry=False):
    """One pass. -> {"actions": [...], "open": bool, "timer": ..., "held": bool}. Never raises."""
    p = p or Ports()
    cfg = cfg or load()
    now = time.time() if now is None else now
    with lock(now) as held:
        if not held:
            return {"actions": [], "open": True, "timer": "", "held": False, "note": "another tick is running"}
        st = load_state()
        led = p.ledger()
        try:
            prs = p.prs()
            gh_err = ""
        except Exception as e:  # noqa: BLE001 - gh down or signed out: the task rules still run
            prs, gh_err = [], f"gh unreadable: {str(e)[:120]}"
        cache = {}

        def ci(sha):
            if sha not in cache:
                try:
                    cache[sha] = p.ci_state(sha)
                except Exception as e:  # noqa: BLE001
                    cache[sha] = ("none", str(e)[:80])
            return cache[sha]
        try:
            watched = p.watch_findings(led)
        except Exception:  # noqa: BLE001
            watched = []
        ctx = {"led": led, "prs": prs, "ci": ci, "now": now, "stamp": _stamp(now), "hours": cfg["hours"], "wake": [],
               "retry": [], "actions": [], "watch": watched, "dry": dry}
        if dry:
            return {"actions": [], "open": is_open(p, cfg, led, prs, st), "timer": "", "held": True, "dry": True,
                    "findings": [f["line"] for f in watched], "gh": gh_err}
        for name, fn in RULES.items():
            rc = cfg["rules"].get(name) or {}
            if not rc.get("enabled"):
                continue
            try:
                ctx["actions"] += fn(p, rc, st, ctx)
            except Exception as e:  # noqa: BLE001 - one broken rule never stops the others
                ctx["actions"].append(_act(name, name, f"rule failed: {type(e).__name__}: {str(e)[:140]}", False))
        for k in ctx["retry"]:
            st["done"].pop(k, None)
        open_ = is_open(p, cfg, led, prs, st) or any(k not in st["woken"] for k in st["wake"])
        timer = ""
        if not open_ and not gh_err:
            timer = p.timer_off()
            ctx["actions"].append(_act("timer", "off", f"nothing open: timer {timer}"))
        save_state(st)
        journal(p, ctx["actions"], now)
        if gh_err:
            ctx["actions"].append(_act("gh", "unreadable", gh_err, False))
        return {"actions": ctx["actions"], "open": open_, "timer": timer, "held": True}


def journal(p, actions, now=None):
    """Every action: a line in the keeper log and in the room, so `keeper did X at HH:MM` is visible to every session."""
    now = time.time() if now is None else now
    with open(state_dir() / "keeper.jsonl", "a", encoding="utf-8", newline="\n") as f:
        for a in actions:
            f.write(json.dumps(dict(a, at=_stamp(now)), ensure_ascii=False) + "\n")
    for a in actions:
        with contextlib.suppress(Exception):
            p.room("keeper", f"keeper did {a['rule']}: {a['what']}"[:300])


def last_actions(n=10):
    try:
        lines = (state_dir() / "keeper.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    return [json.loads(x) for x in lines if x.strip().startswith("{")]


# ---- Zero's own guard: a Stop hook ------------------------------------------------------------------------------------

def stop_guard(led=None, timer=None, now=None):
    """-> a warning line when a turn ends with jobs in flight and no keeper timer active, else ''. A watcher of the
    session's own is not provable from here, so the timer is the proof: with it, nothing waits on a model turn."""
    try:
        led = tasks.ledger() if led is None else led
        running = [t["id"] for t in led.values() if t.get("status") == "started"]
        if not running:
            return ""
        on = Ports().timer_state() if timer is None else timer
        if on:
            return ""
        return (f"KEEPER WARN: {len(running)} job(s) in flight ({', '.join(running[:3])}) and no keeper timer is active: "
                "nothing wakes anyone when they end. Run `fieldkit keeper install` (or start a background `fieldkit task watch ID`).")
    except Exception:  # noqa: BLE001 - a Stop hook must never break the session
        return ""


# ---- the command ------------------------------------------------------------------------------------------------------

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit keeper", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["tick", "status", "install", "remove"])
    ap.add_argument("--dry-run", action="store_true", help="tick: read and say what is open, change nothing")
    ap.add_argument("--scheduled", action="store_true", help="tick: run by the timer (prints only actions)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.action in ("install", "remove"):
        r = install() if a.action == "install" else remove()
        print(f"keeper timer: {r}\nNEXT: fieldkit keeper status")
        return 0 if not r.startswith("ERROR") else 1
    if a.action == "status":
        p = Ports()
        led = tasks.ledger()
        d = {"timer": p.timer_state(), "running": [t["id"] for t in led.values() if t.get("status") == "started"],
             "last": last_actions(5)}
        print(json.dumps(d, indent=1, ensure_ascii=False) if a.json else
              f"keeper timer {'ACTIVE' if d['timer'] else 'OFF'}; {len(d['running'])} task(s) running\n" +
              "\n".join(f"  {x['at']} {x['rule']}: {x['what']}" for x in d["last"]) + "\nNEXT: fieldkit keeper tick --dry-run")
        return 0
    r = tick(dry=a.dry_run)
    if a.json:
        print(json.dumps(r, indent=1, ensure_ascii=False))
    elif r["actions"] or not a.scheduled:
        print("\n".join(f"keeper {x['rule']}: {x['what']}" for x in r["actions"]) or
              ("keeper: " + ("open work, nothing to move on" if r["open"] else "nothing open")))
        print("NEXT: fieldkit keeper status")
    return 3 if any(not x["ok"] for x in r["actions"]) else 0


if __name__ == "__main__":
    sys.exit(main())
