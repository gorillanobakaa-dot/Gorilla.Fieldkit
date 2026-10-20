# Version: 1.0.0 · updated 26-10-18
"""The fast loop as a gate: a chrome change (CSS, JS, Fluent, markup, SVG) is tried on the LAST build before the next
one is compiled.

Born 2026-10-18. Build 2 of 158 was compiled with a theme block that had never run in a browser; build-verify then
found the menus still in the system's colours, the hover not the highlight and a label cut short, and the build was
spent. Gorilla builds WITHOUT omni.ja on purpose (the objdir's dist/bin keeps every chrome file as a plain file), so
that exactly this can be tried in a minute without a build (NEXT-RELEASE.md step 2.3). It was a step a model had to
remember; here the gate does it:

  1. the tree's changes since the commit the last build was made from (the build record's head);
  2. each changed file whose OLD version is a file of dist/bin, byte for byte, is replaced by its NEW version in a
     throwaway COPY of dist/bin (probe.tree_since; a preprocessed or generated file matches nothing and is listed);
  3. probe ui-contrast on that copy, judged by the same rows as build-verify (uicheck.judge, themecheck.judge_popups).

A failing row fails the gate, before an hour of compiling. No earlier build or no chrome change: nothing to try, said so.
"""
import json
import subprocess
import tempfile
from pathlib import Path

CHROME = (".css", ".mjs", ".js", ".ftl", ".xhtml", ".html", ".svg", ".json")
PREFIX = "fast loop (the change tried on the last build's unpacked dist/bin, before compiling): "


def changed(workdir, since, until="HEAD"):
    out = subprocess.run(["git", "-C", str(workdir), "diff", "--name-only", "--no-renames", since, until],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
    return [p for p in out if p.lower().endswith(CHROME)]


def rows(task_id, t, say=print, run=None, tree_since=None, record=None):
    """-> gate rows [{check, ok, evidence}]."""
    from . import compile as cg, probe, themecheck, uicheck
    rec = record
    if rec is None and task_id:
        rp = cg._record_path(task_id)
        rec = json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else None
    dist = Path(rec["objdir"]) / "dist" / "bin" if rec and rec.get("objdir") else None
    if not rec or not dist or not dist.is_dir() or (dist / "omni.ja").is_file():
        return [{"check": PREFIX + "ran", "ok": True,
                 "evidence": "no earlier unpacked build to try the change on (first build of this tree)"}]
    files = changed(t["workdir"], rec["head"])
    if not files:
        return [{"check": PREFIX + "ran", "ok": True, "evidence": f"no chrome change since the last build ({rec['head'][:10]})"}]
    got, skipped = (tree_since or probe.tree_since)(t["workdir"], rec["head"], dist, tempfile.mkdtemp(prefix="gfastloop_"))
    repl = {k[1:]: str(v) for k, v in got.items() if k.startswith("@")}
    out = [{"check": PREFIX + "the changed files reached the copy", "ok": bool(repl) or not files,
            "evidence": f"{len(repl)} file(s) replaced of {len(files)} changed; not tried: "
                        + ("; ".join(f"{p} ({w})" for p, w in skipped[:5]) or "none")}]
    if not repl:
        out[0]["ok"] = True          # nothing packaged as-is changed (generated files): the build itself must show it
        return out
    say(f"  fast loop: {len(repl)} changed file(s) tried on a copy of {dist} (probe ui-contrast) ...")
    r = (run or probe.run)(str(dist), "ui-contrast", wait=20, timeout=420, say=lambda m: None, files=repl)
    if not r.get("done"):
        return out + [{"check": PREFIX + "the ui-contrast probe ran", "ok": False,
                       "evidence": "the probe did not finish: " + "; ".join(r.get("lines", [])[-3:])[:200]}]
    res = uicheck.parse(r["lines"])
    judged = uicheck.judge(res) + themecheck.judge_popups(res["popups"], themecheck.load())
    return out + [{"check": PREFIX + j["check"], "ok": j["ok"], "evidence": j["evidence"]} for j in judged]
