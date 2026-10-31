# Version: 1.0.0 · updated 26-10-31-12-00
"""fieldkit review: ONE list of everything that waits on the owner, answered once (owner, 2026-10-31).

    fieldkit review            the owner's, at a real terminal: parked ideas (I-nn) first, then open inbox items (#nnn)
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
import subprocess
import sys

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


def entries(root=None):
    """Parked ideas first, then open inbox items -> [{"type", "ref", "title", "why", "command", "words", "raw"}]."""
    out = []
    for e in ideas.listing("parked", root or ROOT):
        out.append({"type": "idea", "ref": e["id"], "title": e["title"], "why": e["what"] or e["why"], "command": "",
                    "words": False, "raw": e})
    for it in _inbox().items("open"):
        out.append({"type": "inbox", "ref": f"#{it['n']}", "title": it["title"], "why": it.get("why", ""),
                    "command": it.get("command", ""), "words": needs_words(it), "raw": it})
    return out


def render(es):
    if not es:
        return ["Nothing waits for the owner."]
    out = [f"{len(es)} item(s) wait for the owner (parked ideas first, then inbox items):"]
    for k, e in enumerate(es, 1):
        out += ["", f"{k}. {e['ref']}  {e['title']}", f"   Why: {e['why']}"]
        if e["command"]:
            out.append(f"   Would run (review never runs it): {e['command']}")
        if e["words"]:
            out.append("   This needs a choice: your words after ':' are the answer.")
    out += ["", "Answer once: a = approve all, p or Enter = keep all waiting, f = forget/dismiss all,",
            "numbers (1,3) pick some, or both (1,3 a); words after ':' are your note (2 a: core embedded)."]
    return out


def write_page(root=None):
    """Rewrite state/REVIEW.md from the stores. -> the path, or None when it could not be written."""
    try:
        text = "\n".join(["# Waiting on the owner", "",
                          "Written by `fieldkit review`; rewritten on every inbox or ideas change. Answer at a terminal: "
                          "`fieldkit review` (or double-click launchers/review.cmd, review.sh, review.command).", ""]
                         + render(entries(root))) + "\n"
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
    say("\n".join(render(es)))
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
