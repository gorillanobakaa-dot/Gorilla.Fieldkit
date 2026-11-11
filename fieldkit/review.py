# Version: 1.0.0 · updated 26-10-31-12-00
"""fieldkit review: ONE list of everything that waits on the owner, answered once (owner, 2026-10-31).

    fieldkit review            the owner's, at a real terminal: main goal, everyday problems, then parked ideas, numbered 1..N
    fieldkit review --inbox    write state/INBOX.md (waiting items with why + command, and the agent ledger) and open it
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
NL = "\n"


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
    ib = _inbox()
    ib.migrate_legacy()                               # 2026-11-10: only a kind that reaches the owner is his to answer
    for it in ib.items("open"):
        if it.get("kind") not in ib.OWNER_KINDS:
            continue
        row = plan_of(it, titles)
        w = ib.words_for(it)
        out.append({"type": "inbox", "group": "goal" if row else "problem", "plan": row, "ref": f"#{it['n']}",
                    "title": it["title"], "what": w["what"], "if_approve": w["if_approve"],
                    "if_leave": w["if_leave"], "command": it.get("command", ""), "words": needs_words(it),
                    "raw": it})
    in_plan = ideas_in_plan(root)
    for e in ideas.listing("parked", root or ROOT):
        built = ideas.built_note(e)
        row = idea_row(in_plan, e)
        if row and not built:                         # 2026-11-10: I-01/I-02 were built as P23/P24 and still said
            built = (f"ALREADY BUILT as plan row {row['row']} ({row['pr']}): accepting it closes it as done"   # "becomes a row"
                     if row["pr"] else f"ALREADY IN THE PLAN as row {row['row']}: accepting it closes the idea")
        appr = ("It is closed as done: the work is already merged; no new plan row and no brief are made."
                if built.startswith("ALREADY BUILT") else
                f"It is closed: plan row {row['row']} already carries it and stays as it is; no new row is made."
                if built else "It becomes a new row in the plan, with a brief a worker can start.")
        out.append({"type": "idea", "group": "idea", "plan": row["row"] if row else "", "ref": e["id"], "title": e["title"],
                    "what": e["what"] + (f" {built}." if built else ""),
                    "if_approve": appr,
                    "if_leave": "It stays parked here; the running plan does not change.", "command": "", "words": False,
                    "raw": e})
    out.sort(key=lambda e: [g for g, _ in GROUPS].index(e["group"]))
    return out


def ideas_in_plan(root=None):
    """-> {idea id: [{"row", "pr", "line"}]} for every plan row that names an idea ("idea I-01"), with the PR that merged it
    (plan.merged, or a DONE (PR #N) status) or "" when it is not built yet."""
    import re as _re
    from . import plan
    p = Path(root or ROOT) / plan.PLAN
    if not p.is_file():
        return {}
    text = p.read_text(encoding="utf-8")
    st, mg = plan.items(text), plan.merged(Path(root or ROOT))
    out = {}
    for line in text.splitlines():
        m = plan.ROW_ID.match(line)
        if not m:
            continue
        for idea in _re.findall(r"\bidea (I-\d+)", line):
            done = _re.search(r"PR (#\d+)", st.get(m.group(1), ""))
            out.setdefault(idea, []).append({"row": m.group(1), "line": line,
                                             "pr": (done and done.group(1)) or mg.get(m.group(1), "")})
    return out


def idea_row(in_plan, e):
    """The plan row for this idea: one that names its id AND its date (ids were reused: an October I-01 is row P2,
    the 2026-11-10 I-01 is P23), else None - never a guess."""
    day = str(e.get("date") or "")[:10]
    return next((r for r in in_plan.get(e["id"], []) if day and day in r["line"]), None)


def where_we_are(root=None):
    """Plain lines: the phase now and its rows, each id spelled out."""
    from . import plan
    r = plan.next_(root or ROOT)
    if not r.get("ok"):
        return ["Where we are: the plan file is not readable here."]
    titles = plan_titles(root)
    out = ([f"Where we are: phase {r['phase']} of the plan is the current one. Rows not finished yet:"] if r.get("phase")
           else ["Where we are: no plan row is open to start."])
    for ph in r["phases"]:
        for i in ph["open"]:
            out.append(f"  - {i} - {titles.get(i, '(no title)')} [phase {ph['phase']}]")
    # 2026-11-10 (owner): a row the owner holds (W10, the final goal) is never shown as work to do
    held = [i for ph in r["phases"] for i in ph.get("held", [])]
    if held:
        out.append("Held by the owner, not work to do (only when you say so, at the very end):")
        out += [f"  - {i} - {titles.get(i, '(no title)')} [held]" for i in held]
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


def accepted_by_harness():
    """-> (rows, lines): what the harness accepted by itself (I-04) that the owner has not reviewed yet, with the undo
    line of each. Shown, never answered: an acceptance stands unless the owner runs its undo line."""
    try:
        from .lifecycle import autoaccept
        rs = [r for r in autoaccept.unseen() if r.get("accepted")]
    except Exception:                                        # noqa: BLE001 - the review never fails on the ledger
        return [], []
    if not rs:
        return [], []
    head = [f"Accepted by the harness while you were away ({len(rs)}): every check passed and an independent reviewer "
            "said yes, or (TIE-BREAK / OVERRIDE) a higher rung settled a check-vs-reviewer deadlock on that one item. "
            "Review after the fact; undo any with its line."]
    return rs, head + autoaccept.lines(rs)[1:]


def unavailable_models_lines():
    """P9: models the harness is not using now (out of budget or pinned by the owner), with the time they are back."""
    try:
        from .lifecycle import models
        ls = models.unavailable_lines()
    except Exception:                                        # noqa: BLE001 - the review never fails on this line
        return []
    return (["Models the harness is routing around (out of budget, or marked by you); they return by themselves:"]
            + [f"    {x}" for x in ls]) if ls else []


def counsel_lines():
    """P9: every stronger-model call the harness made (advisor, reviewer, tie-break, climb), from the counsel journal."""
    try:
        from .lifecycle import counsel
        return counsel.lines()
    except Exception:                                        # noqa: BLE001 - the review never fails on this line
        return []


def render(es, root=None):
    titles = plan_titles(root)
    out = [GOAL, FINAL, ""] + where_we_are(root)
    _, aa = accepted_by_harness()
    if aa:
        out += [""] + aa
    ua = unavailable_models_lines()
    if ua:
        out += [""] + ua
    cl = counsel_lines()
    if cl:
        out += [""] + cl
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
            "numbers (1,3) pick some, or both (1,3 a); words after ':' are your note (for example 2 a: yes, go ahead)."]
    return out


def inbox_page_path(root=None):
    return state_root(root or ROOT) / "state" / "INBOX.md"


def ledger_rows(limit=30):
    """-> the last `limit` rows of the agent ledger (items `fieldkit inbox add` routed away from the owner)."""
    import json
    rows = []
    f = _inbox().ledger_file()
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[-limit:]


def render_inbox(root=None):
    """The owner's readable inbox: what waits on the owner (why + exact command), then what agents handled alone."""
    its = _inbox().items("open")
    out = ["# Inbox", "", "Rewritten on every inbox change. Read-only: answer at a terminal with `fieldkit review`.", "",
           f"## Waiting on you ({len(its)})", ""]
    if not its:
        out.append("Nothing waits for you.")
    for it in its:
        out += [f"### [{it['n']}] {it['title']}", f"- Kind: {it['kind']}, added {it.get('added', '')}",
                f"- Why: {it.get('why', '')}"]
        if it.get("command"):
            out += ["- Command:", "", f"      {it['command']}", ""]
    _, aa = accepted_by_harness()
    if aa:
        out += ["", "## Accepted by the harness (after-the-fact review)", ""] + [f"    {x}" for x in aa]
    rows = ledger_rows()
    out += ["", f"## Handled by the agents, no action from you ({len(rows)} latest)", "",
            "Only a hard line reaches you; everything else is filed here for Zero and the next agent.", ""]
    if not rows:
        out.append("Nothing filed.")
    for r in reversed(rows):
        out.append(f"- {r.get('added', '')} [{r.get('kind', '')}] {r.get('title', '')}: {str(r.get('why', ''))[:200]}")
        if r.get("command"):
            out.append(f"  - command: {r['command']}")
    return out


def write_inbox_page(root=None):
    """Rewrite state/INBOX.md. -> the path, or None (never breaks the change that triggered it)."""
    try:
        p = inbox_page_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(NL.join(render_inbox(root)) + NL, encoding="utf-8", newline=NL)
        return p
    except Exception:  # noqa: BLE001
        return None


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
    write_inbox_page(root)
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
    seen, _ = accepted_by_harness()
    if seen:                                    # shown to the owner at their terminal: reviewed after the fact
        from .lifecycle import autoaccept
        autoaccept.mark_seen([r["id"] for r in seen])
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


def open_page(root=None, run=None, inbox=False):
    p = write_inbox_page(root) if inbox else write_page(root)
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
    ap.add_argument("--inbox", action="store_true", help="write state/INBOX.md (waiting items + agent ledger) and open it")
    ap.add_argument("--list", action="store_true", help="print the list, change nothing")
    a = ap.parse_args(argv)
    try:
        if a.inbox:
            print(f"opened {open_page(inbox=True)}")
        elif a.open:
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
