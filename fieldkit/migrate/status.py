"""Where every intent of the ledger stands in one migration (one build-harness task).

Three independent sources, never the record alone:

    the task record     did the hunk land in S2 (mechanical apply, no port step) or need S3 (a port, hand or relocation
                        step), and is that step closed?
    the tree            the claims audit's hunk judge (claims.audit_patches: verify.score_hunk with its second opinions,
                        relocations, hand ports, supersession) on the ported tree, plus the intent's own tree checks
    the installed build the intent's build-layer checks (shipped prefs, files in the install folder, post-install rows),
                        run with the claims audit's evidence runner (claims.run_check)

Statuses (one per intent, the furthest it has got):
    OUT-OF-SCOPE        the group is disabled or the patch excluded by the patch policy
    OPEN                a port step for one of its hunks is still pending, failed, blocked or deferred
    NOT-IN-TREE         the tree does not carry it, and nothing explains why
    EXPLAINED           none of its hunks is in the tree, each for a recorded reason (superseded by a later patch,
                        obsolete upstream and closed so by the record, dropped by the maintainer)
    VERIFIED-IN-TREE    the tree carries it (and its tree checks pass); no build-layer check proves it in the build
    PROVEN-IN-BUILD     it is in the tree AND every build-layer check passed on the installed build
    BUILD-CONTRADICTED  it is in the tree, but a build-layer check FAILED on the installed build
    TREE-CHECK-FAILS    the hunk judge finds it, but its own behaviour check fails in the tree (a check to review)
"""
import os
import re
from pathlib import Path

from ..buildh import claims as cl
from ..buildh import decisions as dec
from . import ledger

STATUSES = ("OUT-OF-SCOPE", "OPEN", "NOT-IN-TREE", "TREE-CHECK-FAILS", "EXPLAINED", "VERIFIED-IN-TREE",
            "BUILD-CONTRADICTED", "PROVEN-IN-BUILD")
OPEN_STEP = ("pending", "failed", "blocked", "deferred")
MOVED = re.compile(r"moved upstream to (\S+?):")


def _hunks_by_patch(owner, intents):
    """{patch rel: {(key, n): hunk dict}} parsed once per patch file."""
    from ..buildh import firefox
    pset, _ = firefox._policy(owner)
    out = {}
    for e in intents:
        if e.get("kind") != "hunks" or e["patch"] in out:
            continue
        p = Path(pset) / e["patch"]
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        out[e["patch"]] = {(f["key"], h["n"]): h for f in ledger.parse(text) for h in f["hunks"]}
    return out


def build_context(t, owner, install_dir, journal=None):
    """The evidence runner's context (claims.run_check), shared by every intent so the install is read once."""
    return {"owner": str(owner), "workdir": str(t["workdir"]), "install": str(install_dir) if install_dir else None,
            "build_id": cl.installed_build_id(install_dir), "journal": journal, "sources": {}, "patches": {},
            "excluded": {}, "disabled": {}}


def tree_conditionals(workdir):
    """{pref name: True} for prefs whose LAST line in the ported all.js / firefox.js sits under #if: the shipped file is
    preprocessed, so the post-install prefs row (not a value check) judges them (21.PORT.FIXES.157 moved the sandbox
    level under #ifdef XP_LINUX, for example)."""
    from ..buildh import proof
    out = {}
    for src in ledger.PREF_FILES:
        p = Path(workdir) / src
        if p.is_file():
            for name, (_, _, cond) in proof.pref_lines(p.read_text(encoding="utf-8", errors="replace")).items():
                out[name] = out.get(name, False) or cond
    return out


