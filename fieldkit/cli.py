"""fieldkit - one command line for every harness. Add --json for machine output.

    fieldkit host                                  what machine is this
    fieldkit tools list [--harness H] [--all]      registered tools (+ every toolbox script)
    fieldkit tools check [--run-tests] [--id ...]  exists? safe to probe? tested?

    fieldkit office read FILE [--engine builtin|markitdown|docling] [--out F]
    fieldkit office create SPEC.(json|yaml) OUT [--force]
    fieldkit office check FILE...
    fieldkit office scrub FILE... [--check] [--term WORD...] [--no-backup]
    fieldkit office deliver FILE...              check, scrub names, recheck, privacy scan: safe to send?

    fieldkit pipeline list
    fieldkit pipeline plan NAME [--var k=v ...]
    fieldkit pipeline run  NAME [--only S ...] [--from S] [--dry-run] [--force] [--var k=v]
    fieldkit pipeline status NAME
    fieldkit pipeline reset NAME [--stage S]

    fieldkit triage LOG [--set auto|firefox-windows|debian-kernel|debian-packaging]
    fieldkit privacy scan PATH... [--git] [--allow-paths] [--allow-emails]
    fieldkit gather [--only NAME...] [--check|--test] [--offline]   bring tools in per imports.yaml
    fieldkit harvest [--root DIR] | --find WORDS...   index every script; search it
    fieldkit next PIPELINE                       the one next thing to do: DO / BLOCKED / CANNOT HERE / DONE
    fieldkit agent discover WORDS... | describe TOOL | run TOOL --input k=v [--mode preview] [--approve] | undo RUN
    fieldkit release check releases/X.yaml       published == tested, and every claim proven (no --force)
    fieldkit release prove releases/X.yaml       run the tag's tests HERE; record evidence for this platform
    fieldkit mcp                                 the agent interface over MCP (LM Studio, Gorilla OpenCode, Claude)
    fieldkit cards list [--level L] | show ID    the contract of every tool, with its trust level
    fieldkit readiness [--write-docs]            how much of the collection an agent can rely on
    fieldkit refcheck manifest|markdown|python|regex TARGET   every named thing must exist (exit 3 if not)
    fieldkit snapshot take NAME [--path DIR...] [--services --tasks --programs --processes]
    fieldkit snapshot diff BEFORE AFTER          exit 3 when something changed
    fieldkit lifecycle SPEC.yaml --approve       install, verify, uninstall; report leftovers (exit 3)
    fieldkit exam run --model ID... [--toolset raw kit] [--task ...]   measure models with/without the kit
    fieldkit exam report
    fieldkit docs plan|prep|fill|render|check|index [GROUP...]   dual-track docs (Gorilla.Documentation.IBM.Style)
    fieldkit kernel localversion --base 7.1.2 --tags unleashed gorilla eapd
    fieldkit kernel fragment INJECTOR.py [--out fragment.yaml]
    fieldkit build-harness latest|vault|start|approve|next|status|submit|unblock|log ...
                                                 Firefox & kernel builds in small checked steps (see buildh/cli.py)
    fieldkit build-harness migrate plan|sitrep|check|advance|work ... TASK
    fieldkit build-harness netbench [TASK] [--bench B1,...] / compare A B   network benches, local servers only
    fieldkit build-harness replay TASK              the public patch set on pristine upstream = the compiled tree (S9)
    fieldkit build-harness techniques TASK          the ideas behind Gorilla's fixes: do they still hold in this tree?
    fieldkit build-harness probe TASK js=NAME       ask a copy of the build a question / try a JS fix without a build
    fieldkit build-harness release-check TASK       every release check in order, one verdict, the fix for each failure
                                                 migration control: stages, gates, SITREP, drift guard (see migrate/cli.py)

Exit codes: 0 fine, 1 error, 2 bad usage, 3 findings (problems, secrets, failed stage).
"""
import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import settings

PIPE_DIR = Path(__file__).resolve().parent / "build" / "pipelines"


def _emit(data, as_json, human=None):
    if as_json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    elif human:
        human(data)
    else:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))


