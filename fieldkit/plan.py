# Version: 1.0.0 · updated 26-10-28-17-28
"""fieldkit plan next: where the harness plan stands, so no session forgets it (owner, 2026-10-28: "Just make sure
noone forgets stuff . It costs us a lot of money").

Reads docs/HARNESS-PLAN.md (the item tables and the Order table) and the merge commits on origin/main since that file
was added. An item id in the trailing parenthesis of a merged pull request's title marks the item done: "(R5)",
"(O2, C6)", "(R4 + O5)". Name an id there only when the pull request finishes the item. A merge counts only while the
item's status cell says PLANNED: a status written by hand (PARTIAL ..., WINDOWS DONE ...) wins over it. Merges older
than the plan are not read: they used O1-O4 for other stages.

    fieldkit plan next [--json] [--write]

--write sets each PLANNED status cell the merges finished to "DONE (PR #N)". Every run saves the one-line summary to
state/plan-next.json; `fieldkit zero`, both handovers and the room hook show that line (the hook reads only the file,
no git call per tool call).

The line also counts the lessons in docs/LESSONS.yaml with no tool yet (no `tool`, or a `test` file this checkout does
not have): a mistake written down but not made a check stays on every session's screen until it is one (I8).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
PLAN = "docs/HARNESS-PLAN.md"
LESSONS = "docs/LESSONS.yaml"
ID = re.compile(r"\b([RLOCIWP]\d+)\b")
ROW_ID = re.compile(r"^\|\s*([RLOCIP]\d+)\s*\|")
TAIL_PAREN = re.compile(r"\(([^()]*)\)\s*$")


def state_file(root=None):
    return Path(os.environ.get("FIELDKIT_PLAN_STATE") or (state_root(root or ROOT) / "state" / "plan-next.json"))


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def items(text):
    """-> {id: status cell} for every item row (first cell R1, O2, ...; status is the last cell)."""
    out = {}
    for line in text.splitlines():
        m = ROW_ID.match(line)
        if m:
            out[m.group(1)] = _cells(line)[-1]
    return out


def order(text):
    """-> [(phase, [ids])] from the "## Order" table, in its order."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith("## Order")
            continue
        if not inside or not line.startswith("|") or set(line) <= set("|- "):
            continue
        c = _cells(line)
        if len(c) >= 2 and c[0] != "phase":
            out.append((c[0], list(dict.fromkeys(ID.findall(c[1])))))
    return out


def _git(root, *args, run=subprocess.run):
    try:
        r = run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def merged(root, run=subprocess.run):
    """-> {id: "#N"} from the merge commits since the plan was added (origin/main, else HEAD). Offline: local refs."""
    added = _git(root, "log", "--diff-filter=A", "--format=%H", "--", PLAN, run=run).split()
    if not added:
        return {}
    tip = "origin/main" if _git(root, "rev-parse", "--verify", "-q", "origin/main", run=run).strip() else "HEAD"
    log = _git(root, "log", "--merges", "--reverse", "--format=%s%x1f%b%x1e", f"{added[-1]}..{tip}", run=run)
    out = {}
    for rec in log.split("\x1e"):
        subj, _, body = rec.strip().partition("\x1f")
        pr = re.search(r"Merge pull request (#\d+)", subj)
        title = (body.strip().splitlines() or [""])[0]
        m = TAIL_PAREN.search(title)
        if pr and m:
            for i in ID.findall(m.group(1)):
                out.setdefault(i, pr.group(1))
    return out


def lessons(root):
    """-> {"total", "untooled": [ids]} from docs/LESSONS.yaml; None when the file is missing or unreadable."""
    try:
        import yaml
        data = yaml.safe_load((Path(root) / LESSONS).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError):
        return None
    rows = [r for r in (data.get("lessons") or []) if isinstance(r, dict)]
    bad = [str(r.get("id") or r.get("what", "?")[:30]) for r in rows
           if not str(r.get("tool") or "").strip() or not r.get("test") or not (Path(root) / str(r["test"])).is_file()]
    return {"total": len(rows), "untooled": bad}


def lessons_note(ls):
    if ls is None:
        return f"; {LESSONS} missing"
    return f"; lessons with no tool yet: {len(ls['untooled'])} of {ls['total']}" if ls["untooled"] else ""


def is_done(status):
    return status.upper().startswith("DONE")


