"""plan, prep, fill status, render, check and index: the steps of the documentation workflow.

Paths given to dual_track.py are relative to the Fieldkit folder, and it runs
there, so the prep files carry no home folder. Every function returns plain
data; the command line (gdocs/cli.py) and the pipeline stages (gdocs/stages.py)
only present it.
"""
import hashlib
import json
from pathlib import Path

from . import checks, dualtrack
from . import groups as G

BRIEF = Path(__file__).resolve().parent / "WRITER_BRIEF.md"
TRACKS = ("layman", "developer")
STEPS = [
    ("plan", "fieldkit docs plan", "see which groups are stale and what comes next"),
    ("prep", "fieldkit docs prep [GROUP...]", "stage the sources, run dual_track prep, add the writer brief"),
    ("fill", "(a model or a person)", "write each <group>_<track>.filled.json named by its prep.json, "
                                       "following fieldkit/gdocs/WRITER_BRIEF.md"),
    ("render", "fieldkit docs render [GROUP...]", "dual_track render, then the Gorilla checks"),
    ("check", "fieldkit docs check", "coverage and Gorilla checks on the committed docs"),
    ("index", "fieldkit docs index", "write docs/dual-track/README.md"),
]


def rel(p):
    """A path as shown to people and written into prep files: relative, forward slashes."""
    try:
        return Path(p).resolve().relative_to(G.ROOT).as_posix()
    except ValueError:
        return Path(p).as_posix()


def brief_text():
    return BRIEF.read_text(encoding="utf-8")


def brief_sha():
    return hashlib.sha256(BRIEF.read_bytes()).hexdigest()[:16]


def prep_path(group, track):
    return G.out_dir(group) / f"{group['name']}_{track}.prep.json"


def filled_path(group, track):
    return G.out_dir(group) / f"{group['name']}_{track}.filled.json"


def md_path(group, track):
    return G.out_dir(group) / f"{group['name']}_{track}.md"


# -- the evidence corpus for the number and address checks ------------------------------
_CORPUS = {}


def corpus(group):
    """(allowed numbers, text) a document may cite: MEASUREMENTS.md, every .py in fieldkit/ and
    tests/ (a group's docs describe what it calls and what its tests show), and the line counts
    dual_track.py prints into the prep (per file and for the group)."""
    key = group["name"]
    if key in _CORPUS:
        return _CORPUS[key]
    texts = []
    if G.MEASUREMENTS.is_file():
        texts.append(G.MEASUREMENTS.read_text(encoding="utf-8"))
    for base in (G.PKG, G.ROOT / "tests"):
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" not in p.parts:
                texts.append(p.read_text(encoding="utf-8", errors="replace"))
    counts = []
    for r in G.files_of(group):
        counts.append(len((G.PKG / r).read_text(encoding="utf-8", errors="replace").splitlines()))
    texts.append(" ".join(str(c) for c in counts + [sum(counts), len(counts)]))
    allowed = checks.allowed_numbers(texts)
    _CORPUS[key] = (allowed, "\n".join(texts))
    return _CORPUS[key]


# -- plan ----------------------------------------------------------------------------
def plan(names=None):
    gs = G.load()
    cov = G.coverage(gs)
    rows = []
    for g in G.pick(gs, names):
        st = G.status(g)
        files = {t: {"prep": prep_path(g, t).is_file(), "filled": filled_path(g, t).is_file(),
                     "filled_current": _filled_current(g, t), "md": md_path(g, t).is_file()} for t in TRACKS}
        rows.append({"group": g["name"], "title": g["title"], "files": len(G.files_of(g))} | st |
                    {"tracks": files})
    nxt = next_step(rows, cov)
    return {"coverage": cov, "groups": rows, "steps": [{"id": s, "command": c, "what": w} for s, c, w in STEPS],
            "next": nxt}


def next_step(rows, cov):
    if not cov["ok"]:
        return "fix docs/groups.yaml: " + "; ".join(
            ([f"orphans {', '.join(cov['orphans'])}"] if cov["orphans"] else []) +
            ([f"in two groups {', '.join(cov['duplicates'])}"] if cov["duplicates"] else []) +
            ([f"empty groups {', '.join(cov['empty_groups'])}"] if cov["empty_groups"] else []))
    todo = [r for r in rows if r["state"] != "fresh"]
    if not todo:
        return "nothing stale: fieldkit docs check"
    need_prep = [r["group"] for r in todo if not all(r["tracks"][t]["prep"] for t in TRACKS)]
    if need_prep:
        return "fieldkit docs prep " + " ".join(need_prep)
    need_fill = [r["group"] for r in todo if not all(r["tracks"][t]["filled_current"] for t in TRACKS)]
    if need_fill:
        return "fill the .filled.json files of " + ", ".join(need_fill) + " (fieldkit/gdocs/WRITER_BRIEF.md)"
    return "fieldkit docs render " + " ".join(r["group"] for r in todo)


