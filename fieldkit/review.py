# Version: 1.0.0 · updated 26-10-31-12-00
"""fieldkit review: ONE list of everything that waits on the owner, answered once (owner, 2026-10-31).

    fieldkit review            the owner's, at a real terminal: main goal, everyday problems, then parked ideas, numbered 1..N
    fieldkit review --open     write state/REVIEW.md (the same list) and open it in the default viewer
    fieldkit review --list     print the list, change nothing

`fieldkit ideas review` and `fieldkit inbox review` run the same code. The answer is one line:
`a` approve all, `p` or Enter keep all waiting, `f` forget/dismiss all, numbers (`1,3`) or both (`1,3 a`); words after a
colon are recorded as the owner's note (`2 a: core embedded + media pack`).

Approve on an idea = accepted (a plan id and a brief, as `ideas review`). Approve on an inbox item = done, note
"approved by the owner at a terminal: WORDS", field `approved` true, so the job or Zero waiting on it reads the answer.
Forget on an inbox item = done, "dismissed by the owner". An item of kind choice/decision (or with `choice` set) needs
words: they ARE the answer. An item's command is never run here. Owner terminal only (task.owner_terminal()).
state/REVIEW.md is rewritten on every inbox or ideas change.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from . import ideas
from .core.settings import state_root

ROOT = ideas.ROOT
CHOICE_KINDS = ("choice", "decision")


class Refused(Exception):
    pass


def page_path(root=None):
    return state_root(root or ROOT) / "state" / "REVIEW.md"


def _inbox():
    from .autopilot import inbox
    return inbox


def needs_words(it):
    return bool(it.get("choice")) or it.get("kind") in CHOICE_KINDS


GOAL = ("THE MAIN GOAL (it never changes): one command builds a whole product, like Gentoo's Portage builds a whole "
        "system, while you are away; scripts do the work and an AI is called only when a step fails.")
FINAL = ("The final goal (plan row W10): once you say the harness works perfectly, Fieldkit is built into Gorilla "
         "OpenCode so the two become one program. Not now.")
NO_WORDS = "(no plain explanation was written for this item)"
PLAN_REF = re.compile(r"\b([RLOCIWPZ]\d+)[a-z]?\b")
GROUPS = (("goal", "Main goal: decisions that move it"),
          ("problem", "Everyday problems that came up"),
          ("idea", "New ideas (not in the plan until you accept)"))


def plan_titles(root=None):
    """-> {row id: plain title} from docs/HARNESS-PLAN.md: the row's first sentence, cut at ':' or '('."""
    from . import plan
    p = Path(root or ROOT) / plan.PLAN
    out = {}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        m = re.match(r"^\|\s*([RLOCIWPZ]\d+)\s*\|", line)
        if m and m.group(1) not in out:
            t = re.split(r"[:(]", plan._cells(line)[1])[0].strip(" .")
            out[m.group(1)] = t[:140]
    return out


def plan_of(it, titles):
    """The plan row an inbox item belongs to: its `plan` field, else a row id named in title/why, else ''."""
    if it.get("plan"):
        return str(it["plan"])
    for m in PLAN_REF.finditer(f"{it.get('title', '')} {it.get('why', '')}"):
        if m.group(1) in titles:
            return m.group(1)
    return ""


def entries(root=None):
    """The page's items in page order (main-goal inbox items, everyday inbox items, parked ideas):
    [{"type", "group", "plan", "ref", "title", "what", "if_approve", "if_leave", "command", "words", "raw"}]."""
    titles = plan_titles(root)
    out = []
    for it in _inbox().items("open"):
        row = plan_of(it, titles)
        out.append({"type": "inbox", "group": "goal" if row else "problem", "plan": row, "ref": f"#{it['n']}",
                    "title": it["title"], "what": it.get("what", ""), "if_approve": it.get("if_approve", ""),
                    "if_leave": it.get("if_leave", ""), "command": it.get("command", ""), "words": needs_words(it),
                    "raw": it})
    for e in ideas.listing("parked", root or ROOT):
        out.append({"type": "idea", "group": "idea", "plan": "", "ref": e["id"], "title": e["title"], "what": e["what"],
                    "if_approve": "It becomes a row in the plan, with a brief a worker can start.",
                    "if_leave": "It stays parked here; the running plan does not change.", "command": "", "words": False,
                    "raw": e})
    out.sort(key=lambda e: [g for g, _ in GROUPS].index(e["group"]))
    return out


def where_we_are(root=None):
    """Plain lines: the phase now and its rows, each id spelled out."""
    from . import plan
    r = plan.next_(root or ROOT)
    if not r.get("ok") or not r.get("phase"):
        return ["Where we are: the plan file is not readable here."]
    titles = plan_titles(root)
    out = [f"Where we are: phase {r['phase']} of the plan is the current one. Rows not finished yet:"]
    for ph in r["phases"]:
        for i in ph["open"]:
            out.append(f"  - {i} - {titles.get(i, '(no title)')} [phase {ph['phase']}]")
    return out


def _item_lines(k, e, titles):
    ref = e["ref"] + (f", plan row {e['plan']}" if e["plan"] else "")
    out = ["", f"{k}. {e['title']}  [{ref}]"]
    if e["plan"]:
        out.append(f"   Plan row: {e['plan']} - {titles.get(e['plan'], '(row not found)')}")
    out += [f"   What it is: {e['what'] or NO_WORDS}",
            f"   If you approve: {e['if_approve'] or NO_WORDS}",
            f"   If you leave it: {e['if_leave'] or NO_WORDS}"]
    if e["words"]:
        out.append("   This needs a choice: your words after ':' are the answer.")
    if e["command"]:
        out.append(f"   Command (review never runs it): {e['command']}")
    return out


def render(es, root=None):
    titles = plan_titles(root)
    out = [GOAL, FINAL, ""] + where_we_are(root)
    if not es:
        return out + ["", "Nothing waits for you."]
    out += ["", f"{len(es)} item(s) wait for you, in three groups."]
    k = 0
    for g, head in GROUPS:
        group = [e for e in es if e["group"] == g]
        out += ["", "=" * 60, f"{head} ({len(group)})", "=" * 60]
        if not group:
            out.append("Nothing here.")
        for e in group:
            k += 1
            out += _item_lines(k, e, titles)
    out += ["", "Answer once: a = approve all, p or Enter = keep all waiting, f = forget/dismiss all,",
            "numbers (1,3) pick some, or both (1,3 a); words after ':' are your note (2 a: core embedded)."]
    return out


def write_page(root=None):
    """Rewrite state/REVIEW.md from the stores. -> the path, or None when it could not be written."""
    try:
        text = "\n".join(["# Waiting on the owner", "",
                          "Written by `fieldkit review`; rewritten on every inbox or ideas change. Answer at a terminal: "
                          "`fieldkit review` (or double-click launchers/review.cmd, review.sh, review.command).", ""]
                         + render(entries(root), root)) + "\n"
        p = page_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p
    except Exception:  # noqa: BLE001 - the page never breaks the change that triggered it
        return None


def refresh(root=None):
    return write_page(root)


def _owner_only():
    from .buildh import task
    if not task.owner_terminal():
        why = task.owner_terminal_why() or "not a person's terminal"
        raise Refused("only the owner, at a real terminal, answers the review; an assistant's shell has none, "
                      f"and an AI never records this answer (refused because: {why})")


def split_answer(text):
    """'1,3 a: words' -> ('1,3 a', 'words')."""
    head, _, words = text.partition(":")
    return head, " ".join(words.split())


def apply(chosen, action, words, root=None, now=None, say=print):
    """Record one action ('a' or 'f') on the chosen entries. -> [result dicts]."""
    res = []
    ib = _inbox()
    idea_ids = [e["raw"]["id"] for e in chosen if e["type"] == "idea"]
    if idea_ids:
        status = "accepted" if action == "a" else "forgotten"
        for r in ideas.set_status(idea_ids, status, root, now, note=words):
            res.append(r)
            say(f"{r['id']} {r['status']}" + (f" as {r['plan_id']}; brief {r['brief']}" if r["plan_id"] else ""))
    for e in chosen:
        if e["type"] != "inbox":
            continue
        n = e["raw"]["n"]
        if action == "a":
            if e["words"] and not words:
                say(f"{e['ref']} needs a choice: answer again with words after ':' (left open)")
                continue
            ib.done(n, "approved by the owner at a terminal" + (f": {words}" if words else ""), approved=True, answer=words)
            say(f"{e['ref']} approved")
        else:
            ib.done(n, "dismissed by the owner" + (f": {words}" if words else ""), approved=False, answer=words)
            say(f"{e['ref']} dismissed")
        res.append({"id": e["ref"], "status": "approved" if action == "a" else "dismissed", "brief": None, "plan_id": None})
    return res


def review(root=None, ask=input, say=print, now=None):
    """The owner's single pass over everything waiting. -> list of results (empty when nothing changed)."""
    _owner_only()
    es = entries(root)
    write_page(root)
    say("\n".join(render(es, root)))
    if not es:
        return []
    for _ in range(5):
        head, words = split_answer(ask("Your answer [a/p/f/numbers, or both: 1,3 a; ': words' = note; Enter = keep waiting]: "))
        c = ideas.parse_answer(head, len(es))
        if c:
            break
        say("Not understood: a, p, f, numbers such as 1,3, or both such as 1,3 a (then ': words' if you like).")
    else:
        return []
    kind, ix = c
    chosen = es if ix is None else [es[i - 1] for i in ix]
    if kind == "pick":
        for _ in range(5):
            a = ideas.parse_choice(ask(f"For {', '.join(e['ref'] for e in chosen)} [a/p/f]: "), 0)
            if a and a[0] in ("a", "p", "f"):
                kind = a[0]
                break
        else:
            return []
    if kind == "p":
        say("Kept waiting.")
        return []
    res = apply(chosen, kind, words, root, now, say)
    write_page(root)
    briefs = [r["brief"] for r in res if r.get("brief")]
    say("NEXT: " + (f"fieldkit task start {briefs[0]}" if briefs else "the jobs waiting on these read the answer and go ahead"))
    return res


def open_page(root=None, run=None):
    p = write_page(root)
    if p is None:
        raise Refused("the page could not be written")
    if run is not None:
        run(str(p))
    elif sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606 - the owner's own page
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)])
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit review", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--open", action="store_true", help="write state/REVIEW.md and open it")
    ap.add_argument("--list", action="store_true", help="print the list, change nothing")
    a = ap.parse_args(argv)
    try:
        if a.open:
            print(f"opened {open_page()}")
        elif a.list:
            write_page()
            print("\n".join(render(entries())))
        else:
            review()
        return 0
    except (Refused, ideas.Refused) as ex:
        print(f"fieldkit review: {ex}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as ex:
        print(f"fieldkit review: a store could not be read or written: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