def next_(root=None, run=subprocess.run):
    """-> {"phase", "open", "done", "line", ...}: the first phase with an open item, and the plan's one line."""
    root = Path(root or ROOT)
    p = root / PLAN
    if not p.is_file():
        return {"ok": False, "line": f"PLAN: {PLAN} is missing here", "phases": []}
    text = p.read_text(encoding="utf-8")
    st, mg = items(text), merged(root, run=run)
    phases = []
    for name, ids in order(text):
        done = [i for i in ids if is_done(st.get(i, "")) or (i in mg and st.get(i, "PLANNED") == "PLANNED")]
        phases.append({"phase": name, "items": ids, "done": done, "open": [i for i in ids if i not in done]})
    cur = next((ph for ph in phases if ph["open"]), None)
    if cur is None:
        line = "PLAN: every phase of the Order table is done (docs/HARNESS-PLAN.md)"
    else:
        line = (f"PLAN: phase {cur['phase']}: open {' '.join(cur['open'])}"
                + (f" (done {' '.join(cur['done'])})" if cur["done"] else "")
                + " - docs/HARNESS-PLAN.md, fieldkit plan next")
    ls = lessons(root)
    line += lessons_note(ls)
    try:
        from . import ideas
        parked = ideas.parked_count(root)
    except Exception:  # noqa: BLE001
        parked = 0
    return {"ok": True, "lessons": ls, "parked_ideas": parked, "phase": cur and cur["phase"], "open": cur["open"] if cur else [],
            "done": cur["done"] if cur else [], "merged": mg, "statuses": st, "phases": phases, "line": line,
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def write_statuses(root, mg):
    """Set each PLANNED status cell whose item a merge finished to "DONE (PR #N)". -> the ids written."""
    p = Path(root) / PLAN
    lines, out = p.read_text(encoding="utf-8").split("\n"), []
    for k, line in enumerate(lines):
        m = ROW_ID.match(line)
        if m and m.group(1) in mg and line.rstrip().endswith("| PLANNED |"):
            lines[k] = line.rstrip()[:-len("PLANNED |")] + f"DONE (PR {mg[m.group(1)]}) |"
            out.append(m.group(1))
    if out:
        p.write_text("\n".join(lines), encoding="utf-8")
    return out


def save(r, root=None):
    f = state_file(root)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({k: r.get(k) for k in ("line", "phase", "open", "done", "lessons", "at")}, indent=1),
                     encoding="utf-8")
    except OSError:
        pass
    return f


def line(root=None, live=True):
    """The plan's one line: computed (live) and saved, or read from the saved file (the hook: no git). "" on none."""
    try:
        if live:
            r = next_(root)
            if r.get("ok"):
                save(r, root)
            return r["line"]
        return json.loads(state_file(root).read_text(encoding="utf-8")).get("line") or ""
    except Exception:  # noqa: BLE001 - the plan line never breaks the command that carries it
        return ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit plan", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["next"])
    ap.add_argument("--write", action="store_true", help="mark the items merges finished DONE in the plan file")
    ap.add_argument("--repo", default=str(ROOT), help="the Fieldkit checkout (default: this one)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = next_(a.repo)
    if not r["ok"]:
        print(r["line"])
        return 1
    r["written"] = write_statuses(a.repo, r["merged"]) if a.write else []
    save(r, a.repo)
    if a.json:
        print(json.dumps(r, indent=1))
        return 0
    print(r["line"])
    for ph in r["phases"]:
        mark = "done" if not ph["open"] else ("NOW " if ph["phase"] == r["phase"] else "open")
        print(f"  {mark}  {ph['phase']}: " + " ".join(i + ("*" if i in ph["done"] else "") for i in ph["items"]))
    if r.get("lessons") and r["lessons"]["untooled"]:
        print(f"  lessons with no tool yet ({LESSONS}): {' '.join(r['lessons']['untooled'])}")
    stale = [i for i in r["merged"] if r["statuses"].get(i) == "PLANNED" and i not in r["written"]]
    if r["written"]:
        print(f"written DONE: {' '.join(r['written'])} in {PLAN}")
    if r.get("parked_ideas"):
        print(f"  {r['parked_ideas']} parked ideas (not plan work): fieldkit ideas review")
    print("NEXT: " + (f"work on {r['open'][0]} (phase {r['phase']})" if r["open"] else "nothing open")
          + ("" if a.write or not stale else f"; fieldkit plan next --write marks merged items in {PLAN}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
