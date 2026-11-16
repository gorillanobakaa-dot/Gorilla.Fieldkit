"""fieldkit cookbook: the deterministic project load (P41a; design docs/COOKBOOK-VISION.md).

    cookbook load ID | --cwd PATH [--budget N] [--json]   a bounded brief: what the project is, where its cookbook and
                                                           recipes are, the current state, the next command. An unknown
                                                           cwd prints "no project", exit 0, loads nothing.
    cookbook list                                          every registered project
    cookbook show ID                                       the project record
    cookbook check                                         validate every projects/*/project.yaml (CI); exit 3 on a problem

Registry only: no file search, no filesystem walk (projects/<id>/project.yaml, see projects/README.md)."""
import argparse
import json
import subprocess
import sys

from . import registry

TOKEN_CHARS = 4  # a token is about 4 characters


def _emerge_line(recipe):
    try:
        r = subprocess.run([sys.executable, "-m", "fieldkit", "emerge", "status", recipe], capture_output=True,
                           text=True, timeout=20)
        d, _ = json.JSONDecoder().raw_decode(r.stdout)   # the JSON, then a NEXT: line
    except (OSError, ValueError, subprocess.SubprocessError):
        return f"{recipe}: no status here"
    st = d.get("stages") or []
    bad = [s.get("id") for s in st if s.get("status") not in ("pass", "skip", "skipped", "cached")]
    return (f"{recipe}: {'ok' if d.get('ok') else 'NOT ok'} ({d.get('mode')}, {d.get('started')}, {len(st)} stages"
            + (f", not passed: {', '.join(bad[:4])}" if bad else "") + ")")


def _plan_rows(proj):
    f = registry.ROOT / "docs" / "HARNESS-PLAN.md"
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    words = [w.lower() for w in proj.get("plan_words") or []]
    out = []
    for ln in lines:
        if ln.startswith("| P") and "OPEN" in ln.rsplit("|", 2)[-2] and any(w in ln.lower() for w in words):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            out.append(f"{cells[0]}: {cells[1][:110]}")
    return out


def build(proj, with_state=True):
    p = {"id": proj["id"], "name": proj["name"], "purpose": proj["purpose"],
         "platforms": proj.get("platforms"),
         "cookbook": [f"{c['path']} ({c['repo']}): {c.get('what', '')}" for c in proj.get("cookbook") or []],
         "recipes": proj.get("recipes"),
         "entry": [f"{e['cmd']} - {e.get('what', '')}" for e in proj.get("entry") or []],
         "tests": [f"{t['cmd']}  [{', '.join(t['paths'])}]" for t in proj.get("tests") or []]}
    dec = proj.get("decisions")
    if dec:
        base = registry.repo_path(proj, dec["repo"])
        p["decisions"] = f"{dec['path']} in repo {dec['repo']}" + (f" ({base})" if base else "")
    p["state"] = ([_emerge_line(r) for r in proj.get("recipes") or []] if with_state else [])
    p["plan"] = _plan_rows(proj) if with_state else []
    p["next"] = (proj.get("entry") or [{}])[0].get("cmd")
    return p


def render(p, budget):
    L = [f"PROJECT {p['id']}: {p['name']} - {p['purpose']}",
         "Platforms: " + ", ".join(p["platforms"] or []),
         "Cookbook (read these, search nothing):"] + [f"  {x}" for x in p["cookbook"]]
    L += ["Recipes: " + ", ".join(p["recipes"] or []), "Entry commands:"] + [f"  {x}" for x in p["entry"]]
    if p.get("decisions"):
        L.append("Owner decisions (settled, do not re-argue): " + p["decisions"])
    L += ["Tests: " + "; ".join(p["tests"])]
    if p["state"]:
        L += ["State:"] + [f"  {x}" for x in p["state"]]
    if p["plan"]:
        L += ["Open plan rows:"] + [f"  {x}" for x in p["plan"][:6]]
    L.append(f"NEXT: {p['next']}")
    text = "\n".join(L)
    cap = budget * TOKEN_CHARS
    if len(text) > cap:  # keep the head and the NEXT line, cut the middle
        tail = L[-1]
        text = text[: cap - len(tail) - 40].rstrip() + "\n  ... (cut to the token budget; `fieldkit cookbook show " + p["id"] + "`)\n" + tail
    return text


def main(argv=None, prog="fieldkit cookbook"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("id", nargs="?")
    ld.add_argument("--cwd")
    ld.add_argument("--budget", type=int, default=2000, help="tokens (default 2000)")
    ld.add_argument("--no-state", action="store_true", help="skip emerge status and plan rows (fast)")
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("id")
    sub.add_parser("check")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        rows = [{"id": d.get("id"), "name": d.get("name"), "recipes": d.get("recipes")} for d in registry.load_all()]
        print(json.dumps(rows, indent=1) if a.json else "\n".join(f"{r['id']:18} {r['name']}  recipes: {', '.join(r['recipes'] or [])}" for r in rows))
        print("NEXT: fieldkit cookbook load ID")
        return 0
    if a.cmd == "show":
        d = registry.get(a.id)
        if not d:
            print(f"no project {a.id}")
            return 1
        d = {k: v for k, v in d.items() if k != "_file"}
        print(json.dumps(d, indent=1) if a.json else __import__("yaml").safe_dump(d, sort_keys=False))
        return 0
    if a.cmd == "check":
        probs = registry.check()
        if a.json:
            print(json.dumps({"ok": not probs, "problems": probs}))
        else:
            print("\n".join(probs) if probs else f"OK: {len(registry.load_all())} project(s) valid")
            print("NEXT: " + ("fix the lines above" if probs else "fieldkit cookbook load ID"))
        return 3 if probs else 0
    proj = registry.get(a.id) if a.id else registry.by_cwd(a.cwd) if a.cwd else None
    if not a.id and not a.cwd:
        print("give an ID or --cwd PATH")
        return 2
    if not proj:
        print(json.dumps({"project": None}) if a.json else "no project")
        return 0 if a.cwd else 1
    p = build(proj, with_state=not a.no_state)
    print(json.dumps(p, indent=1) if a.json else render(p, a.budget))
    return 0
