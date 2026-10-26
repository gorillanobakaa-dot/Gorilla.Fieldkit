"""fieldkit emerge: compile jobs the way Gentoo's Portage does them, and better. A recipe written once, a plain runner,
no model in the loop unless a stage fails, no owner clicks.

    fieldkit emerge list                                    the recipes (fieldkit/emerge/recipes/*.yaml)
    fieldkit emerge plan RECIPE [--mode M] [--var k=v] [--json]
                                                            the dry run: every stage's key and what it would do
                                                            (SKIP cached / RUN / OFFLOAD / TIER-SKIP / NOT-HERE),
                                                            the ETA, the preflight; nothing runs
    fieldkit emerge run RECIPE [--mode iterate|install|release] [--only S ...] [--var k=v] [--no-fix] [--local]
                               [--no-guard] [--json]        the run: preflight once, then each stage; exit 0 green,
                                                            3 failed (in the inbox), 2 refused before stage 1
    fieldkit emerge status RECIPE                            the last report and overhead
    fieldkit emerge manifest RECIPE [--write]               sha256 of the file sources (Portage's Manifest), written
                                                            beside the recipe as RECIPE.manifest.json
    fieldkit emerge export RECIPE [--out F]                  every recorded key and its outputs' sha256
    fieldkit emerge repro RECIPE --with F                    the same keys built elsewhere: same bytes or not
    fieldkit emerge forget RECIPE [--stage S]                drop cache records (the outputs themselves stay)

Modes are the test tiers the owner approved in the test cost audit (2026-10-26): `iterate` builds; `install` adds
the package checks and post-install, once, on the installed build; `release` adds the leak gate, once per release, on
the final build. Owner, 2026-10-26: "Be better than Portage. Let's implement that". The baseline is the pre-AI hand
script: a Firefox compile + package in about 90 minutes, unattended.
"""
import argparse
import json
import sys
from pathlib import Path

from .recipe import MODES, Recipe, RecipeError, available, sha256_file
from .runner import Emerge, compare, export


def _vars(pairs):
    out = {}
    for p in pairs or []:
        k, sep, v = p.partition("=")
        if not sep:
            raise SystemExit(f"--var needs k=v, got {p!r}")
        out[k] = v
    return out


