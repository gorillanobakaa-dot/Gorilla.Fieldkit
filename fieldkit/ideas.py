# Version: 1.0.0 · updated 26-10-29-20-40
"""fieldkit ideas: a suggestion made mid-job is parked, never added to the plan that is running (owner, 2026-10-29: the
finish line kept moving because every suggestion went into the current plan).

    fieldkit ideas add "TITLE" --why "what it would do" [--what "2-3 plain sentences"] [--from owner|agent:NAME]
    fieldkit ideas list [--status parked|accepted|forgotten] [--json]
    fieldkit ideas brief           the review brief: each parked idea, its title and what it would do (read only)
    fieldkit ideas built I-NN --pr URL   an agent: the idea is already built (shown in the review; status unchanged)
    fieldkit ideas review          the owner's, at a real terminal: the same as `fieldkit review` (ideas AND inbox)

The store is state/ideas/ideas.json (the truth, for tools) and state/ideas/TODO.Features.Plan.md (the same, written
for a person, rewritten from the json on every change). Both are git-ignored until the owner says "add it to git".
An idea has an id (I-01), a date, a title, what it would do, a source, and a status: parked, accepted or forgotten.
Nothing is ever deleted: a forgotten idea stays in the file, struck through.

Only a person at a terminal changes a status (the same guard as `build-harness decide`: task.owner_terminal(), which
refuses an AI agent's shell even when it has a pty). Accepting assigns the next plan id (P1, P2, ...) and writes a
small brief for `fieldkit task start`, which adds the row to docs/HARNESS-PLAN.md on its own branch. The store is
never created by a read: `list`, `brief` and the hook on a missing store say so or stay silent.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("parked", "accepted", "forgotten")
JSON_NAME = "ideas.json"
MD_NAME = "TODO.Features.Plan.md"


class Refused(Exception):
    pass


def store_dir(root=None):
    return state_root(root or ROOT) / "state" / "ideas"


def _load(root=None):
    p = store_dir(root) / JSON_NAME
    if not p.is_file():
        return {"ideas": []}
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_md(root=None):
    """Rewrite TODO.Features.Plan.md from ideas.json when it is missing or differs (edited by hand, half-written, lost).
    The json is the truth. -> True when it rewrote. A missing json is never created here: nothing to write from."""
    d = store_dir(root)
    if not (d / JSON_NAME).is_file():
        return False
    try:
        want = render(_load(root))
        m = d / MD_NAME
        if m.is_file() and m.read_text(encoding="utf-8") == want:
            return False
        m.write_text(want, encoding="utf-8", newline="\n")
        return True
    except (OSError, ValueError):
        return False


def _stamp(now):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(now() if callable(now) else (now or time.time())))


def _clean(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def render(data):
    out = ["# Parked ideas (TODO.Features.Plan)", "",
           "Written by `fieldkit ideas`; rewritten from ideas.json on every change. An idea changes status only when the",
           "owner answers `fieldkit review` at a terminal. Forgotten ideas stay here, struck through.", ""]
    for i in data["ideas"]:
        t = f"~~{i['title']}~~" if i["status"] == "forgotten" else i["title"]
        out += [f"## {i['id']}: {t}", "",
                f"- date: {i['date']}", f"- source: {i['source']}", f"- status: {i['status']}"
                + (f" ({i['plan_id']})" if i.get("plan_id") else "") + (f", decided {i['decided']}" if i.get("decided") else ""),
                f"- why: {i['why']}"] + ([f"- owner's note: {i['note']}"] if i.get("note") else []) + ["", i["what"], ""]
    return "\n".join(out)


def _save(data, root=None):
    d = store_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / JSON_NAME).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    (d / MD_NAME).write_text(render(data), encoding="utf-8", newline="\n")
    try:
        from . import review
        review.refresh(root)
    except Exception:  # noqa: BLE001 - the page never breaks the store
        pass


def add(title, why, what=None, source="owner", root=None, now=None):
    """Park an idea. -> the new entry. The store is created here, by an add and by nothing else."""
    title, why = _clean(title), _clean(why)
    if not title or not why:
        raise Refused("an idea needs a title and --why (what it would do)")
    if not re.fullmatch(r"owner|agent:[\w.@-]+", source):
        raise Refused("--from is owner or agent:NAME")
    ensure_md(root)
    data = _load(root)
    n = max([int(m.group(1)) for e in data["ideas"] if (m := re.fullmatch(r"I-(\d+)", e["id"]))] or [0]) + 1
    e = {"id": f"I-{n:02d}", "date": _stamp(now), "title": title, "why": why, "what": _clean(what) or why,
         "source": source, "status": "parked"}
    data["ideas"].append(e)
    _save(data, root)
    return e


def listing(status=None, root=None):
    if status and status not in STATUSES:
        raise Refused(f"status is one of {', '.join(STATUSES)}")
    ensure_md(root)
    return [e for e in _load(root)["ideas"] if not status or e["status"] == status]


def parked_count(root=None):
    try:
        return len(listing("parked", root))
    except Exception:  # noqa: BLE001 - a broken store never breaks the command that mentions it
        return 0


def line(root=None):
    n = parked_count(root)
    return f"{n} parked ideas - fieldkit review" if n else ""


def brief(root=None):
    ps = listing("parked", root)
    if not ps:
        return "No parked ideas."
    out = [f"{len(ps)} parked ideas. Each is what the owner (or an agent) suggested while a job ran:"]
    for k, e in enumerate(ps, 1):
        out += ["", f"{k}. {e['id']}  {e['title']}  ({e['source']}, {e['date']})", f"   Would do: {e['what']}"]
        if built_note(e):
            out.append(f"   {built_note(e)}")
    out += ["", "Choices at a terminal (`fieldkit review`): a = accept (becomes a plan row), p = keep parked,",
            "f = forget; a, p or f alone applies to all; numbers (\"1,3\") pick a subset."]
    return "\n".join(out)


def hook_text(session_id, root=None):
    """The session-start text for the hooks: the one line, and on a session's first prompt the brief as well. Reads only
    (plus one marker file per session in the store); "" when nothing is parked or the store is missing."""
    ln = line(root)
    if not ln:
        return ""
    d = store_dir(root)
    mark = d / "briefed" / (re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "") + ".txt")
    if not session_id or mark.exists():
        return ln
    try:
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(_stamp(None), encoding="utf-8")
    except OSError:
        return ln
    return ln + "\n" + brief(root)


def _owner_only():
    from .buildh import task
    if not task.owner_terminal():
        why = task.owner_terminal_why() or "not a person's terminal"
        raise Refused("only the owner, at a real terminal, accepts or forgets an idea; an assistant's shell has none, "
                      f"and an AI never records this answer (refused because: {why})")


def _next_plan_id(data, root):
    used = [int(m.group(1)) for e in data["ideas"] if e.get("plan_id") and (m := re.fullmatch(r"P(\d+)", e["plan_id"]))]
    p = Path(root or ROOT) / "docs" / "HARNESS-PLAN.md"
    if p.is_file():
        used += [int(x) for x in re.findall(r"^\|\s*P(\d+)\s*\|", p.read_text(encoding="utf-8"), re.M)]
    return f"P{max(used or [0]) + 1}"


def set_status(ids, status, root=None, now=None, note=""):
    """Owner terminal only. accepted -> a plan id and a brief file; forgotten -> struck through; parked -> as it was.
    -> [{"id", "status", "plan_id", "brief"}]."""
    _owner_only()
    if status not in STATUSES:
        raise Refused(f"status is one of {', '.join(STATUSES)}")
    data = _load(root)
    by = {e["id"]: e for e in data["ideas"]}
    miss = [i for i in ids if i not in by]
    if miss:
        raise Refused(f"no such idea: {' '.join(miss)}")
    out = []
    for i in ids:
        e = by[i]
        if e["status"] != "parked":
            raise Refused(f"{i} is {e['status']} already; only a parked idea is decided")
        b = None
        if status != "parked":
            e["status"], e["decided"] = status, _stamp(now)
            if note:
                e["note"] = note
        if status == "accepted":
            e["plan_id"] = _next_plan_id(data, root)
            b = _write_brief(e, root)
        out.append({"id": i, "status": e["status"], "plan_id": e.get("plan_id"), "brief": b})
    _save(data, root)
    return out


def _write_brief(e, root):
    d = store_dir(root) / "briefs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{e['plan_id']}.md"
    p.write_text(
        f"# Add plan item {e['plan_id']}: {e['title']}\n\n"
        f"The owner accepted idea {e['id']} on {e['decided']}. Add ONE row for {e['plan_id']} to docs/HARNESS-PLAN.md "
        f"(item table and the Order table, after the current phase), status PLANNED, with this text:\n\n{e['what']}\n\n"
        f"Why: {e['why']}\n\nBranch feat/plan-{e['plan_id'].lower()}, one small PR, `fieldkit ship push`. Do not build the item.\n",
        encoding="utf-8", newline="\n")
    return str(p)


def mark_built(iid, pr, root=None, now=None):
    """An agent records that a parked idea was already built (C7, 2026-11-01: I-09 was built in PR #264 while it sat
    parked). The status stays the owner's; `fieldkit review` and the brief show it as BUILT so the owner can close it."""
    data = _load(root)
    for e in data["ideas"]:
        if e["id"] == iid:
            e["built"] = {"pr": pr, "at": _stamp(now)}
            _save(data, root)
            return e
    raise Refused(f"no idea {iid}")