def run_check(check, ctx, redirect=None, conditional=None):
    """-> (layer, verdict 'ok'|'FAIL'|'cannot run', evidence)."""
    kind, arg = next(iter(check.items()))
    lay = ledger.layer(check)
    if kind == "pref" and (conditional or {}).get(arg.get("name")):
        return lay, "cannot run", (f"{arg['name']}: its line in the ported tree is under #if; the shipped file is "
                                   "preprocessed, so the post-install prefs row judges it, not a value check")
    if redirect and kind in ("tree_contains", "tree_lacks") and arg.get("path") in redirect:
        arg = {**arg, "path": redirect[arg["path"]]}
    try:
        ok, what = cl.run_check(kind, arg, ctx)
    except (cl.Unreadable, dec.Unreadable) as e:
        return lay, "cannot run", str(e)[:200]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return lay, "cannot run", f"{type(e).__name__}: {e}"[:200]
    return lay, ("ok" if ok else "FAIL"), str(what)[:200]


def compute(t, owner, intents, install_dir, pa=None, journal=None, say=None):
    """-> {"rows": {intent id: row}, "counts": {...}, "by_group": {...}}. Read-only. `pa` is a claims.audit_patches
    result (it takes about a minute on a full tree; pass one in to reuse it)."""
    owner = Path(owner)
    steps = t.get("steps", [])
    if pa is None:
        say and say("  judging every hunk against the tree (claims.audit_patches) ...")
        pa = cl.audit_patches(owner, t["workdir"], steps)
    hunk_st = {(p["patch"], h["file"], h["n"]): h for p in pa["patches"] for h in p["hunks"]}
    new_st = {x["file"]: x for x in pa.get("new_files", [])}
    del_st = {x["file"]: x for x in pa.get("deleted", [])}
    idx = cl._step_index(steps)
    applied_groups = {(s.get("args") or {}).get("group") for s in steps if s["id"].startswith("apply-") and s.get("status") == "done"}
    parsed = _hunks_by_patch(owner, intents)
    ctx = build_context(t, owner, install_dir, journal=journal)
    cond = tree_conditionals(t["workdir"])
    rows = {}
    for e in intents:
        if e.get("retired"):
            continue
        row = {"group": e["group"], "status": None, "origin": None, "tree": None, "checks": []}
        rows[e["id"]] = row
        if e.get("in_scope") is False:
            row["status"], row["why"] = "OUT-OF-SCOPE", e.get("scope_note")
            continue
        redirect, judged = {}, []
        if e.get("kind") == "hunks":
            hp = parsed.get(e["patch"], {})
            open_steps, ported = [], False
            for ref in e["hunks"]:
                h = hp.get((ref.get("key", ref["file"]), ref["n"]))
                st = hunk_st.get((e["patch"], ref.get("key", ref["file"]), ref["n"]))
                if ref.get("key") == "/dev/null":          # a deletion: the judge has no file to read; the tree decides
                    gone = not (Path(t["workdir"]) / ref["file"]).exists()
                    st = {"status": "APPLIED" if gone else "NOT-APPLIED", "explained": False,
                          "detail": "the file is gone from the tree" if gone else "the patch deletes this file; it is still in the tree"}
                judged.append(st or {"status": "UNJUDGED", "explained": False, "detail": "not in the audit (group not in scope?)"})
                if st and st["status"] == "RELOCATED":
                    m = MOVED.search(st.get("detail", ""))
                    if m:
                        redirect[ref["file"]] = m.group(1)
                # a record step speaks for this hunk when it carries the same change, or names the same file (the task
                # may have been ported from a snapshot of the patch set whose hunk text differs from the public one)
                cands = [s for s in idx.get((e["patch"], ref["n"]), []) if h is None or cl._same_change(s, h)
                         or (s.get("args") or {}).get("file") == ref["file"]]
                if cands:
                    ported = True
                    open_steps += [s["id"] for s in cands if s.get("status") in OPEN_STEP]
            if e["group"] not in applied_groups:
                row["origin"] = "authored-in-this-port"        # 21/22: made in this migration and exported
            elif open_steps:
                row["origin"], row["open_steps"] = "open", open_steps[:5]
            else:
                row["origin"] = "ported" if ported else "applied"
        elif e.get("kind") == "new-files":
            paths = next((c["tree_present"] for c in ledger.checks_of(e) if "tree_present" in c), [])
            judged = [{"status": "APPLIED" if (new_st.get(p) or {}).get("fail") is False else (new_st.get(p) or {}).get("status", "UNJUDGED"),
                       "explained": False, "detail": p} for p in paths]
            row["origin"] = "applied"
        elif e.get("kind") == "deleted-files":
            paths = next((c["tree_absent"] for c in ledger.checks_of(e) if "tree_absent" in c), [])
            judged = [{"status": "APPLIED" if not (del_st.get(p) or {}).get("present") else "NOT-APPLIED", "explained": False,
                       "detail": p} for p in paths]
            row["origin"] = "applied"
        ok_h = [j for j in judged if j["status"] in cl.HUNK_OK]
        bad_h = [j for j in judged if j["status"] not in cl.HUNK_OK and not j.get("explained")]
        row["tree"] = {"verdict": "NOT-IN-TREE" if bad_h else ("EXPLAINED" if not ok_h else "IN-TREE"),
                       "hunks": [j["status"] for j in judged][:40]}
        if bad_h:
            row["tree"]["why"] = [f"{j['status']}: {str(j.get('detail', ''))[:160]}" for j in bad_h[:3]]
        for c in ledger.checks_of(e):
            lay, v, ev = run_check(c, ctx, redirect, cond)
            row["checks"].append([next(iter(c)), lay, v, ev])
        tree_fail = [c for c in row["checks"] if c[1] == "tree" and c[2] == "FAIL"]
        build = [c for c in row["checks"] if c[1] == "build"]
        if row["origin"] == "open":
            row["status"] = "OPEN"
        elif bad_h:
            row["status"] = "NOT-IN-TREE"
        elif not ok_h:
            row["status"] = "EXPLAINED"
        elif tree_fail:
            row["status"] = "TREE-CHECK-FAILS"
        elif any(c[2] == "FAIL" for c in build):
            row["status"] = "BUILD-CONTRADICTED"
        elif build and all(c[2] == "ok" for c in build):
            row["status"] = "PROVEN-IN-BUILD"
        else:
            row["status"] = "VERIFIED-IN-TREE"
    return {"rows": rows, **summarise(intents, rows)}


