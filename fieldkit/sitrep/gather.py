"""`fieldkit sitrep gather`: every fact a sitrep needs, collected by one deterministic command, no model (plan P34).

    fieldkit sitrep gather [--json] [--hours 24]                the facts
    fieldkit sitrep gather --render [--lang ro] [--order classic] [--md]   the facts in the sitrep shape, checked
    fieldkit sitrep gather --words                              one Haiku call turns the render into prose (optional)

Why (owner, 2026-11-12): a sitrep was written by the strongest model after a dozen of its own Bash calls, each of
which re-sent the whole conversation. The facts are in files and one `gh` call; the shape is already a template.
Here the harness reads the facts and fills the template the same way every time: 0 tokens. A model is called only
for `--words`, and counsel.prose_ask never lets that be above Haiku.

Every source is read on its own: one that fails is `{"error": ...}` in its slot and the rest still come back (an
unreachable GitHub, a missing state folder). A fact that could not be read is said, never left out silently.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ENDED = ("done", "failed", "blocked", "died", "not-started")
TAG = re.compile(r"^(\[[A-Z0-9\-]+\]\s*)+#?\s*(\[[A-Z0-9\-]+\]\s*)?#?\s*")


def _try(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as e:  # noqa: BLE001 - one source failing never costs the others
        return {"error": f"{type(e).__name__}: {str(e)[:160]}"}


def short(title, n=70):
    t = TAG.sub("", (title or "").strip().splitlines()[0] if (title or "").strip() else "")
    return (t[:n - 3].rstrip() + "...") if len(t) > n else t


def _age_h(at, now):
    try:
        return (now - time.mktime(time.strptime(at, "%Y-%m-%d %H:%M:%S"))) / 3600
    except (ValueError, TypeError):
        return 1e9


# ---- sources -------------------------------------------------------------------------------------------------------
def log_tail(path, n=3):
    try:
        lines = [l.rstrip() for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except (OSError, TypeError, ValueError):
        return []
    return [l[:160] for l in lines[-n:]]


def board_rows(tid, plan_file=None):
    """-> the Board table lines of PLAN.md that name the task id (at most 2)."""
    try:
        from ..lifecycle import board
        p = Path(plan_file) if plan_file else board.plan_file()
        return [l.strip()[:200] for l in p.read_text(encoding="utf-8").splitlines() if tid in l and "|" in l][:2]
    except (OSError, ValueError, ImportError):
        return []


def tasks(now=None, hours=24, alive=None, ledger=None, plan_file=None):
    """-> {"running": [...], "blocked": [...], "died": [...], "done": [...]} for the last `hours` (running always)."""
    from ..lifecycle import tasks as T
    now = now or time.time()
    led = {k: dict(v) for k, v in (ledger if ledger is not None else T.ledger()).items()}   # settle() on a copy
    T.settle(led, **({"alive": alive} if alive else {}))
    out = {"running": [], "blocked": [], "died": [], "done": [], "failed": [], "not-started": []}
    for t in led.values():
        st = t.get("status", "?")
        key = "running" if st == "started" else st
        if key not in out:
            continue
        if key != "running" and _age_h(t.get("reported") or t.get("at"), now) > hours:
            continue
        row = {k: t.get(k) for k in ("id", "status", "on", "model", "branch", "pr", "at", "note", "answer")
               if t.get(k)}
        row["title"] = short(t.get("title"))
        if key in ("running", "blocked", "died", "failed"):
            row["log_tail"] = log_tail(t.get("log"))
        row["board"] = board_rows(t["id"], plan_file)
        out[key].append(row)
    return out


def ci_state(rollup):
    """-> "failing" | "pending" | "passing" | "none" from a gh statusCheckRollup list."""
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup or []]
    if not states:
        return "none"
    if any(s in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED") for s in states):
        return "failing"
    if any(s in ("PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "EXPECTED", "") for s in states):
        return "pending"
    return "passing"


def prs(run=subprocess.run):
    r = run(["gh", "pr", "list", "--state", "open", "--limit", "30", "--json",
             "number,title,headRefName,statusCheckRollup"], capture_output=True, text=True, timeout=40)
    if r.returncode != 0:
        return {"error": "gh: " + ((r.stderr or r.stdout or "no answer").strip().splitlines() or ["?"])[0][:140]}
    return [{"number": p["number"], "title": short(p.get("title")), "branch": p.get("headRefName"),
             "ci": ci_state(p.get("statusCheckRollup"))} for p in json.loads(r.stdout or "[]")]


def builds(root=None):
    """-> one row per emerge recipe that has a last report: {recipe, ok, at}."""
    from ..core import settings
    base = Path(root) if root else Path(settings.state_root()) / "state" / "emerge"
    out = []
    for p in sorted(base.glob("*/last-report.json")) if base.is_dir() else []:
        try:
            rep = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"recipe": p.parent.name, "ok": rep.get("ok"),
                    "at": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))})
    return out


def inbox():
    from ..autopilot import inbox as I
    return [{"n": it.get("n") or it.get("id"), "title": short(it.get("title"), 90), "why": (it.get("why") or "")[:160]}
            for it in I.items("open")]


def ideas():
    from .. import ideas as I
    return [{"id": e["id"], "title": short(e["title"], 90)} for e in I.listing("parked")]


def defects():
    from .. import refusals
    return {"open": refusals.open_count()}


def watchers():
    """-> the running `task watch` / `ci-wait` processes (Linux; empty elsewhere)."""
    if not sys.platform.startswith("linux"):
        return []
    r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, timeout=10)
    return [{"pid": int(l.split(None, 1)[0]), "what": l.split(None, 1)[1][:100]} for l in r.stdout.splitlines()[1:]
            if re.search(r"fieldkit.*(task watch|ci-wait)", l) and "grep" not in l and "ps -eo" not in l]


def net(now=None):
    from ..zero import net as N
    n = N.load_net()
    z = N.zero_of(n) or {}
    roll = N.roll(n, now or time.time())
    rows = roll.get("rows") or []
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"zero": z.get("station", ""), "epoch": n.get("epoch"), "stations": counts}


def collect(now=None, hours=24, **kw):
    now = now or time.time()
    return {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "hours": hours,
            "tasks": _try(tasks, now, hours, **{k: kw[k] for k in ("alive", "ledger", "plan_file") if k in kw}),
            "prs": _try(prs, *([kw["run"]] if "run" in kw else [])), "builds": _try(builds, kw.get("builds_root")),
            "inbox": _try(inbox), "ideas": _try(ideas), "defects": _try(defects),
            "watchers": _try(watchers), "net": _try(net, now)}


# ---- render: the facts into the sitrep shape, no model --------------------------------------------------------------
def phrases():
    return yaml.safe_load((HERE / "gather_phrases.yaml").read_text(encoding="utf-8"))


def _say(ph, lang, key, **kw):
    t = (ph.get(lang) or {}).get(key) or ph["en"][key]
    return t.format(**kw)


def _n(x, key=None):
    if isinstance(x, dict) and "error" in x:
        return None
    return len(x[key] if key else x)


def build_text(f, lang="en", order="ask-first"):
    """-> the markdown report (the seven headings, any language) for the facts `f`."""
    from . import heading, ORDER_ASK_FIRST, ORDER_CLASSIC
    ph = phrases()
    s = lambda k, **kw: _say(ph, lang, k, **kw)           # noqa: E731
    t = f["tasks"] if "error" not in f["tasks"] else {k: [] for k in ("running", "blocked", "died", "done", "failed",
                                                                          "not-started")}
    prs_ = f["prs"] if isinstance(f["prs"], list) else []
    inbox_ = f["inbox"] if isinstance(f["inbox"], list) else []
    bad = len(t["blocked"]) + len(t["died"]) + len(t["failed"])
    sec = {}
    sec["One line"] = [s("one_line", run=len(t["running"]), bad=bad, prs=len(prs_) if isinstance(f["prs"], list) else "?", inbox=len(inbox_))]
    sec["What I need from you"] = ([s("ask", title=i["title"]) for i in inbox_[:3]] or [s("ask_none")])
    sec["Where we started"] = [s("window", n=len(t["done"]) + bad + len(t["running"]), h=f["hours"])]
    happened = [s("t_" + k, title=x["title"], id=x["id"][-6:]) for k in ("done", "failed", "died", "blocked")
                for x in t[k]][:7]
    sec["What happened"] = happened or [s("nothing")]
    sec["What was achieved"] = [s("pr_of", title=x["title"], pr=x["pr"].rsplit("/", 1)[-1]) for x in t["done"]
                                if x.get("pr")][:5] or [s("nothing")]
    now_ = [s("t_running", title=x["title"], id=x["id"][-6:]) for x in t["running"]][:4]
    now_ += [s("pr_" + p["ci"], n=p["number"], title=p["title"][:40]) for p in prs_[:5]]
    if isinstance(f["prs"], dict):
        now_.append(s("prs_unknown", why=f["prs"]["error"][:80]))
    now_ += [s("build_" + ("ok" if b["ok"] else "bad"), name=b["recipe"], at=b["at"]) for b in
             (f["builds"] if isinstance(f["builds"], list) else [])[:3]]
    if isinstance(f["net"], dict) and "stations" in f["net"]:
        now_.append(s("net", counts=", ".join(f"{v} {k.lower()}" for k, v in sorted(f["net"]["stations"].items()))
                      or "0"))
    if isinstance(f["watchers"], list) and f["watchers"]:
        now_.append(s("watchers", n=len(f["watchers"])))
    sec["Where we are now"] = now_ or [s("nothing")]
    left = [s("ideas", n=len(f["ideas"]))] if isinstance(f["ideas"], list) and f["ideas"] else []
    d = f["defects"].get("open") if isinstance(f["defects"], dict) else 0
    if d:
        left.append(s("defects", n=d))
    left += [s("t_died_left", title=x["title"]) for x in t["died"]][:3]
    sec["What is left"] = left or [s("nothing")]
    out = []
    for h in (ORDER_CLASSIC if order == "classic" else ORDER_ASK_FIRST):
        out.append(f"# {heading(h, lang)}\n")
        for i, line in enumerate(sec[h], 1):
            out.append(f"{i}. {line}" if h in ("What happened", "What I need from you") else f"- {line}")
        out.append("")
    return "\n".join(out)


def render(f, lang="en", order="ask-first", md=False):
    """-> (text, check): the checked report. The check is sitrep.check; a FAIL is returned, never hidden."""
    from . import check, render as R
    text = build_text(f, lang, order)
    r = R(text, lang=lang, order=order, md=md)
    return ((r.get("text") if isinstance(r, dict) else r) or text), check(text)


def main(a):
    """cmd_sitrep's gather action: a is the argparse namespace."""
    f = collect(hours=getattr(a, "hours", 24))
    if getattr(a, "words", False):
        text = build_text(f, a.lang, a.order)
        from ..lifecycle import counsel
        r = counsel.prose_ask("Rewrite this status report as three short plain sentences for the owner, in language "
                              f"code {a.lang}. Keep every number. Add nothing.\n\n" + text)
        print(r["text"] if r["ok"] else f"(no prose: {r['why']})\n" + render(f, a.lang, a.order, a.md)[0])
        return 0
    if getattr(a, "render", False):
        text, chk = render(f, a.lang, a.order, a.md)
        if a.json:
            print(json.dumps({"text": text, "check": chk, "facts": f}, indent=1, ensure_ascii=False))
        else:
            print(text)
            print("NEXT: " + ("none, the report passed its check" if chk.get("ok") else
                              "a sentence failed `fieldkit sitrep check`: " + "; ".join(chk.get("problems", [])[:2])))
        return 0 if chk.get("ok") else 3
    print(json.dumps(f, indent=1, ensure_ascii=False))
    return 0