def _vars(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--var needs k=v, got {p!r}")
        k, v = p.split("=", 1)
        out[k] = v
    return out


def _pipeline_path(name):
    p = Path(name)
    if p.suffix in (".yaml", ".yml", ".json") and p.is_file():
        return p
    for ext in (".yaml", ".yml", ".json"):
        c = PIPE_DIR / f"{name}{ext}"
        if c.is_file():
            return c
    raise SystemExit(f"no pipeline {name!r}; have: {', '.join(sorted(q.stem for q in PIPE_DIR.glob('*.y*ml')))}")


# -- handlers -----------------------------------------------------------------
def cmd_host(a):
    from .core.host import host
    _emit(host(), a.json)
    return 0


def cmd_tools(a):
    from .desk import registry
    if a.action == "list":
        rows = [t for t in registry.load() if not a.harness or t.get("harness") == a.harness]
        if a.all:
            from .desk import discover
            known = {str(t.get("path")) for t in rows}
            rows += [dict(t, harness="toolbox", changes=True) for t in discover.discover() if t["path"] not in known]

        def human(rows):
            for t in rows:
                where = t.get("repo") if not t.get("path") else ""
                tested = t.get("test") or t.get("tests")
                flags = ("changes " if t.get("changes", True) else "read-only ") + ("tested" if tested else "NO TEST")
                if t.get("discovered") and not t.get("portable"):
                    flags += f" hard-coded-home:{t['hardcoded_home_paths']}"
                print(f"{t['id']:<26} {t.get('harness', ''):<7} {','.join(t.get('platforms') or []):<14} {flags:<20} "
                      f"{t['title']}{('  [' + where + ']') if where else ''}")
        _emit(rows, a.json, human)
        return 0
    rows = registry.check(run_tests=a.run_tests, only=a.id)

    def human(rows):
        for r in rows:
            extra = ""
            if "probe_safe" in r:
                extra = "probe-safe" if r["probe_safe"] else f"DO NOT PROBE ({r['probe_reason']})"
            if "test_ok" in r:
                extra += f"  test {'PASS' if r['test_ok'] else 'FAIL'}"
            print(f"{r['id']:<26} {r['status']:<8} {'tested' if r['has_test'] else 'no test':<8} {extra}")
        gaps = [r["id"] for r in rows if not r["has_test"] and r["status"] == "present" and not r["retired"]]
        print(f"\n{len(rows)} tools; {len(gaps)} present without a test: {', '.join(gaps)}")
    _emit(rows, a.json, human)
    failed = [r for r in rows if r.get("test_ok") is False or r["status"] == "missing"]
    return 3 if failed else 0


def cmd_office(a):
    from .office import check, create, read, scrub
    if a.action == "read":
        text = read.read(a.files[0], engine=a.engine)
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            _emit({"file": a.files[0], "out": a.out, "chars": len(text)}, a.json)
        elif a.json:
            _emit({"file": a.files[0], "text": text}, True)
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(text)
        return 0
    if a.action == "create":
        spec = settings.read_file(a.files[0])
        rep = create.create(spec, a.files[1], force=a.force)
        _emit(rep, a.json)
        return 0
    if a.action == "check":
        reps = [check.check(f) for f in a.files]
        _emit(reps, a.json, lambda rs: [print(f"{'OK  ' if not r['problems'] else 'FAIL'} {r['file']}  {r['info']}"
                                              + "".join(f"\n     - {p}" for p in r['problems'])) for r in rs])
        return 3 if any(r["problems"] for r in reps) else 0
    if a.action == "scrub":
        from .core import privacy
        terms = (a.term or []) + privacy.private_terms()
        reps = []
        for f in a.files:
            if a.check:
                found = scrub.inspect(f, terms)
                reps.append({"file": f, "found": found})
            else:
                reps.append({"file": f} | scrub.scrub(f, terms, backup=not a.no_backup))
        _emit(reps, a.json)
        return 3 if a.check and any(r["found"] for r in reps) else 0
    if a.action == "deliver":
        import hashlib
        from .core.pipeline import Pipeline
        reps = []
        for f in a.files:
            key = hashlib.sha256(str(Path(f).resolve()).lower().encode()).hexdigest()[:12]
            pipe = Pipeline.load(PIPE_DIR / "office-deliver.yaml", overrides={"file": f, "no_backup": "1" if a.no_backup else ""},
                                 state_dir=settings.ROOT / "state" / "office-deliver" / key)
            reps.append({"file": f} | pipe.run(force=True))

        def show(rs):
            for r in rs:
                print(f"{'SAFE TO SEND' if r['ok'] else 'NOT SAFE'}  {r['file']}")
                for st in r["stages"]:
                    res = st.get("result") or {}
                    print(f"  {st['status']:<15} {st['id']}: {res.get('detail', '') if isinstance(res, dict) else ''}")
        _emit(reps, a.json, show)
        return 0 if all(r["ok"] for r in reps) else 3
    return 2


def cmd_pipeline(a):
    from .core.pipeline import Pipeline
    if a.action == "list":
        rows = []
        for p in sorted(PIPE_DIR.glob("*.y*ml")):
            d = settings.read_file(p)
            rows.append({"name": d["name"], "stages": [s["id"] for s in d["stages"]], "description": d.get("description", "")})
        _emit(rows, a.json, lambda rs: [print(f"{r['name']:<18} {' > '.join(r['stages'])}") for r in rs])
        return 0
    path = _pipeline_path(a.name)
    strict = a.action == "run" and not a.dry_run
    pl = Pipeline.load(path, overrides=_vars(a.var), strict=strict)
    if a.action == "plan":
        _emit(pl.plan(), a.json, lambda p: [print(f"{s['id']:<14} {'here' if s['runs_here'] else 'NOT HERE':<9} "
                                                 f"{s['platforms']}  last={s['last_status']}  "
                                                 f"{s['run'].get('cmd') or s['run'].get('python')}") for s in p["stages"]])
        return 0
    if a.action == "status":
        _emit(pl.state(), a.json)
        return 0
    if a.action == "reset":
        pl.reset(a.stage)
        _emit({"reset": a.stage or "all"}, a.json)
        return 0
    rep = pl.run(only=a.only, start=a.start, dry_run=a.dry_run, force=a.force)

    def human(r):
        for s in r["stages"]:
            res = s.get("result") if isinstance(s.get("result"), dict) else {}
            print(f"{s['id']:<14} {s['status']:<18} {s.get('detail') or res.get('detail') or ''}")
            for v in s.get("verify", []):
                print(f"               {v}")
            if s.get("triage"):
                t = s["triage"]
                print(f"               triage: {t['verdict']}")
                for m in t["matches"]:
                    print(f"                 {m['id']}: {m['cause']}\n                 fix: {m['fix']}")
        print(f"\n{'OK' if r['ok'] else 'STOPPED at ' + str(r.get('stopped_at'))}")
    _emit(rep, a.json, human)
    return 0 if rep["ok"] else 3


def cmd_triage(a):
    from .build import triage
    rep = triage.triage_file(a.log, a.set)

    def human(r):
        print(f"log: {r['log']}\nerror lines: {r['error_count']}   verdict: {r['verdict'].upper()}")
        for e in r["errors"][:10]:
            print(f"  [{e['kind']}] {e['line'][:160]}")
        for m in r["matches"]:
            print(f"\n  KNOWN {m['id']} ({m['set']}){'  [check: ' + m['check'] + ']' if m.get('check') else ''}"
                  f"\n    cause: {m['cause']}\n    fix:   {m['fix']}")
        if r.get("stub"):
            print("\n  New failure. Add to the signature set:\n" + r["stub"])
        if r.get("hint"):
            print("\n  " + r["hint"])
    _emit(rep, a.json, human)
    return 0 if rep["verdict"] == "known" else 3


def cmd_privacy(a):
    from .core import privacy
    rep = {}
    for p in a.paths:
        rep.update(privacy.scan_path(p, git_only=a.git, allow_paths=a.allow_paths, allow_emails=a.allow_emails))

    def human(r):
        for f, hits in r.items():
            print(f)
            for h in hits[:20]:
                print(f"   line {h['line']:<5} {h['kind']:<26} {h['excerpt']}")
        print(f"\n{sum(len(h) for h in r.values())} finding(s) in {len(r)} file(s)")
    _emit(rep, a.json, human)
    return 3 if rep else 0


def cmd_gather(a):
    from . import gather
    rows = gather.gather(only=a.only, offline=a.offline, check=a.check, test=a.test)

    def human(rows):
        for r in rows:
            if a.test:
                if r["ok"] is None:
                    print(f"{r['name']:<28} no tests")
                for run in r["runs"]:
                    print(f"{r['name']:<28} {'PASS' if run['ok'] else 'FAIL'}  {run['cmd']:<52} {(run['tail'] or [''])[-1][:70]}")
                continue
            if "error" in r:
                print(f"{r['name']:<28} ERROR  {r['error']}")
            elif a.check:
                extra = "" if r["status"] == "in-sync" else "  " + ", ".join(
                    f"{k}={len(v)}" for k, v in r.items() if isinstance(v, list) and v)
                print(f"{r['name']:<28} {r['status']}{extra}")
            else:
                warn = []
                if r["compile_errors"]:
                    warn.append(f"{len(r['compile_errors'])} .py do not compile")
                if r["privacy_findings"]:
                    warn.append(f"privacy findings in {len(r['privacy_findings'])} file(s)")
                print(f"{r['name']:<28} {r['files']:>4} files  @{r['commit'] or 'local':<9} {'; '.join(warn)}")
    _emit(rows, a.json, human)
    bad = [r for r in rows if "error" in r or r.get("status") == "drift" or r.get("compile_errors") or r.get("ok") is False]
    return 3 if bad else 0


def cmd_harvest(a):
    from . import gather, harvest
    root = Path(a.root) if a.root else gather.TOOLBOX
    out = Path(a.out) if a.out else settings.ROOT / "harvest"
    if a.find:
        idx_file = out / "harvest.json"
        index = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.is_file() else harvest.build(root)
        hits = harvest.find(index, " ".join(a.find), limit=a.limit)
        def human(hs):
            for h in hs:
                print(f"{h['score']:>3}  {h['id']}")
                print(f"     {h['title'][:110]}")
            if not hs:
                print("no match")
        _emit(hits, a.json, human)
        return 0 if hits else 3
    index = harvest.build(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "harvest.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "HARVEST.md").write_text(harvest.to_markdown(index), encoding="utf-8")
    tools = [e for e in index if not e["is_test"]]
    summary = {"scripts": len(tools), "tests": len(index) - len(tools),
               "undocumented": sum(1 for e in tools if not e["title"]),
               "not_portable": sum(1 for e in tools if not e["portable"]),
               "out": str(out)}
    _emit(summary, a.json)
    return 0


def _inputs(pairs):
    """--input k=v (repeatable). JSON values allowed: --input size=3 --input tags='["a","b"]' --input v=true."""
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--input needs name=value, got {p!r}")
        k, v = p.split("=", 1)
        try:
            out[k] = json.loads(v)
        except ValueError:
            out[k] = v
    return out


def cmd_agent(a):
    from . import agent
    try:
        if a.action == "discover":
            res = agent.discover(" ".join(a.args), include_drafts=a.include_drafts)
            _emit(res, a.json, lambda rs: [print(f"{r['trust']:<9} {str(r['safety']):<12} {r['tool']:<34} "
                                                 f"{r['title'][:60]}  [{', '.join(r['inputs'])}]") for r in rs]
                  or (None if rs else print("no reviewed tool fits; try other words or --include-drafts")))
            return 0 if res else 3
        if a.action == "describe":
            _emit(agent.describe(a.args[0]), a.json)
            return 0
        if a.action == "undo":
            res = agent.undo(a.args[0], approve=getattr(a, "approve", False))
            _emit(res, a.json, lambda r: print(chr(10).join(r["answer"])))
            return 0
        rec = agent.run(a.args[0], _inputs(a.input), mode=a.mode, approve=a.approve)
        _emit(rec, a.json, lambda r: print(chr(10).join(r["answer"])))
        return 0 if rec.get("ok") and rec.get("verified") is not False else 3
    except agent.Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3


def cmd_release(a):
    from . import release
    if a.action == "prove":
        ev, path = release.prove(a.spec)
        _emit({"evidence": str(path)} | ev, a.json, lambda e: print(
            f"{'PASSED' if ev['passed'] else 'FAILED'} on {ev['platform']} for {ev['name']} {ev['tag']} "
            f"(tree {ev['tree'][:12]})" + chr(10) + f"evidence: {path}" + chr(10) +
            "NEXT: bring this file back; `fieldkit release check` accepts it for this platform."))
        return 0 if ev["passed"] else 3
    r = release.check(a.spec)
    _emit(r, a.json, lambda r: print(chr(10).join(release.lines(r))))
    return 0 if r["clear"] else 3


def cmd_mcp(a):
    from . import mcp
    mcp.serve()
    return 0


def cmd_cards(a):
    from .desk import cards
    all_c = cards.all_cards()
    results = cards.test_results()
    if a.action == "show":
        hit = [c for c in all_c if c["id"] == a.id]
        if not hit:
            raise SystemExit(f"no card {a.id!r}; see: fieldkit cards list")
        c = dict(hit[0])
        c["trust"], c["trust_blockers"] = cards.trust(c, results)
        _emit(c, a.json)
        return 0
    rows = []
    for c in all_c:
        lvl, why = cards.trust(c, results)
        if a.level and lvl != a.level:
            continue
        rows.append({"id": c["id"], "level": lvl, "safety": c.get("safety"), "title": c.get("title", ""),
                     "blocker": why[0] if why else ""})

    def human(rows):
        for r in rows:
            print(f"{r['level']:<9} {str(r['safety']):<12} {r['id'][:56]:<56} {r['blocker'][:60]}")
        print(f"\n{len(rows)} card(s)")
    _emit(rows, a.json, human)
    return 0


def cmd_readiness(a):
    from .desk import readiness
    r = readiness.write_docs() if a.write_docs else readiness.report()
    out = {k: v for k, v in r.items() if k != "rows"}
    _emit(out, a.json, lambda o: print(chr(10).join(readiness.lines(r))))
    return 0


def cmd_next(a):
    from .core import next as nxt
    from .core.pipeline import Pipeline
    d = nxt.decide(Pipeline.load(_pipeline_path(a.name), overrides=_vars(a.var), strict=False))
    _emit(d, a.json, lambda d: print(chr(10).join(nxt.lines(d))))
    return {"DO": 0, "DONE": 0}.get(d["kind"], 3)


def cmd_refcheck(a):
    from .build import refcheck
    if a.rule == "manifest":
        r = refcheck.check_manifest(a.target, a.base)
    elif a.rule == "markdown":
        r = refcheck.check_markdown(a.target)
    elif a.rule == "python":
        r = refcheck.check_python(a.target)
    else:
        if not a.pattern:
            raise SystemExit("refcheck regex needs --pattern with one capture group")
        r = refcheck.check_regex(a.target, a.pattern, a.glob)
    _emit(r, a.json, lambda r: [print(line) for line in refcheck.lines(r)])
    return 0 if r["ok"] else 3


def cmd_snapshot(a):
    from .core import snapshot
    if a.action == "list":
        rows = sorted(p.stem for p in snapshot.SNAP_DIR.glob("*.json")) if snapshot.SNAP_DIR.is_dir() else []
        _emit(rows, a.json, lambda rs: [print(r) for r in rs] or (print("no snapshots") if not rs else None))
        return 0
    if a.action == "take":
        if not a.names or len(a.names) != 1:
            raise SystemExit("snapshot take needs one NAME")
        parts = [p for p in ("services", "tasks", "programs", "processes") if getattr(a, p)]
        if not a.path and not parts:
            raise SystemExit("say what to record: --path DIR and/or --services --tasks --programs --processes")
        try:
            out = snapshot.take(a.names[0], a.path or [], parts)
        except ValueError as e:
            raise SystemExit(f"fieldkit: {e}")
        _emit({"snapshot": str(out)}, a.json)
        return 0
    if not a.names or len(a.names) != 2:
        raise SystemExit("snapshot diff needs two names: BEFORE AFTER")
    try:
        d = snapshot.diff(*a.names)
    except ValueError as e:
        raise SystemExit(f"fieldkit: {e}")
    _emit(d, a.json, lambda d: print(chr(10).join(snapshot.summary_lines(d))))
    return 3 if d else 0


def cmd_lifecycle(a):
    from .build import lifecycle
    try:
        r = lifecycle.run(a.spec, approve=a.approve)
    except PermissionError as e:
        raise SystemExit(str(e))
    _emit(r, a.json, lambda r: print(chr(10).join(lifecycle.lines(r))))
    return 0 if r["clean"] else 3


def cmd_exam(a):
    from .exam import runner
    out = Path(a.out) if a.out else settings.ROOT / "exam-results"
    if a.action == "report":
        recs = []
        for p in sorted(out.rglob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec.setdefault("run", p.parent.name)          # results from before runs were named
            recs.append(rec)
    else:
        recs = []
        for m in a.model:
            recs += runner.run(m, toolsets=a.toolset, task_ids=a.task, base=a.base, out_dir=out,
                               max_rounds=a.max_rounds)
    rows = runner.summary(recs)

    def human(rows):
        print()
        print(f"{'run':<22} {'model':<30} {'toolset':<7} {'passed':<8} {'prompt tok':>10} {'seconds':>8} {'calls':>6} "
              f"{'fmt miss':>8} {'chose kit tool':>14}")
        for r in rows:
            chose = f"{r['chose_kit_tool']}/{r['tasks']}" if r["toolset"] == "kit" else "-"
            print(f"{r['run'][:22]:<22} {r['model'][:30]:<30} {r['toolset']:<7} "
                  f"{str(r['passed']) + '/' + str(r['tasks']):<8} "
                  f"{r['prompt_tokens']:>10} {r['seconds']:>8.0f} {r['tool_calls']:>6} {r['format_misses']:>8} "
                  f"{chose:>14}")
    _emit(rows, a.json, human)
    return 0


def cmd_kernel(a):
    from .build import kernel
    if a.action == "localversion":
        _emit(kernel.localversion(a.base, a.tags, year_digits=a.year), a.json)
        return 0
    flags = kernel.fragment_from_injector(a.injector)
    if a.out:
        import yaml
        Path(a.out).write_text(yaml.safe_dump({"flags": flags}, sort_keys=False), encoding="utf-8")
    _emit({"flags": len(flags), "out": a.out} if a.out else {"flags": flags}, a.json)
    return 0


# -- parser ---------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog="fieldkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fieldkit {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("host", parents=[common]).set_defaults(fn=cmd_host)

    t = sub.add_parser("tools", parents=[common])
    t.add_argument("action", choices=["list", "check"])
    t.add_argument("--harness")
    t.add_argument("--all", action="store_true", help="also every script discovered in toolbox/")
    t.add_argument("--run-tests", action="store_true")
    t.add_argument("--id", nargs="*")
    t.set_defaults(fn=cmd_tools)

    o = sub.add_parser("office", parents=[common])
    o.add_argument("action", choices=["read", "create", "check", "scrub", "deliver"])
    o.add_argument("files", nargs="+")
    o.add_argument("--engine", default="builtin", choices=["builtin", "markitdown", "docling"])
    o.add_argument("--out")
    o.add_argument("--check", action="store_true", help="scrub: report only")
    o.add_argument("--term", action="extend", nargs="+", metavar="WORD",
                   help="scrub: extra words to hunt; put them after the files, or repeat: --term A --term B")
    o.add_argument("--no-backup", action="store_true",
                   help="scrub/deliver: keep no backup (backups go to state/office-backups, never beside the file)")
    o.add_argument("--force", action="store_true", help="create: replace an existing output file")
    o.set_defaults(fn=cmd_office)

    p = sub.add_parser("pipeline", parents=[common])
    p.add_argument("action", choices=["list", "plan", "run", "status", "reset"])
    p.add_argument("name", nargs="?")
    p.add_argument("--var", nargs="*")
    p.add_argument("--only", nargs="*")
    p.add_argument("--from", dest="start")
    p.add_argument("--stage")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_pipeline)

    tr = sub.add_parser("triage", parents=[common])
    tr.add_argument("log")
    tr.add_argument("--set", default="auto")
    tr.set_defaults(fn=cmd_triage)

    pr = sub.add_parser("privacy", parents=[common])
    pr.add_argument("action", choices=["scan"])
    pr.add_argument("paths", nargs="+")
    pr.add_argument("--allow-paths", action="store_true")
    pr.add_argument("--allow-emails", action="store_true")
    pr.add_argument("--git", action="store_true", help="scan only what git would publish (respects .gitignore)")
    pr.set_defaults(fn=cmd_privacy)

    g = sub.add_parser("gather", parents=[common])
    g.add_argument("--only", nargs="*")
    g.add_argument("--offline", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--test", action="store_true", help="run each source's tests (imports.yaml)")
    g.set_defaults(fn=cmd_gather)

    hv = sub.add_parser("harvest", parents=[common])
    hv.add_argument("--root", help="folder to index (default: toolbox/)")
    hv.add_argument("--out", help="where to write harvest.json and HARVEST.md (default: harvest/)")
    hv.add_argument("--find", nargs="+", help="plain words: which scripts do this?")
    hv.add_argument("--limit", type=int, default=10)
    hv.set_defaults(fn=cmd_harvest)

    ag = sub.add_parser("agent", parents=[common])
    ag.add_argument("action", choices=["discover", "describe", "run", "undo"])
    ag.add_argument("args", nargs="+", help="discover: goal words; describe/run: TOOL; undo: RUN_ID")
    ag.add_argument("--input", action="append", help="run: name=value (repeat; JSON values allowed)")
    ag.add_argument("--mode", choices=["preview", "apply"], default="apply")
    ag.add_argument("--approve", action="store_true", help="run: the owner approves an irreversible change")
    ag.add_argument("--include-drafts", action="store_true")
    ag.set_defaults(fn=cmd_agent)

    rl = sub.add_parser("release", parents=[common])
    rl.add_argument("action", choices=["check", "prove"])
    rl.add_argument("spec", help="release spec YAML (see fieldkit/release.py)")
    rl.set_defaults(fn=cmd_release)

    sub.add_parser("mcp", help="serve the agent interface over MCP (stdio)").set_defaults(fn=cmd_mcp, json=False)

    cd = sub.add_parser("cards", parents=[common])
    cd.add_argument("action", choices=["list", "show"])
    cd.add_argument("id", nargs="?")
    cd.add_argument("--level", choices=["gathered", "carded", "tested", "verified"])
    cd.set_defaults(fn=cmd_cards)

    rd = sub.add_parser("readiness", parents=[common])
    rd.add_argument("--write-docs", action="store_true", help="refresh local/READINESS.md (this machine's numbers, never published)")
    rd.set_defaults(fn=cmd_readiness)

    nx = sub.add_parser("next", parents=[common])
    nx.add_argument("name", help="pipeline name or file")
    nx.add_argument("--var", nargs="*")
    nx.set_defaults(fn=cmd_next)

    rc = sub.add_parser("refcheck", parents=[common])
    rc.add_argument("rule", choices=["manifest", "markdown", "python", "regex"])
    rc.add_argument("target", help="manifest file, or the folder to scan")
    rc.add_argument("--base", help="manifest: folder the listed paths are relative to (default: its folder)")
    rc.add_argument("--pattern", help="regex: pattern with one capture group naming a path")
    rc.add_argument("--glob", default="*", help="regex: which files to scan")
    rc.set_defaults(fn=cmd_refcheck)

    sn = sub.add_parser("snapshot", parents=[common])
    sn.add_argument("action", choices=["take", "diff", "list"])
    sn.add_argument("names", nargs="*")
    sn.add_argument("--path", nargs="*", help="files/folders to record (content hashed)")
    for part in ("services", "tasks", "programs", "processes"):
        sn.add_argument(f"--{part}", action="store_true")
    sn.set_defaults(fn=cmd_snapshot)

    lc = sub.add_parser("lifecycle", parents=[common])
    lc.add_argument("spec", help="lifecycle spec (YAML): watch, install, upgrade, uninstall")
    lc.add_argument("--approve", action="store_true", help="the owner's go-ahead: this installs software")
    lc.set_defaults(fn=cmd_lifecycle)

    ex = sub.add_parser("exam", parents=[common])
    ex.add_argument("action", choices=["run", "report"])
    ex.add_argument("--model", nargs="+", default=[], help="model ids as the server lists them")
    ex.add_argument("--toolset", nargs="+", default=["raw", "kit"], choices=["raw", "kit"])
    ex.add_argument("--task", nargs="*", help="task ids (default: all)")
    ex.add_argument("--base", default="http://localhost:1234/v1", help="OpenAI-compatible server (LM Studio)")
    ex.add_argument("--out", help="results folder (default: exam-results/)")
    ex.add_argument("--max-rounds", type=int, default=10)
    ex.set_defaults(fn=cmd_exam)

    from .gdocs import cli as gdocs_cli                  # fieldkit docs ... (fieldkit/gdocs/cli.py)
    gdocs_cli.register(sub, common)

    k = sub.add_parser("kernel", parents=[common])
    ks = k.add_subparsers(dest="action", required=True)
    lv = ks.add_parser("localversion", parents=[common])
    lv.add_argument("--base", required=True)
    lv.add_argument("--tags", nargs="+", required=True)
    lv.add_argument("--year", type=int, choices=(2, 4), default=2)
    fr = ks.add_parser("fragment", parents=[common])
    fr.add_argument("injector")
    fr.add_argument("--out")
    k.set_defaults(fn=cmd_kernel)

    th = sub.add_parser("thermal", parents=[common], help="CPU temperature: proven sources, a thermald-like governor for builds")
    th.add_argument("action", choices=["status", "prove", "watch"])
    th.add_argument("--seconds", type=int, default=60, help="watch: how long")
    th.add_argument("--target", type=float, default=75.0, help="watch: target C for the cap")
    th.set_defaults(fn=cmd_thermal)

    bh = sub.add_parser("build-harness", parents=[common],
                        help="Firefox & kernel build harness: vault, checked steps, checkpoints")
    bh.add_argument("action", choices=["latest", "vault", "start", "approve", "next", "status", "submit",
                                       "unblock", "rewind", "log", "watch", "report", "drive", "compare", "audit", "preflight", "build-gate", "build-run", "build-verify", "install", "post-install", "truthbound", "repair", "capture", "leakgate", "leakgate-approve", "leakgate-propose", "leakgate-baseline", "export-hand", "record", "decisions", "claims", "creep", "brief", "briefs", "decide", "deferred", "verify", "snapshot", "visual", "migrate", "netbench", "replay", "techniques", "probe", "release-check", "ui-check"])
    bh.add_argument("--park", help="migration control: do NOT run this record/repair/build-run/decide; park it as a ticket with this reason")
    bh.add_argument("--static", action="store_true", help="visual, ui-check: only the static layer (the ported tree; no browser is started)")
    bh.add_argument("--out", help="snapshot: where to write the captured set (default Build.Work/snapshot-<version>); "
                                  "netbench: the results folder (default <firefox.root>/bench)")
    bh.add_argument("--prove", action="store_true", help="snapshot: rebuild a pristine copy from the set and compare it with the live tree")
    bh.add_argument("--reopen", action="store_true", help="verify: put every false completion back to pending")
    bh.add_argument("args", nargs="*")
    bh.add_argument("--fix-locks", action="store_true", help="preflight: remove stale git locks (only when no git process runs)")
    bh.add_argument("--force", action="store_true", help="build-run: pass --force to the owner's build stage when ONLY build-dependent blockers fail")
    bh.add_argument("--no-backup", action="store_true", dest="no_backup", help="install: skip the backup (never the default)")
    bh.add_argument("--drive", action="store_true", help="post-install: allow the checks that take the keyboard (announced, 20 s countdown)")
    bh.add_argument("--strict", action="store_true", help="decisions: a pending decision counts as not done (release, baseline); "
                                                          "claims: every claim PROVEN, every patch IMPLEMENTED or explained")
    bh.add_argument("--report", help="claims: where to write the public audit report (default <owner>/claims/AUDIT-<major>.md)")
    bh.add_argument("--release", action="store_true", help="leakgate: release run (full durations, 3 repetitions, packets required)")
    bh.add_argument("--repeat", type=int, help="leakgate: repetitions per scenario; netbench: repetitions per bench (default 3)")
    bh.add_argument("--bench", help="netbench: comma list of B1,B2,B3,B4,B5 (default all)")
    bh.add_argument("--profile", choices=["normal", "satellite", "slow", "satellite-emulated", "slow-emulated", "upstream-buffers", "upstream-memcache", "upstream-both"], help="netbench: browser mode level or RAM variant (default normal)")
    bh.add_argument("--links", help="netbench: comma list of broadband,starlink,geo,austere (default all)")
    bh.add_argument("--label", help="netbench: the name of this result (e.g. before-build16)")
    bh.add_argument("--soak", type=int, help="leakgate: startup-idle duration in seconds (spec: 1800 or 3600)")
    bh.add_argument("--firewall", action="store_true", help="leakgate (elevated): outbound block rule for the direct build copy, removed at the end")
    bh.add_argument("--packets-only", action="store_true", dest="packets_only", help="capture: only the frame-level pktmon pass (needs an admin shell)")
    bh.add_argument("--only", help="post-install: comma list of check names to run; visual: comma list of about: pages (a partial run is never OK); migrate check: comma list of stages (S0,S4)")
    bh.add_argument("--restore", help="install: put a backup directory back instead of installing")
    bh.add_argument("--install-dir", dest="install_dir", help="install: the directory to install into (default: the registered install)")
    bh.add_argument("--build", action="store_true", help="preflight: also check what a compile needs (disk, fan control)")
    bh.add_argument("--model", action="store_true", help="preflight: also check that the model server answers")
    bh.add_argument("--technical", action="store_true", help="brief: the full technical brief instead of the plain-words one")
    bh.add_argument("--do", help="brief: the exact sentence the brief told you to type, to carry out the safe fix")
    bh.add_argument("--words", help="decide: what the maintainer decided, in their own words (recorded as the provenance)")
    bh.add_argument("--task")
    bh.add_argument("--pin", help="start: a stable version instead of the latest (e.g. 155.0.1)")
    bh.add_argument("--source", help="start: repository URL or local git path holding the release tag")
    bh.add_argument("--budget", type=int, default=100_000, help="the model's context window, in tokens")
    bh.add_argument("--workdir")
    bh.add_argument("--version", help="vault verify/restore: a version other than the newest")
    bh.add_argument("--note", help="submit: a note for the log")
    bh.add_argument("--hand", action="store_true", help="submit: a PERSON ported this hunk by hand; judge it by meaning (removed lines gone, added text's tokens present), not by the letter")
    bh.add_argument("--session", help="watch/report: a Gorilla OpenCode session id (default: the newest)")
    bh.add_argument("--harness", help="start: the Gorilla.firefox folder to read patches from (default firefox.root)")
    bh.add_argument("--agent", help="drive: the agent command (default gorilla-opencode)")
    bh.add_argument("--max-jobs", type=int, help="drive: stop after this many jobs")
    bh.add_argument("--job-timeout", help="drive: time limit per job, e.g. 45m")
    bh.add_argument("--reference", help="compare: a person-made result folder (the answer key)")
    bh.add_argument("--tools", action="store_true", help="drive: let the model edit with tools instead of answering in text")
    bh.add_argument("--line-ops", action="store_true", help="drive: use line operations (DELETE/CHANGE/INSERT) instead of REMOVE/KEEP questions")
    bh.set_defaults(fn=cmd_build_harness)
    return ap


def cmd_build_harness(a):
    from .buildh import cli as bh, task
    try:
        return bh.run(a, lambda obj, human: _emit(obj, a.json, human))
    except task.Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2


def cmd_thermal(a):
    from .thermal import governor, sensors
    if a.action == "status":
        for name, fn in sensors.PROVIDERS:
            print(f"  {name:14} {fn()}")
        print(f"  cap (PROCTHROTTLEMAX): {governor.read_cap()}%  perf: {sensors.perf_percent()}")
        return 0
    if a.action == "prove":
        name, fn, detail = sensors.best(prove=True)
        print(f"{'PROVEN' if name else 'NO LIVE SOURCE'}: {name}: {detail}")
        return 0 if name else 3
    name, fn, detail = sensors.best(prove=True)
    if not name:
        print("no live source: " + detail)
        return 3
    print(f"watching {name} for {a.seconds}s, target {a.target} C (the cap moves; nothing is killed)")
    gov = governor.Governor(fn, target_c=a.target, interval=3.0, kill=lambda why: print("  would kill: " + why))
    gov.start()
    import time as _t
    end = _t.time() + a.seconds
    try:                                                   # Ctrl+C must still put the power plan's cap back
        while _t.time() < end and gov.is_alive():
            _t.sleep(3)
            print(f"  {fn()} C  cap {gov.cap}%  peak {gov.peak}")
    finally:
        gov.stop()
        gov.join(timeout=15)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):          # Windows consoles default to cp1252
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    if a.cmd == "pipeline" and a.action != "list" and not a.name:
        raise SystemExit("pipeline: name required")
    try:
        return a.fn(a)
    except (settings.SettingsError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"fieldkit: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