def built_note(e):
    b = e.get("built") or {}
    return f"ALREADY BUILT ({b.get('pr')}, {b.get('at')}): accepting it closes it as done" if b else ""


def parse_choice(text, n):
    """-> ("a"|"p"|"f", None) for all, ("pick", [indexes]) for numbers, or None when the answer is not understood."""
    t = text.strip().lower()
    if t in ("", "p"):
        return ("p", None)
    if t in ("a", "f"):
        return (t, None)
    if re.fullmatch(r"\d+(\s*,\s*\d+)*", t):
        ix = [int(x) for x in t.split(",")]
        if all(1 <= x <= n for x in ix):
            return ("pick", list(dict.fromkeys(ix)))
    return None


def parse_answer(text, n):
    """-> (action, indexes or None) for "NUMBERS ACTION" and "ACTION NUMBERS" (1,2 a / a 1,2 / 1 f), else parse_choice."""
    t = text.strip().lower()
    m = re.fullmatch(r"([\d,\s]+?)\s+([apf])", t) or re.fullmatch(r"([apf])\s+([\d,\s]+)", t)
    if m:
        nums, act = (m.group(1), m.group(2)) if m.group(1)[0] in "0123456789," else (m.group(2), m.group(1))
        c = parse_choice(nums, n)
        return (act, c[1]) if c and c[0] == "pick" else None
    return parse_choice(t, n)