def summarise(intents, rows):
    by_group, counts = {}, {s: 0 for s in STATUSES}
    order = {}
    for i, e in enumerate(intents):
        order.setdefault(e["group"], i)
    for e in intents:
        r = rows.get(e["id"])
        if not r:
            continue
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        g = by_group.setdefault(e["group"], {"total": 0, "applied": 0, "ported": 0, "authored": 0, "open": 0,
                                             "in_tree": 0, "proven": 0, "contradicted": 0, "explained": 0,
                                             "not_in_tree": 0, "out_of_scope": 0})
        if r["status"] == "OUT-OF-SCOPE":
            g["out_of_scope"] += 1
            continue
        g["total"] += 1
        g[{"applied": "applied", "ported": "ported", "open": "open", "authored-in-this-port": "authored"}.get(r["origin"], "applied")] += 1
        if r["status"] in ("VERIFIED-IN-TREE", "PROVEN-IN-BUILD", "BUILD-CONTRADICTED"):
            g["in_tree"] += 1
        g["proven"] += r["status"] == "PROVEN-IN-BUILD"
        g["contradicted"] += r["status"] == "BUILD-CONTRADICTED"
        g["explained"] += r["status"] == "EXPLAINED"
        g["not_in_tree"] += r["status"] in ("NOT-IN-TREE", "TREE-CHECK-FAILS", "OPEN")
    groups = sorted(by_group, key=lambda g: ledger.plan_rank(g, order.get(g, 0)))
    return {"counts": counts, "by_group": {g: by_group[g] for g in groups}}


def stamp(intents, rows, release):
    """Write each intent's status for this release into its ledger entry (status: {release: status})."""
    for e in intents:
        r = rows.get(e["id"])
        if r:
            st = dict(e.get("status") or {}) if isinstance(e.get("status"), dict) else {}
            st[str(release)] = r["status"]
            e["status"] = st
    return intents


def normcase(p):
    return os.path.normcase(os.path.abspath(str(p)))