def _filled_current(group, track):
    """The filled JSON exists and was written after its prep file (same sources and brief)."""
    pp, fp = prep_path(group, track), filled_path(group, track)
    return fp.is_file() and pp.is_file() and fp.stat().st_mtime >= pp.stat().st_mtime


def stale_names():
    return [g["name"] for g in G.load() if G.status(g)["state"] != "fresh"]


# -- prep ----------------------------------------------------------------------------
def _prep_current(group, hashes):
    for t in TRACKS:
        try:
            env = json.loads(prep_path(group, t).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        gor = env.get("gorilla") or {}
        if gor.get("sources") != hashes or gor.get("brief_sha256") != brief_sha():
            return False
    return True


def prep(names=None, force=False):
    """-> list of {group, status, prep_files, fill, detail}."""
    out = []
    gs = G.load()
    for g in G.pick(gs, names or stale_names()):
        hs = G.hashes(g)
        rec = {"group": g["name"], "prep_files": [rel(prep_path(g, t)) for t in TRACKS],
               "fill": [rel(filled_path(g, t)) for t in TRACKS]}
        if not force and _prep_current(g, hs):
            out.append(rec | {"status": "current", "detail": "prep files already match the sources and brief"})
            continue
        staged = G.stage(g)
        od = G.out_dir(g)
        od.mkdir(parents=True, exist_ok=True)
        args = ["code", "prep", rel(staged), "--format", "both", "--output-dir", rel(od)]
        if G.MEASUREMENTS.is_file():
            args += ["--context", rel(G.MEASUREMENTS)]
        code, log = dualtrack.run(*args, cwd=G.ROOT)
        if code != 0:
            out.append(rec | {"status": "failed", "detail": log.strip()[-600:]})
            continue
        for t in TRACKS:
            rewrite_prep(g, t, hs)
        out.append(rec | {"status": "prepared", "detail": f"{len(hs)} file(s) staged in {rel(staged)}"})
    return out


def rewrite_prep(group, track, hashes):
    """Fix then_run (dual_track writes `--validate`, which render does not accept) and give
    every writer the same rules: the Gorilla writer brief, appended to the instructions."""
    p = prep_path(group, track)
    env = json.loads(p.read_text(encoding="utf-8"))
    name = group["name"]
    env["write_completion_to"] = rel(G.ROOT / Path(env["write_completion_to"]))
    env["then_run"] = f"fieldkit docs render {name}"
    env["then_run_without_fieldkit"] = (f"python dual_track.py code render {rel(G.STAGING / name)} --output-dir "
                                        f"{rel(G.out_dir(group))}   (validation is on by default; there is no "
                                        f"--validate flag)")
    env["after_publish"] = (f"Commit the rendered .md files, PRECHECK.md and STATE.json only; the repository "
                            f".gitignore keeps *.prep.json, *.filled.json, the renderer's *_layman.json/"
                            f"*_developer.json and PRECHECK.json out of git. Optional, after the push: python "
                            f"dual_track.py cleanup {rel(G.out_dir(group))} retires those working files.")
    base = env["instructions"].split("\n\nGORILLA WRITER BRIEF", 1)[0]
    env["instructions"] = (base + "\n\nGORILLA WRITER BRIEF - these rules are part of the task and are checked "
                           f"by `fieldkit docs render` (exit 3 on any finding). This file is the {track.upper()} "
                           f"track of the group '{name}' ({group['title']}).\n\n" + brief_text())
    pointer = ("\n\nALSO: follow the GORILLA WRITER BRIEF in 'instructions'. Where it is stricter than the "
               "rules above, the brief wins.")
    if pointer not in env["system_prompt"]:
        env["system_prompt"] += pointer
    env["gorilla"] = {"group": name, "track": track, "sources": hashes, "brief_sha256": brief_sha(),
                      "prepared": G.today()}
    p.write_text(json.dumps(env, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# -- fill ----------------------------------------------------------------------------
def schema_errors(data, schema, where="$"):
    """A small JSON Schema check (type, properties, required, items, enum): stdlib only."""
    errs = []
    types = schema.get("type")
    if types:
        tl = types if isinstance(types, list) else [types]
        ok = any(_is(data, t) for t in tl)
        if not ok:
            return [f"{where}: expected {'/'.join(tl)}, got {type(data).__name__}"]
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{where}: {data!r} is not one of {schema['enum']}")
    if isinstance(data, dict):
        for k in schema.get("required", []):
            if k not in data:
                errs.append(f"{where}: missing required '{k}'")
        for k, sub in (schema.get("properties") or {}).items():
            if k in data:
                errs += schema_errors(data[k], sub, f"{where}.{k}")
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            errs += schema_errors(item, schema["items"], f"{where}[{i}]")
    return errs


def _is(v, t):
    return {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str),
            "boolean": isinstance(v, bool), "null": v is None,
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool)}.get(t, True)


def fill_status(names=None):
    """-> list of {group, track, file, ok, problems}. Groups default to the stale ones."""
    dt = dualtrack.module()
    out = []
    for g in G.pick(G.load(), names or stale_names()):
        for t in TRACKS:
            pp, fp = prep_path(g, t), filled_path(g, t)
            rec = {"group": g["name"], "track": t, "file": rel(fp), "prep": rel(pp), "problems": []}
            if not pp.is_file():
                rec["problems"].append(f"no prep file: run `fieldkit docs prep {g['name']}`")
            elif not fp.is_file():
                rec["problems"].append("not written yet")
            else:
                env = json.loads(pp.read_text(encoding="utf-8"))
                if fp.stat().st_mtime < pp.stat().st_mtime:
                    rec["problems"].append("older than its prep file (the sources or the brief changed): rewrite it")
                try:
                    data = dt._extract_json(fp.read_text(encoding="utf-8"))
                except ValueError as e:
                    rec["problems"].append(f"not valid JSON: {e}")
                    data = None
                if data is not None:
                    rec["problems"] += schema_errors(data, env.get("json_schema") or {})
                    req = dt._CODE_VALIDATION.get(t, [])
                    for name, (ok, detail) in dt.validate_json(data, req).items():
                        if not ok:
                            rec["problems"].append(f"{name}: {detail}")
            rec["ok"] = not rec["problems"]
            out.append(rec)
    return out


# -- render ----------------------------------------------------------------------------
def score(group, track):
    """dual_track's own quality score of the rendered track (needs the renderer's .json)."""
    dt = dualtrack.module()
    try:
        data = json.loads((G.out_dir(group) / f"{group['name']}_{track}.json").read_text(encoding="utf-8"))
        md = md_path(group, track).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    total, _ = dt.score_document(md, data, dt._CODE_VALIDATION.get(track, []), track)
    return total


def check_group(group, parser=None, terms=None):
    """-> {group, findings, metrics} for the committed .md files of one group."""
    allowed, text = corpus(group)
    findings, metrics = [], {}
    for t in TRACKS:
        p = md_path(group, t)
        if not p.is_file():
            findings.append(f"{t}: {rel(p)} does not exist (not rendered)")
            continue
        md = p.read_text(encoding="utf-8")
        fn = checks.check_layman if t == "layman" else checks.check_developer
        f, m = fn(md, allowed, text, parser, terms)
        findings += f
        metrics[t] = m
    return {"group": group["name"], "findings": findings, "metrics": metrics}


def render(names=None):
    """-> list of {group, ok, findings, scores}. Default: every group that is not fresh.

    Fail closed: a failed render never records fresh hashes, so the group stays stale. dual_track
    still writes the failing .md (so you can read what to fix); git shows that change."""
    from .. import cli
    parser = cli.build_parser()
    out = []
    for g in G.pick(G.load(), names or stale_names()):
        rec = {"group": g["name"], "findings": [], "scores": {}}
        missing = [rel(filled_path(g, t)) for t in TRACKS if not filled_path(g, t).is_file()]
        if missing:
            rec["findings"].append("not filled yet: " + ", ".join(missing))
            out.append(rec | {"ok": False})
            continue
        hs = G.hashes(g)
        # Answers written for older sources must not be stamped fresh against the new ones.
        if not _prep_current(g, hs):
            rec["findings"].append(f"the prep files do not match the current sources or brief: run "
                                   f"`fieldkit docs prep {g['name']}`, then refill")
        old = [rel(filled_path(g, t)) for t in TRACKS if not _filled_current(g, t)]
        if old:
            rec["findings"].append("written before their prep file (for older sources or brief): " + ", ".join(old)
                                   + "; check them against the prep file and save them again")
        if rec["findings"]:
            out.append(rec | {"ok": False})
            continue
        staged = G.stage(g)
        code, log = dualtrack.run("code", "render", rel(staged), "--output-dir", rel(G.out_dir(g)), cwd=G.ROOT)
        if code != 0:
            bad = [ln.strip() for ln in log.splitlines() if ln.strip().startswith(("✗", "→", "Error", "VALIDATION",
                                                                                     "QUALITY SCORE"))]
            rec["findings"].append(f"dual_track render exited {code}: " + " | ".join(bad[-12:] or [log.strip()[-400:]]))
        for t in TRACKS:
            rec["scores"][t] = score(g, t)
        chk = check_group(g, parser)
        rec["findings"] += chk["findings"]
        rec["ok"] = not rec["findings"]
        st = G.read_state(g)
        if rec["ok"]:
            st = {"group": g["name"], "title": g["title"], "sources": hs, "rendered": G.today(),
                  "scores": rec["scores"], "brief_sha256": brief_sha(), "gorilla_check": "PASS",
                  "metrics": _slim(chk["metrics"])}
        else:
            st["last_failed_render"] = {"date": G.today(), "findings": len(rec["findings"])}
        G.write_state(g, st)
        out.append(rec)
    return out


def _slim(metrics):
    return {t: {k: v for k, v in m.items() if k.startswith("words_total") or k in (
        "concept_rows", "trust_steps", "usage_steps", "commands", "glossary_terms")} for t, m in metrics.items()}


# -- check ---------------------------------------------------------------------------
def check(names=None, strict=False):
    """Coverage + Gorilla checks on the committed docs, nothing rendered. -> {ok, coverage, groups}."""
    from .. import cli
    parser = cli.build_parser()
    gs = G.load()
    cov = G.coverage(gs)
    rows = []
    for g in G.pick(gs, names):
        r = check_group(g, parser)
        st = G.status(g)
        r["state"] = st["state"]
        r["warnings"] = []
        if st["state"] != "fresh":
            why = (f"stale: {', '.join(st['changed'] + st['added'] + st['removed'])} changed since the last render"
                   if st["state"] == "stale" else "no STATE.json: never rendered by fieldkit docs")
            (r["findings"] if strict else r["warnings"]).append(why)
        r["ok"] = not r["findings"]
        rows.append(r)
    ok = cov["ok"] and all(r["ok"] for r in rows)
    return {"ok": ok, "coverage": cov, "groups": rows, "strict": strict}


# -- index ---------------------------------------------------------------------------
INDEX = G.OUT / "README.md"


def index_text():
    rep = check()
    by = {r["group"]: r for r in rep["groups"]}
    L = ["# Fieldkit documentation, two tracks per group", "",
         "Every group of `fieldkit/` modules is documented twice by the workflow "
         "Gorilla.Documentation.IBM.Style (`fieldkit docs`): a **layman** track for someone who has never "
         "opened a terminal, and a **developer** track for someone who will audit or change the code. "
         "Neither is a summary of the other.", "",
         "This page is written by `fieldkit docs index`; do not edit it by hand. The quality score is "
         "dual_track.py's own score out of 100 (structure and evidence, not prose quality). The Gorilla "
         "check is `fieldkit docs check`: PASS means every section, step, comparison, command, number and "
         "privacy rule held. Stale means a source file changed after the last render.", "",
         "| Group | What it covers | Layman | Developer | Score (layman / developer) | Gorilla check | "
         "Last render | State |",
         "|---|---|---|---|---|---|---|---|"]
    for g in G.load():
        st = G.read_state(g)
        sc = st.get("scores") or {}
        s = f"{sc.get('layman', '-')} / {sc.get('developer', '-')}" if sc else "-"
        r = by[g["name"]]
        links = []
        for t in TRACKS:
            p = md_path(g, t)
            links.append(f"[{t}]({g['name']}/{p.name})" if p.is_file() else "not rendered")
        gc = "PASS" if r["ok"] else f"FAIL ({len(r['findings'])})"
        state = {"fresh": "fresh", "stale": "**stale**", "never": "**never rendered**"}[r["state"]]
        L.append(f"| `{g['name']}` | {g['title']} | {links[0]} | {links[1]} | {s} | {gc} | "
                 f"{st.get('rendered') or '-'} | {state} |")
    cov = rep["coverage"]
    L += ["", "Coverage: " + ("every `.py` file under `fieldkit/` belongs to exactly one group."
                             if cov["ok"] else "INCOMPLETE: orphans " + ", ".join(cov["orphans"])),
          "", "Groups are defined in [`docs/groups.yaml`](../groups.yaml). Numbers in these documents come "
          "from [`MEASUREMENTS.md`](MEASUREMENTS.md) or from the source; anything else is written as "
          "\"not measured\".", ""]
    return "\n".join(L)


def index():
    text = index_text()
    INDEX.write_text(text, encoding="utf-8", newline="\n")
    return {"written": rel(INDEX), "lines": len(text.splitlines())}


def index_current():
    try:
        return INDEX.read_text(encoding="utf-8") == index_text()
    except OSError:
        return False