def review(root=None, ask=input, say=print, now=None):
    """The owner's walk: the one review of everything that waits (parked ideas and inbox items), see review.py."""
    from . import review as _r
    try:
        return _r.review(root, ask=ask, say=say, now=now)
    except _r.Refused as ex:
        raise Refused(str(ex)) from None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ideas", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    pa = sub.add_parser("add")
    pa.add_argument("title")
    pa.add_argument("--why", required=True)
    pa.add_argument("--what")
    pa.add_argument("--from", dest="source", default="owner")
    pl = sub.add_parser("list")
    pl.add_argument("--status", choices=STATUSES)
    sub.add_parser("brief")
    sub.add_parser("review")
    pb = sub.add_parser("built", help="record that a parked idea is already built (the status stays the owner's)")
    pb.add_argument("id")
    pb.add_argument("--pr", required=True)
    for p in (pa, pl):
        p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "add":
            e = add(a.title, a.why, a.what, a.source)
            print(json.dumps(e, indent=1) if a.json else f"parked as {e['id']}")
            return 0
        if a.action == "list":
            es = listing(a.status)
            if a.json:
                print(json.dumps(es, indent=1))
            else:
                for e in es:
                    print(f"{e['id']}  {e['status']:<9}  {e['title']}")
                print("NEXT: fieldkit review (the owner, at a terminal)" if any(e["status"] == "parked" for e in es)
                      else "NEXT: nothing parked")
            return 0
        if a.action == "brief":
            print(brief())
            return 0
        if a.action == "built":
            e = mark_built(a.id, a.pr)
            print(f"{e['id']}: {built_note(e)}\nNEXT: the owner closes it in fieldkit review")
            return 0
        review()
        return 0
    except Refused as ex:
        print(f"fieldkit ideas: {ex}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as ex:
        print(f"fieldkit ideas: the store could not be read or written: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
