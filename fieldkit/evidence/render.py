"""A checked research run as a report: what was claimed, on what, and what held - written by code, not by a model.

    fieldkit evidence render RECORD.jsonl|FINDINGS.md --out report.md|.docx|.pdf [--store DIR] [--run RUN]

Every claim keeps its lane, its tier or grade and its sources; every claim the check found a problem with is listed
first, as UNSUPPORTED, with the rule; one source behind several lanes is said once. With --store, each source shows
when it was fetched and the hash of what was read. Nothing is summarised, merged or reworded: the report is the
record, laid out.
"""
from . import record as recmod


def _scale(c):
    return c.get("grade") or c.get("tier") or "—"


def blocks(records, result, store=None, run=None):
    run_rec = next((r for r in records if r.get("type") == "run"), {})
    lanes = [r for r in records if r.get("type") == "lane"]
    claims = [r for r in records if r.get("type") == "claim"]
    by_claim, by_lane = {}, {}
    for f in result["findings"]:
        if " claim " in f["where"]:
            by_claim.setdefault(f["where"], []).append(f)
        else:
            by_lane.setdefault(f["where"], []).append(f)
    unsupported = [c for c in claims if f"{c['lane']} claim {c['n']}" in by_claim]
    c = result["counts"]
    b = [{"heading": f"Evidence report: {run_rec.get('question') or 'research run'}", "level": 1},
         {"paragraph": f"{c['lanes']} lane(s), {c['claims']} claim(s), {c['urls']} source(s); "
                       f"{c['claims'] - len(unsupported)} claim(s) hold, {len(unsupported)} unsupported. "
                       f"Discipline: {c['doctrine']}" + (f"; run {run_rec['call']}" if run_rec.get("call") else "")
                       + ". Checked by fieldkit evidence check"
                       + (", against the evidence store" if store else "") + "."}]
    if unsupported:
        b += [{"heading": "Unsupported claims", "level": 2},
              {"paragraph": "A claim here broke a rule the check holds every claim to. It is not a finding until "
                            "someone opens the source and confirms it."},
              {"table": [["Lane", "#", "Claim", "Why"]] + [
                  [u["lane"], str(u["n"]), u.get("claim", ""),
                   "; ".join(f"{f['rule']}: {f['message']}" for f in by_claim[f"{u['lane']} claim {u['n']}"])]
                  for u in unsupported]}]
    lane_problems = [(w, f) for w, fs in by_lane.items() for f in fs]
    if lane_problems:
        b += [{"heading": "Lanes that broke the output contract", "level": 2},
              {"bullets": [f"{w}: {f['message']}" for w, f in lane_problems]}]
    for ln in lanes:
        b.append({"heading": ln["lane"], "level": 2})
        if ln.get("uncovered"):
            b.append({"paragraph": "This lane produced nothing. Its ground is unexamined."})
            continue
        basis = ln.get("basis") or {}
        if basis.get("evidence"):
            b.append({"paragraph": f"Answer rests on: {basis['evidence']} ({basis.get('grade') or basis.get('tier') or '—'})"})
        rows = [["#", "Claim", "Tier/grade", "Sources", "Check"]]
        for cl in (x for x in claims if x.get("lane") == ln["lane"]):
            key = f"{cl['lane']} claim {cl['n']}"
            rows.append([str(cl["n"]), cl.get("claim", ""), _scale(cl), "\n".join(cl.get("urls") or []) or
                         cl.get("evidence", ""), "UNSUPPORTED: " + ", ".join(sorted({f["rule"] for f in by_claim[key]}))
                         if key in by_claim else "holds"])
        if len(rows) > 1:
            b.append({"table": rows})
        if ln.get("sources_tried"):
            b.append({"paragraph": "Sources tried:"})
            b.append({"bullets": ln["sources_tried"]})
    notes = [n for n in result["notes"] if n["rule"] in ("same-source", "one-origin", "lane-uncovered")]
    if notes:
        b += [{"heading": "Notes", "level": 2}, {"bullets": [f"{n['where']}: {n['message']}" for n in notes]}]
    urls = sorted({u for cl in claims for u in cl.get("urls") or []})
    if urls:
        b.append({"heading": "Sources", "level": 2})
        snaps = store.snapshots() if store else None
        rows = [["Source", "Fetched", "Status", "Content (sha256)"]] if store else None
        for u in urls:
            if store:
                s = store.latest(u, run, snaps)
                rows.append([u, s.get("fetched_at", "") if s else "not fetched", str(s.get("status", "")) if s else "",
                             s.get("sha256", "")[:16] if s else ""])
        b.append({"table": rows} if store else {"bullets": urls})
    return b


def to_markdown(bl):
    out = []
    for x in bl:
        if "heading" in x:
            out.append("#" * int(x.get("level", 1)) + " " + x["heading"])
        elif "paragraph" in x:
            out.append(x["paragraph"])
        elif "bullets" in x:
            out.append("\n".join(f"- {i}" for i in x["bullets"]))
        elif "table" in x:
            t = x["table"]
            cell = lambda v: str(v).replace("|", "\\|").replace("\n", "<br>")
            out.append("\n".join(["| " + " | ".join(cell(v) for v in t[0]) + " |",
                                  "|" + "---|" * len(t[0])] + ["| " + " | ".join(cell(v) for v in r) + " |"
                                                               for r in t[1:]]))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render(records, out, store=None, run=None, fetched=None):
    result = recmod.check(records, store=store, fetched=fetched, run=run)
    bl = blocks(records, result, store, run)
    out = str(out)
    if out.endswith(".md"):
        from pathlib import Path
        Path(out).write_text(to_markdown(bl), encoding="utf-8")
    else:
        from ..office import create as office_create
        kind = out.rsplit(".", 1)[-1].lower()
        if kind not in ("docx", "pdf"):
            raise ValueError(f"write .md, .docx or .pdf, not .{kind}")
        if kind == "pdf":       # the PDF writer takes headings, paragraphs and bullets: tables become bullets
            bl = [x if "table" not in x else {"bullets": [" | ".join(map(str, r)) for r in x["table"][1:]]} for x in bl]
        office_create.create({"type": kind, "blocks": bl}, out, force=True)
    return result