def plan_lines(pl):
    out = [f"emerge plan {pl['recipe']} ({pl['mode']}) on {pl['host']}"
           + (f"; docs-only change ({pl['docs_why']})" if pl["docs_only"] else "")]
    n = len(pl["stages"])
    for i, r in enumerate(pl["stages"], 1):
        eta = f"  eta {r['eta_s'] / 60:.1f} min" if r["eta_s"] else ""
        out.append(f"[{i}/{n}] {r['id']:<16} {r['action'].upper():<9} {r['key'][:12]}  {r['why']}{eta}")
    for name, st, line in pl["preflight"]:
        out.append(f"  preflight {name:<12} {st.upper():<4} {line}")
    todo = sum(r["action"] in ("run", "offload") for r in pl["stages"])
    out.append(f"PLAN {todo} of {n} stage(s) to build, ETA {pl['eta_s'] / 60:.1f} min"
               + (f" + {pl['eta_unknown']} never measured" if pl["eta_unknown"] else "")
               + f"; preflight {pl['preflight_status'].upper()}; keys in {pl['hash_s']} s"
               + (f"; pre-AI baseline {pl['baseline_minutes']} min" if pl.get("baseline_minutes") else ""))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    ap = argparse.ArgumentParser(prog="fieldkit emerge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "plan", "run", "status", "manifest", "export", "repro", "forget"])
    ap.add_argument("recipe", nargs="?")
    ap.add_argument("--mode", default="iterate", choices=sorted(MODES))
    ap.add_argument("--var", action="append", default=[], help="k=v: a recipe/pipeline variable (repeat)")
    ap.add_argument("--only", action="append", default=[], help="run: only this stage (repeat)")
    ap.add_argument("--no-fix", action="store_true", help="run: a failure goes to the inbox without a fix run")
    ap.add_argument("--local", action="store_true", help="run: never offload; every stage here")
    ap.add_argument("--no-guard", action="store_true", help="run: without the run guard (it runs inside one)")
    ap.add_argument("--write", action="store_true", help="manifest: write RECIPE.manifest.json")
    ap.add_argument("--out", help="export: the file")
    ap.add_argument("--with", dest="other", help="repro: another machine's export")
    ap.add_argument("--stage", help="forget: only this stage")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "list":
        for n in available():
            r = Recipe.load(n, strict=False)
            print(f"  {n:<22} {len(r.stages):>2} stage(s)  {r.description.strip().splitlines()[0][:90] if r.description else ''}")
        return 0
    if not a.recipe:
        print(f"fieldkit emerge {a.action}: name a recipe ({', '.join(available())})")
        return 2
    try:
        r = Recipe.load(a.recipe, overrides=_vars(a.var), strict=a.action == "run")
    except (RecipeError, ValueError) as e:
        print(f"fieldkit emerge: {e}")
        return 2
    if a.action == "plan":
        pl = Emerge(r, mode=a.mode, local_only=a.local).plan()
        print(json.dumps(pl, indent=1, default=str) if a.json else "\n".join(plan_lines(pl)))
        return 2 if pl["preflight_status"] == "stop" else 0
    if a.action == "run":
        e = Emerge(r, mode=a.mode, allow_fix=not a.no_fix, local_only=a.local, guard=not a.no_guard, hold=True,
                   say=lambda m: print(m, flush=True))
        rep = e.run(only=a.only or None)
        if a.json:
            print(json.dumps(rep, indent=1, default=str))
        return 0 if rep["ok"] else (2 if rep.get("refused") else 3)
    if a.action == "status":
        for name in ("last-report.json", "overhead.json"):
            p = r.state_dir / name
            print(p.read_text(encoding="utf-8") if p.is_file() else f"{name}: none yet")
        return 0
    if a.action == "manifest":
        man, bad = {}, 0
        for s in r.sources:
            if s.get("path"):
                p = Path(s["path"])
                if p.is_file():
                    man[s["id"]] = {"file": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size}
                    print(f"  {s['id']:<14} {man[s['id']]['sha256']}  {p.name}")
                else:
                    bad += 1
                    print(f"  {s['id']:<14} MISSING {p}")
            else:
                st, v, why = r.pin(s)
                print(f"  {s['id']:<14} {st.upper():<8} {why}")
        if a.write and man:
            old = r.manifest()
            changed = {k for k in man if k in old and old[k].get("sha256") != man[k]["sha256"]}
            if changed:
                print(f"REFUSED: {sorted(changed)} would change a recorded sha256: a pin moves only by a reviewed "
                      "commit that edits the manifest, never by this command")
                return 3
            r.manifest_path.write_text(json.dumps(dict(old, **man), indent=1) + "\n", encoding="utf-8")
            print(f"written {r.manifest_path}")
        return 3 if bad else 0
    if a.action == "export":
        ex = export(r)
        text = json.dumps(ex, indent=1)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            print(f"{len(ex['records'])} record(s) -> {a.out}")
        else:
            print(text)
        return 0
    if a.action == "repro":
        if not a.other:
            print("repro needs --with FILE (fieldkit emerge export on the other machine)")
            return 2
        res = compare(export(r), json.loads(Path(a.other).read_text(encoding="utf-8")))
        for d in res["different"]:
            print(f"  DIFFERENT {d['stage']}/{d['name']} key {d['key'][:12]}: here {d['here']} there {d['there']}")
        print(f"REPRO {len(res['same'])} same, {len(res['different'])} different, {len(res['only_here'])} only here, "
              f"{len(res['only_there'])} only there")
        return 3 if res["different"] else 0
    if a.action == "forget":
        from .runner import Store
        print(f"{Store(r.state_dir / 'cache').forget(a.stage)} record(s) dropped")
        return 0
    return 2
