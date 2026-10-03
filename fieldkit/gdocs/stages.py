"""Pipeline stages for gorilla-documentation-ibm-style (fieldkit/build/pipelines/).

    fieldkit next gorilla-documentation-ibm-style       the one next thing to do
    fieldkit pipeline run gorilla-documentation-ibm-style --only fill

Each function takes the pipeline Context and returns {"ok", "detail"}; the run
functions do the work, the verify functions only look. Groups default to the
stale ones (a source file changed since the last render, or never rendered);
--var groups="exam office" names them instead. The fill stage is where a model
or a person comes in: when a .filled.json is missing or invalid it fails with
the exact files to write, so `fieldkit next` reports BLOCKED with that list.
"""
from . import groups as G
from . import workflow as W


def _names(ctx, groups=None):
    raw = groups if groups is not None else (ctx.vars.get("groups") if ctx else "")
    return [n for n in str(raw or "").replace(",", " ").split() if n]


def plan(ctx, groups=None):
    r = W.plan(_names(ctx, groups))
    cov = r["coverage"]
    counts = {}
    for g in r["groups"]:
        counts[g["state"]] = counts.get(g["state"], 0) + 1
    detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) + f"; next: {r['next']}"
    if not cov["ok"]:
        detail = (f"coverage FAILS: orphans {cov['orphans']}, in two groups {list(cov['duplicates'])}, "
                  f"empty {cov['empty_groups']}; fix docs/groups.yaml")
    return {"ok": cov["ok"], "detail": detail}


def coverage(ctx, groups=None):
    cov = G.coverage(G.load())
    return {"ok": cov["ok"], "detail": "every .py in exactly one group" if cov["ok"] else
            f"orphans {cov['orphans']}, duplicates {list(cov['duplicates'])}, empty {cov['empty_groups']}"}


def prep(ctx, groups=None):
    rows = W.prep(_names(ctx, groups))
    bad = [r for r in rows if r["status"] == "failed"]
    if bad:
        return {"ok": False, "detail": "; ".join(f"{r['group']}: {r['detail'][-200:]}" for r in bad)}
    return {"ok": True, "detail": "nothing stale" if not rows else
            ", ".join(f"{r['group']} {r['status']}" for r in rows)}


def prep_current(ctx, groups=None):
    names = _names(ctx, groups) or W.stale_names()
    gs = G.pick(G.load(), names)
    missing = [g["name"] for g in gs if not W._prep_current(g, G.hashes(g))]
    return {"ok": not missing, "detail": "prep files match sources and brief" if not missing else
            "prep missing or out of date: " + ", ".join(missing)}


def fill(ctx, groups=None):
    rows = W.fill_status(_names(ctx, groups))
    todo = [r for r in rows if not r["ok"]]
    if not todo:
        return {"ok": True, "detail": f"{len(rows)} filled file(s) present and valid"}
    files = "; ".join(f"{r['file']} ({r['problems'][0]})" for r in todo[:6])
    if len(todo) > 6:
        files += f"; and {len(todo) - 6} more (fieldkit docs fill lists them all)"
    return {"ok": False, "detail": (f"BLOCKED until a model or a person writes {len(todo)} file(s): {files}. "
                                    "Each is one JSON object matching 'json_schema' in the .prep.json beside it; "
                                    "follow fieldkit/gdocs/WRITER_BRIEF.md (also inside each prep.json). "
                                    "Check with: fieldkit docs fill")}


def render(ctx, groups=None):
    rows = W.render(_names(ctx, groups))
    bad = [r for r in rows if not r["ok"]]
    if bad:
        return {"ok": False, "detail": "; ".join(
            f"{r['group']}: {len(r['findings'])} finding(s), first: {r['findings'][0][:200]}" for r in bad)
            + ". Fix the .filled.json (never the .md), then: fieldkit docs render"}
    return {"ok": True, "detail": "nothing stale" if not rows else
            ", ".join(f"{r['group']} PASS {r['scores']}" for r in rows)}


def none_stale(ctx, groups=None):
    names = _names(ctx, groups)
    left = [n for n in W.stale_names() if not names or n in names]
    return {"ok": not left, "detail": "every group rendered from its current sources" if not left else
            "stale: " + ", ".join(left)}


def check(ctx, groups=None, strict=False):
    r = W.check(_names(ctx, groups), strict=bool(strict))
    bad = [g for g in r["groups"] if not g["ok"]]
    if r["ok"]:
        warn = sum(len(g["warnings"]) for g in r["groups"])
        return {"ok": True, "detail": f"{len(r['groups'])} group(s) pass" + (f" ({warn} stale warning(s))"
                                                                             if warn else "")}
    return {"ok": False, "detail": ("coverage FAILS; " if not r["coverage"]["ok"] else "") + "; ".join(
        f"{g['group']}: {g['findings'][0][:160]} (+{len(g['findings']) - 1} more)" for g in bad)}


def index(ctx, groups=None):
    r = W.index()
    return {"ok": True, "detail": f"wrote {r['written']}"}


def index_current(ctx, groups=None):
    ok = W.index_current()
    return {"ok": ok, "detail": "README.md is current" if ok else "README.md is out of date: fieldkit docs index"}
