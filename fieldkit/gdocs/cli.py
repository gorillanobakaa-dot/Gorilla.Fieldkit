"""`fieldkit docs ...`: the command line of Gorilla.Documentation.IBM.Style.

    fieldkit docs plan [GROUP...]              groups, state (fresh/stale/never), files to fill, the next step
    fieldkit docs prep [GROUP...] [--force]    stage, dual_track prep, writer brief (default: stale groups)
    fieldkit docs fill [GROUP...]              which .filled.json files are missing or invalid (exit 3 if any)
    fieldkit docs render [GROUP...]            dual_track render + Gorilla checks (default: stale groups; exit 3)
    fieldkit docs check [GROUP...] [--strict]  coverage + Gorilla checks on committed docs (exit 3); --strict: stale fails
    fieldkit docs index                        write docs/dual-track/README.md

Registered in fieldkit/cli.py by `register(sub, common)`.
"""
import json


def register(sub, common):
    d = sub.add_parser("docs", parents=[common],
                       help="dual-track documentation (Gorilla.Documentation.IBM.Style): plan, prep, render, check")
    d.add_argument("action", choices=["plan", "prep", "fill", "render", "check", "index"])
    d.add_argument("groups", nargs="*", help="group names from docs/groups.yaml (default: see each action)")
    d.add_argument("--force", action="store_true", help="prep: re-prep even when the prep files are current")
    d.add_argument("--strict", action="store_true", help="check: a stale group is a failure (release gate)")
    d.set_defaults(fn=cmd_docs)
    return d


def _emit(data, as_json, human):
    if as_json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        human(data)


def cmd_docs(a):
    from . import groups as G
    from . import workflow as W
    try:
        if a.action == "plan":
            return _plan(W.plan(a.groups), a.json)
        if a.action == "prep":
            return _prep(W.prep(a.groups, force=a.force), a.json)
        if a.action == "fill":
            return _fill(W.fill_status(a.groups), a.json)
        if a.action == "render":
            return _render(W.render(a.groups), a.json)
        if a.action == "check":
            return _check(W.check(a.groups, strict=a.strict), a.json)
        r = W.index()
        _emit(r, a.json, lambda r: print(f"wrote {r['written']} ({r['lines']} lines)"))
        return 0
    except G.GroupError as e:
        print(f"fieldkit docs: {e}")
        return 2


def _plan(r, as_json):
    def human(r):
        print("steps:")
        for s in r["steps"]:
            print(f"  {s['id']:<7} {s['command']:<34} {s['what']}")
        print("\ngroups:")
        for g in r["groups"]:
            fl = " ".join(f"{t[0]}:{'P' if v['prep'] else '-'}{'F' if v['filled'] else '-'}{'M' if v['md'] else '-'}"
                          for t, v in g["tracks"].items())
            why = ""
            if g["state"] == "stale":
                why = "changed: " + ", ".join(g["changed"] + g["added"] + g["removed"])
            print(f"  {g['group']:<24} {g['state']:<6} {g['files']:>3} files  rendered {g['rendered'] or '-':<10}  "
                  f"{fl}  {why}")
        cov = r["coverage"]
        print("\ncoverage: " + ("every .py under fieldkit/ is in exactly one group" if cov["ok"] else
                                f"FAIL orphans={cov['orphans']} duplicates={cov['duplicates']} "
                                f"empty={cov['empty_groups']}"))
        print("(l/d = layman/developer; P prep, F filled, M rendered)")
        print(f"\nNEXT: {r['next']}")
    _emit(r, as_json, human)
    return 0 if r["coverage"]["ok"] else 3


def _prep(rows, as_json):
    def human(rows):
        if not rows:
            print("nothing stale: no group needs prep (name groups to prep them anyway)")
        for r in rows:
            print(f"{r['group']:<24} {r['status']:<9} {r['detail']}")
            if r["status"] != "failed":
                for f in r["fill"]:
                    print(f"    fill: {f}")
        if any(r["status"] != "failed" for r in rows):
            print("\nNEXT: write each fill file from the prep.json beside it, following "
                  "fieldkit/gdocs/WRITER_BRIEF.md (it is inside each prep.json too), then "
                  "`fieldkit docs render`.")
    _emit(rows, as_json, human)
    return 3 if any(r["status"] == "failed" for r in rows) else 0


def _fill(rows, as_json):
    def human(rows):
        if not rows:
            print("nothing stale: no file to fill")
        for r in rows:
            print(f"{'OK   ' if r['ok'] else 'WRITE'} {r['file']}")
            for p in r["problems"]:
                print(f"       - {p}")
        if any(not r["ok"] for r in rows):
            print("\nWrite each WRITE file: one JSON object matching 'json_schema' in the prep.json named "
                  "beside it, following fieldkit/gdocs/WRITER_BRIEF.md.")
    _emit(rows, as_json, human)
    return 0 if all(r["ok"] for r in rows) else 3


def _render(rows, as_json):
    def human(rows):
        if not rows:
            print("nothing stale: no group to render (name groups to render them anyway)")
        for r in rows:
            sc = " ".join(f"{t}={v}" for t, v in r["scores"].items())
            print(f"{'PASS' if r['ok'] else 'FAIL'} {r['group']:<24} {sc}")
            for f in r["findings"]:
                print(f"     - {f}")
        if any(not r["ok"] for r in rows):
            print("\nFix the .filled.json (never the rendered .md), then render again.")
    _emit(rows, as_json, human)
    return 0 if all(r["ok"] for r in rows) else 3


def _check(r, as_json):
    def human(r):
        cov = r["coverage"]
        print("coverage: " + ("OK" if cov["ok"] else
                              f"FAIL orphans={cov['orphans']} duplicates={cov['duplicates']} "
                              f"empty={cov['empty_groups']}"))
        for g in r["groups"]:
            print(f"{'PASS' if g['ok'] else 'FAIL'} {g['group']:<24} {g['state']}")
            for f in g["findings"]:
                print(f"     - {f}")
            for w in g["warnings"]:
                print(f"     ~ {w}")
        n = sum(1 for g in r["groups"] if g["ok"])
        print(f"\n{n}/{len(r['groups'])} groups pass" + ("" if cov["ok"] else "; coverage FAILS")
              + (" (strict: stale counts as failure)" if r["strict"] else ""))
    _emit(r, as_json, human)
    return 0 if r["ok"] else 3
