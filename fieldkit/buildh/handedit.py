"""A person's edit that no patch asked for, recorded as a task step so the record stays honest.

Live run 16 (2026-10-02 00:06): Firefox 157 wired its new HWInference speech recognition into PContent.ipdl and
PHWInference.ipdl unconditionally, while the owner builds with --disable-webspeech; the IPDL compiler stopped. The
fix is an upstream-style gate (#ifdef MOZ_WEBSPEECH) across eight files that no hunk of the patch set covers: new
upstream code reaching into a component the owner switched off ("excision creep").

Such an edit is not a stray edit and not a hand port of a hunk: it is recorded with `record` as a step of its own,
kind model/hand, whose "hunk" IS the diff the person made (so the verifier, the final re-check and the gate judge it
the way they judge every other hand port), with the reason in the title and the submit note. The journal line
carries the files and the reason; the checkpoint carries the change.
"""
import subprocess
import time
from pathlib import Path

from . import firefox, task


def diff_hunks(workdir, rel):
    """The working tree's diff of `rel` against HEAD, parsed into hunks (firefox.parse_patch)."""
    r = subprocess.run(["git", "-C", str(workdir), "diff", "--no-color", "--", rel], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    parsed = firefox.parse_patch(r.stdout) if r.stdout.strip() else []
    return parsed[0]["hunks"] if parsed else []


def merge_moves(hunks):
    """Hunks of one file that together MOVE lines (removed in one, added back in another) become one hunk: judged
    apart, the removing half reads as "line should be gone" for a line that is rightly still there (2026-10-02: an
    override block moved above a two-line statement in PlacesSemanticHistoryManager.sys.mjs came back as a false
    completion). Unrelated hunks stay separate."""
    hunks = list(hunks)
    groups = []                                         # lists of hunk indexes that share a moved line
    for i, h in enumerate(hunks):
        rem = {l[1:].strip() for l in h["lines"] if l.startswith("-") and l[1:].strip()}
        add = {l[1:].strip() for l in h["lines"] if l.startswith("+") and l[1:].strip()}
        joined = None
        for g in groups:
            for j in g:
                o = hunks[j]
                orem = {l[1:].strip() for l in o["lines"] if l.startswith("-") and l[1:].strip()}
                oadd = {l[1:].strip() for l in o["lines"] if l.startswith("+") and l[1:].strip()}
                if (rem & oadd) or (add & orem):
                    joined = g
                    break
            if joined:
                break
        if joined:
            joined.append(i)
        else:
            groups.append([i])
    out = []
    for g in groups:
        if len(g) == 1:
            out.append(hunks[g[0]])
        else:
            out.append({"header": hunks[g[0]]["header"] + " (+%d moved-line hunk(s) merged)" % (len(g) - 1),
                        "lines": [l for j in g for l in hunks[j]["lines"]]})
    return out


def record(task_id, files, why, group="hand"):
    """Turn the working tree's edits of `files` into done hand-port steps (one per hunk), checkpoint them.
    -> [step ids]. Refuses when a file has no diff or when other files changed too (nothing is recorded blind)."""
    t = task.load(task_id)
    w = Path(t["workdir"])
    changed = set(task.changed_files(t))
    extra = sorted(changed - set(files))
    if extra:
        raise task.Refused(f"other files changed too, record them or revert them first: {extra[:5]}")
    missing = [f for f in files if f not in changed]
    if missing:
        raise task.Refused(f"no diff in: {missing[:5]}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    have = {s["id"] for s in t["steps"]}
    added = []
    at = next((i for i, s in enumerate(t["steps"]) if s["id"].startswith("final")), len(t["steps"]))
    for rel in files:
        for n, h in enumerate(merge_moves(diff_hunks(w, rel)), 1):
            sid = f"hand-{group}-{Path(rel).name}-{stamp}-h{n}"
            if sid in have:
                continue
            t["steps"].insert(at, {"id": sid, "kind": "model", "status": "done", "done_by": "hand", "hand_port": True,
                                   "hand_note": why, "attempts": 1, "max_attempts": 1,
                                   "title": f"hand edit of {rel} (no patch asked for it): {why}",
                                   "packet": "fieldkit.buildh.firefox:packet_port", "check": "fieldkit.buildh.firefox:check_port",
                                   "auto": "fieldkit.buildh.firefox:auto_port", "allowed": [rel],
                                   "args": {"patch": f"hand/{group}", "file": rel, "hunk": h}})
            at += 1
            added.append(sid)
    if not added:
        raise task.Refused("nothing to record")
    task.checkpoint(t, f"hand edit ({group}): {', '.join(Path(f).name for f in files)}")
    task.save(t)
    task.journal(t, "hand-edit", steps=added, files=list(files), why=[why])
    return added
