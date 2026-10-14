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

    fieldkit triage LOG [--set auto|firefox-windows|debian-kernel|debian-packaging|go]
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
    fieldkit sweep [--run-tests]                 the weekly walk through the scratch folders: what is not in the harness yet
    fieldkit guard -- CMD ...                    any compile job with the fan check and the ~70 C build governor
    fieldkit adopt NAME... [--into G] [--apply]  move a Scripts tool into local/kits, repoint shortcuts/skills/memory
    fieldkit adopt --finish [--apply]            remove old copies of adopted tools once nothing runs from them
    fieldkit refcheck manifest|markdown|python|regex TARGET   every named thing must exist (exit 3 if not)
    fieldkit repro compare A B [--root RA RB]     two builds byte for byte: REPRODUCED, or what differs and why (exit 3)
    fieldkit laptop cpu|alerts|sound|volume|video|wallpaper|rules ...   a laptop's everyday repairs (fieldkit/laptop)
    fieldkit voice doctor|install|say|clean ...          a voice worth listening to, on Linux (fieldkit/voice)
    fieldkit watchdog status|run|test|pause|install ...  keep the laptop online, or say so out loud (fieldkit/watchdog)
    fieldkit net route|compare|kind|router|antenna      which interface carries traffic; what antenna the bands need (fieldkit/net)
    fieldkit explain [TOPIC] [--lang ro]                a written explanation from a file: no model, no tokens (fieldkit/explain)
    fieldkit sitrep template|check|render|example|doctor [FILE] [--lang ro] [--order classic] [--md]   a status report a reader can take in pieces (fieldkit/sitrep)
    fieldkit teach [SESSION|newest] --out LOG.md [--since T] [--tools Bash,PowerShell]   every command a session ran, with what came back (fieldkit/teach)
    fieldkit teach propose LOG.md --out DIR [--name N]   a pipeline draft, signature stubs and card candidates from a teaching log
    fieldkit terminal doctor|ask|run|history [-- COMMAND ...]   open a terminal window in front of a person with the command ready; history = every window's outcome (fieldkit/terminal)
    fieldkit update [--only apt|github|native] [--apply]   what is behind what was published (fieldkit/update)
    fieldkit wifi doctor|scan|upgrade                   why the wifi is slow, from the hardware (fieldkit/wifi)
    fieldkit wifi channels [--why|--check CH|--networks] which channel is free, and the argument for it
    fieldkit wifi card                                  what this machine's radio can actually do
    fieldkit wifi gui                                   the same thing in a clickable window
    fieldkit shot read|speedtest|verdict FILE...        read numbers off a screenshot (fieldkit/shot)
    fieldkit snapshot take NAME [--path DIR...] [--services --tasks --programs --processes]
    fieldkit snapshot diff BEFORE AFTER          exit 3 when something changed
    fieldkit lifecycle SPEC.yaml --approve       install, verify, uninstall; report leftovers (exit 3)
    fieldkit exam run --model ID... [--toolset raw kit] [--task ...]   measure models with/without the kit
    fieldkit exam report
    fieldkit docs plan|prep|fill|render|check|index [GROUP...]   dual-track docs (Gorilla.Documentation.IBM.Style)
    fieldkit docs guide                          how to write for a reader who has never opened a terminal
    fieldkit docs philosophy                     why: the Gorilla Open Source Philosophy
    fieldkit release-page compose|check          a release page with both tracks in full on it, plain language first
    fieldkit kernel localversion --base 7.1.2 --tags unleashed gorilla eapd
    fieldkit kernel fragment INJECTOR.py [--out fragment.yaml]
    fieldkit build-harness latest|vault|start|approve|next|status|submit|unblock|log ...
                                                 Firefox & kernel builds in small checked steps (see buildh/cli.py)
    fieldkit build-harness migrate plan|sitrep|check|advance|work ... TASK
    fieldkit build-harness netbench [TASK] [--bench B1,...] / compare A B   network benches, local servers only
    fieldkit build-harness replay TASK              the public patch set on pristine upstream = the compiled tree (S9)
    fieldkit build-harness repro TASK [--run]       the binary reproduced: built again elsewhere, packages byte for byte
    fieldkit build-harness techniques TASK          the ideas behind Gorilla's fixes: do they still hold in this tree?
    fieldkit build-harness probe TASK js=NAME       ask a copy of the build a question / try a JS fix without a build
    fieldkit build-harness probe-compare A B        two saved probe outputs (TOK|...): LOST / changed / now-set
    fieldkit build-harness stats TASK [--since D]   the task's journal counted: builds, attempts, stops, hand edits
    fieldkit build-harness leakgate-status TASK     a (running) leak gate: runs started of expected, alive, CPU since start
    fieldkit build-harness leakgate-context TASK [HOST...]  each host in the install's omni.ja: path:line and context
    fieldkit build-harness leakgate-proposal TASK RESULT.json...  reviewers' rows -> proposed dispositions (never approved)
    fieldkit build-harness release-check TASK       every release check in order, one verdict, the fix for each failure
                                                 migration control: stages, gates, SITREP, drift guard (see migrate/cli.py)

Exit codes: 0 fine, 1 error, 2 bad usage, 3 findings (problems, secrets, failed stage).
"""
import argparse
import os
import re
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


def cmd_where(a):
    """The Fieldkit folder, so a document can say `cd (fieldkit where)` instead of a blank the reader must fill in
    (2026-10-10: `cd "<your Fieldkit folder>"`, pasted as given, failed in C:\\WINDOWS\\system32)."""
    from .core import settings
    root = str(settings.ROOT)
    _emit({"fieldkit": root}, a.json, lambda d: print(d["fieldkit"]))
    return 0


def cmd_doctor(a):
    from .core import doctor
    r = doctor.check(a.pipeline)
    _emit(r, a.json, lambda d: print("\n".join(doctor.lines(d))))
    return 0 if r["ok"] else 3


def cmd_audio(a):
    from .audio import chain
    r = chain.run()
    _emit(r, a.json, lambda d: print("\n".join(chain.lines(d))))
    return 0 if r["ok"] else 3


def cmd_backup(a):
    from .core import backup
    if a.action:
        from .core import usbcopy
        if a.action == "remind":
            r = usbcopy.remind(speak=not a.quiet)
        elif a.check:
            r = usbcopy.check(a.drive, a.stick)
        else:
            r = usbcopy.copy(a.drive, a.stick, a.source)
        _emit(r, a.json, lambda d: print("\n".join(usbcopy.lines(d))))
        return 0 if r["ok"] else (2 if r["status"] in ("ask", "no-backup") else 3)
    r = backup.backup(to=a.to)
    _emit(r, a.json, lambda d: print("\n".join(backup.lines(d))))
    return 0 if r["ok"] else (2 if r["status"] in ("ask", "refused") else 1)


def _check_lines(d):
    print(f"cv: {d['file']} - {d['verdict']} ({d['lang']}, {d['words']} words, {d['pages']} page(s) "
          f"{d['pages_how']}); trades: {', '.join(d['trades']) or 'none'}"
          + (f"; licences seen: {', '.join(d['licences'])}" if d["licences"] else ""))
    for i, f in enumerate(d["findings"], 1):
        print(f"  {i:>2}. {f['severity'].upper():<6} {f['message']}" + (f" (line {f['line']})" if f["line"] else ""))
        if f["excerpt"]:
            print(f"        \"{f['excerpt']}\"")
    print("QUESTIONS (ask every one; wait for the answers):")
    for q in d["questions"]:
        print(f"  - {q['text']}" + (f"   [the CV says: {q['cv_says']}]" if q["cv_says"] else ""))
    print(f"NEXT: {d['next']}")


def _kv_lines(d):
    for k, v in d.items():
        if k != "next":
            print(f"{k}: {v}")
    if d.get("next"):
        print(f"NEXT: {d['next']}")


def cmd_cv(a):
    from .career import cv, store
    from .career.profile import BadProfile
    try:
        if a.action == "init":
            r = store.init(name=a.name, lang=a.lang or "en")
            if r.get("status") == "ask":
                _emit(r, a.json, lambda d: print(f"QUESTION: {d['question']}\nNEXT: {d['next']}"))
                return 2
        elif a.action == "import":
            r = store.import_original(a.file)
        elif a.action == "check":
            r = cv.check(a.file, lang=a.lang, trades=a.trade)
            _emit(r, a.json, _check_lines)
            return 0 if r["ok"] else 3
        else:
            r = cv.render(variant=a.variant, lang=a.lang or "en")
    except (BadProfile, FileNotFoundError, TypeError) as e:
        msg = "a file is needed: fieldkit cv check FILE" if isinstance(e, TypeError) else str(e)
        _emit({"ok": False, "error": msg}, a.json, lambda d: print(f"REFUSED: {d['error']}"))
        return 2
    _emit(r, a.json, _kv_lines)
    return 0 if r.get("ok", True) else 3


def cmd_jobs(a):
    import json as _json
    from .career import pack, pipeline, store, takehome
    from .career import profile as prof
    from .career import sources
    if a.action == "takehome":
        if a.gross is None:
            print("fieldkit jobs takehome --gross N (a year, in pounds)", file=sys.stderr)
            return 2
        r = takehome.take_home(a.gross, a.pension)
        _emit(r, a.json, _kv_lines)
        return 0
    try:
        p = prof.load()
        t = store.Tracker()
        if a.action == "run":
            r = pipeline.run(p, t, top=a.top, do_search=not a.no_search, pension=a.pension)
            def show(d):
                print(f"searched {d['searched']}, seen {d['results_seen']}, new {d['new_jobs']}, "
                      f"rescored {d['rescored']}, errors {len(d['errors'])}")
                for e in d["errors"][:10]:
                    print(f"  ERROR [{e['source']}] {e['query']}: {e['error']}")
                for j in d["shortlist"]:
                    print(f"  {j['score']:>3}  {j['cv']:<12} {j['salary_status']:<9} {j['title'][:44]:<44} "
                          f"{(j['employer'] or '')[:28]}  {j['key']}")
                for h in d["human_actions"]:
                    print(f"  TO DO: {h}")
                print(f"report: {d['report_md']}")
            _emit(r, a.json, show)
            return r["exit_code"]
        if a.action == "add":
            data = prof.load_yaml(a.key)
            r = {"new": pipeline.add_manual(t, data.get("jobs", data) if isinstance(data, dict) else data),
                 "rescored": pipeline.rescore(p, t), "next": "fieldkit jobs run --no-search"}
        elif a.action == "rescore":
            r = {"rescored": pipeline.rescore(p, t), "next": "fieldkit jobs run --no-search"}
        elif a.action == "list":
            rows = [j for j in t.all() if (a.all or not j["excluded"]) and (not a.status or j["status"] == a.status)
                    and (j["score"] or 0) >= a.min_score]
            rows.sort(key=lambda j: (-(j["score"] or 0), j["key"]))
            r = {"jobs": [{k: j[k] for k in ("key", "score", "status", "cv", "salary_status", "title", "employer",
                                             "location", "excluded")} for j in rows], "count": len(rows)}
            def show(d):
                for j in d["jobs"]:
                    print(f"  {j['score'] or 0:>3}  {j['status']:<17} {j['cv'] or '-':<12} {j['salary_status'] or '':<9} "
                          f"{(j['title'] or '')[:42]:<42} {(j['employer'] or '')[:26]}  {j['key']}"
                          + (f"  EXCLUDED: {j['excluded']}" if j["excluded"] else ""))
                print(f"{d['count']} job(s)")
            _emit(r, a.json, show)
            return 0
        elif a.action == "ingest-advert":
            from pathlib import Path as _P
            r = pipeline.ingest_advert(p, t, a.key, _P(a.text_file).read_text(encoding="utf-8"), a.apply_url)
        elif a.action == "verify":
            r = pipeline.verify(p, t, a.key, a.salary_min, a.salary_max, a.closes, a.apply_url, a.note)
        elif a.action == "pack":
            job = t.get(a.key)
            if not job:
                raise KeyError(f"no job {a.key!r}")
            d = pack.build(p, job, pension=a.pension)
            if job["status"] == "new":
                t.set_status(a.key, "pack_ready", "pack built")
            r = {"pack": str(d), "next": f"fieldkit jobs apply {a.key}"}
        elif a.action == "check-letter":
            job = t.get(a.key)
            f = store.base() / "packs" / store.safe_key(a.key) / "cover_letter.md"
            if not job or not f.is_file():
                raise KeyError(f"no pack for {a.key!r}: fieldkit jobs pack {a.key}")
            bad = pack.check_letter(p, job, f.read_text(encoding="utf-8"))
            r = {"ok": not bad, "not_approved": bad, "letter": str(f),
                 "next": "done" if not bad else "remove these sentences, or add them to profile.yaml "
                         "letter.extra_allowed ONLY if the candidate confirms they are true"}
            _emit(r, a.json, _kv_lines)
            return 0 if not bad else 6
        elif a.action == "apply":
            job = t.get(a.key)
            if not job:
                raise KeyError(f"no job {a.key!r}")
            d = store.base() / "packs" / store.safe_key(a.key)
            r = {"job": f"{job['title']} - {job['employer']}  [{job['status']}]",
                 "apply_at": job["apply_url"] or job["url"],
                 "verified": job["verified_at"] or "NO - check pay, closing date and link first (ingest-advert/verify)",
                 "cv": pack.latest_cv(job["cv"], pack.lang_of(p)) or
                 f"none yet: fieldkit cv render --variant {job['cv']} --lang {pack.lang_of(p)}",
                 "cover_letter": str(d / "cover_letter.md") if d.is_dir() else f"none: fieldkit jobs pack {a.key}",
                 "check": ", ".join(_json.loads(job["flags"] or "[]")) or "nothing flagged",
                 "next": f"YOU press Submit; then: fieldkit jobs status {a.key} submitted_by_user"}
        elif a.action == "status":
            t.set_status(a.key, a.status, a.note or "")
            r = {"key": a.key, "status": a.status, "next": "fieldkit jobs list"}
        elif a.action == "salary":
            hist = sources.adzuna_histogram(sources.keys(), a.key, a.where)
            r = {"what": a.key, "where": a.where, "histogram": hist, "next": "-"}
        elif a.action == "export":
            import csv as _csv
            rows = t.all()
            import datetime as _dt
            a.key = a.key or str(store.base() / f"jobs_{_dt.date.today().isoformat()}.csv")
            with open(a.key, "w", newline="", encoding="utf-8") as fh:
                w = _csv.DictWriter(fh, fieldnames=[c for c in store.COLUMNS if c not in ("advert_text",)],
                                    extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            r = {"rows": len(rows), "file": a.key, "next": "-"}
    except (prof.BadProfile, ValueError) as e:
        _emit({"ok": False, "error": str(e)}, a.json, lambda d: print(f"REFUSED: {d['error']}"))
        return 2
    except KeyError as e:
        _emit({"ok": False, "error": str(e).strip("'\"")}, a.json, lambda d: print(f"REFUSED: {d['error']}"))
        return 2
    except sources.MissingKey as e:
        _emit({"ok": False, "error": str(e)}, a.json, lambda d: print(f"NEEDS A KEY: {d['error']}"))
        return 4
    _emit(r, a.json, _kv_lines)
    return 0


def cmd_icons(a):
    from .icons import iconkit
    return iconkit.main(a.args, prog="fieldkit icons")


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
    if a.action == "render":
        from .office import render
        try:
            rep = render.render(a.files[0], a.out, a.scale, a.pages, a.sheet)
        except LookupError as e:
            sysname = "windows" if sys.platform == "win32" else "linux"
            _emit({"ok": False, "error": str(e), "install": render.INSTALL[sysname]}, a.json,
                  lambda d: print(f"NEEDS: {d['error']}\nINSTALL: {d['install']}"))
            return 4
        except (FileNotFoundError, RuntimeError) as e:
            _emit({"ok": False, "error": str(e)}, a.json, lambda d: print(f"REFUSED: {d['error']}"))
            return 2
        _emit(rep, a.json, lambda d: print(f"{d['pages']} page(s); drawn: {len(d['rendered'])}\n"
                                           + "\n".join(d['rendered']) + (f"\nsheet: {d['sheet']}" if d.get('sheet') else "")
                                           + f"\nNEXT: {d['next']}"))
        return 0
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
    # count what was EXAMINED, not only what was flagged. "0 findings in 0
    # files" reads as "nothing was scanned", which is the one thing a privacy
    # gate must never be mistaken for. It misled a reader into reporting a
    # vacuous pass twice on 2026-10-13; the scan had in fact examined 662 files.
    # The count comes BACK from the scanner, which is the only thing that knows
    # which files it opened. Counting a.paths here instead reported "out of 1
    # file(s) scanned" for a folder of five (found 2026-10-13, same day as the
    # message was added, by reading its own output on this release's notes).
    counted = []
    for p in a.paths:
        rep.update(privacy.scan_path(p, git_only=a.git, examined=counted,
                                     allow_paths=a.allow_paths, allow_emails=a.allow_emails))
    examined = sum(counted)

    def human(r):
        for f, hits in r.items():
            print(f)
            for h in hits[:20]:
                print(f"   line {h['line']:<5} {h['kind']:<26} {h['excerpt']}")
        scope = "git would publish" if a.git else "scanned"
        print(f"\n{sum(len(h) for h in r.values())} finding(s) in {len(r)} file(s), "
              f"out of {examined} file(s) {scope}")
        if not r and not examined:
            print("NOTHING WAS EXAMINED. That is not a pass: check the path and whether "
                  "--git is excluding everything.")
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
    r = release.before_publish(a.spec) if a.before_publish else release.check(a.spec)
    _emit(r, a.json, lambda r: print(chr(10).join(release.lines(r))))
    return 0 if r["clear"] else 3


def cmd_mcp(a):
    from . import mcp
    mcp.serve()
    return 0


def cmd_cards(a):
    from .desk import cards
    if a.action in ("check", "draft"):
        from .desk import cardcheck
        try:
            if a.action == "draft":
                if not a.id:
                    raise FileNotFoundError("fieldkit cards draft FILE.py")
                print(cardcheck.draft(a.id), end="")
                return 0
            r = cardcheck.check(a.id)
        except (KeyError, FileNotFoundError) as e:
            print(f"REFUSED: {str(e).strip(chr(39))}")
            return 2
        _emit(r, a.json, lambda d: [print(f"FAIL {c['id']}\n" + "\n".join(f"     - {p}" for p in c["problems"]))
                                    for c in d["cards"]] and None or
              [print(f"note {n}") for n in d["not_here"]] and None or
              print(f"{d['checked']} card(s) checked, {d['failing']} failing"))
        return 0 if r["ok"] else 3
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


def cmd_sweep(a):
    from .desk import sweep, registry
    rows = sweep.scan()
    out = {"rows": rows, "summary": sweep.summary(rows)}
    if a.run_tests:
        ids = sorted({i for r in rows for i in r["cards"]})
        out["tests"] = registry.check(run_tests=True, only=ids) if ids else []
        rows = sweep.scan()
        out["rows"], out["summary"] = rows, sweep.summary(rows)

    def human(o):
        print(chr(10).join(sweep.lines(o["rows"])))
        for tr in o.get("tests", []):
            print(f"test {tr.get('id')}: {'ok' if tr.get('test_ok') else 'FAILED'}" + ("" if tr.get('test_ok') else " " + " | ".join(tr.get('test_tail') or [])))
    _emit(out, a.json, human)
    return sweep.exit_code(rows)


def cmd_guard(a):
    from .thermal import buildguard
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        print("usage: fieldkit guard -- CMD ...   (e.g. fieldkit guard -- cargo build --release)")
        return 2
    return buildguard.run(cmd)


def cmd_adopt(a):
    from .desk import adopt
    if a.finish:
        rows = adopt.finish(do_apply=a.apply)
        _emit({"old_copies": rows}, a.json, lambda o: print(chr(10).join(
            f"{'removed' if r['removed'] else ('in use by PID ' + ','.join(map(str, r['running'])) if r['running'] else 'removable')}  {r['folder']}"
            for r in o["old_copies"]) or "no old copies of adopted tools"))
        return 0
    if not a.names:
        print("name the folder(s) under Documents/Scripts to adopt, or --finish")
        return 2
    p = adopt.plan(a.names, into=a.into)
    if not a.apply or not p.get("ok"):
        _emit(p, a.json, lambda o: print(chr(10).join(adopt.lines(o)) + ("" if not o.get("ok") else
                                                                            chr(10) + "NEXT: the same command with --apply")))
        return 0 if p.get("ok") else 2
    r = adopt.apply(p, run_tests=not a.no_tests)
    _emit(r, a.json, lambda o: print(json.dumps(o, indent=1, default=str)))
    return 0 if r.get("ok") else 1


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
    if a.action == "migrate-apply":
        from .build import kmigrate
        r = kmigrate.apply(a.project, a.new)
        _emit(r, a.json, lambda d: print("\n".join(
            [f"wrote {n}" for n in d["written"]] + [f"ported {p}: {'; '.join(h)}" for p, h in d["ported"].items()]
            + [f"PROBLEM: {x}" for x in d["problems"]] + [("APPLIED" if d["ok"] else "NOTHING WRITTEN"), f"NEXT: {d['next']}"])))
        return 0 if r["ok"] else 3
    if a.action in ("migrate-check", "migrate-verify"):
        from .build import kmigrate
        if a.action == "migrate-check":
            r, human = kmigrate.check(a.project, a.old, a.new), kmigrate.check_lines
        else:
            r, human = kmigrate.verify(a.project, a.new, a.old_patches), kmigrate.verify_lines
        if a.out:
            Path(a.out).write_text("\n".join(human(r)) + "\n", encoding="utf-8")
        _emit(r, a.json, lambda d: print("\n".join(human(d))))
        return 0 if r["ok"] else 3
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
# docs/METHODS.md: how the work gets done, as tools. name -> (module in fieldkit/method, one line)
METHODS = {
    "wait": ("wait", "wait on a condition, never on a clock (--until CMD --matches RE --timeout S)"),
    "mutate": ("mutate", "prove a test can fail: break a rule, run the test, restore the file (FILE --swap A B -- TEST)"),
    "regress": ("regress", "test failures by name before and after a change (record LABEL -- CMD; compare A B)"),
    "drift": ("drift", "does a committed generated file still match its generator? reports, never rewrites"),
    "mcp-probe": ("mcp_probe", "speak MCP to a server as a client does: handshake, tools and their size, one call"),
    "i18n": ("i18n", "translation tables: complete, unique per language, every used string covered; disjoint word lists"),
    "ship": ("ship", "the git side of shipping: status, sync a squash-merged branch (merge -s ours), push; never forces"),
    "handover": ("handover", "fixes to someone else's tool as a handover: before/after, why, test, proof; checked to apply"),
    "survey": ("survey", "facts about a repository before reading it: languages, tests, licence, big files, privacy"),
}


def build_parser():
    ap = argparse.ArgumentParser(prog="fieldkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"fieldkit {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("host", parents=[common]).set_defaults(fn=cmd_host)

    sub.add_parser("where", parents=[common], help="print the Fieldkit folder: cd (fieldkit where)").set_defaults(fn=cmd_where)

    dr = sub.add_parser("doctor", parents=[common], help="what this machine still needs, and the line that installs it")
    dr.add_argument("--for", dest="pipeline", help="also the programs this pipeline needs (e.g. debian-kernel)")
    dr.set_defaults(fn=cmd_doctor)

    bk = sub.add_parser("backup", parents=[common],
                        help="a complete, dated, verified zip of the Fieldkit folder (the folder is never guessed)")
    bk.add_argument("action", nargs="?", choices=["usb", "remind"],
                    help="usb: copy the newest backup to a USB stick and verify it there (never deletes anything on "
                         "the stick); remind: how old the last copy on a stick is")
    bk.add_argument("--to", help="the backup folder (default: backup.dir in fieldkit.local.json)")
    bk.add_argument("--from", dest="source", help="usb: the folder holding the backups (default: backup.dir)")
    bk.add_argument("--drive", help="usb: the stick's drive letter (Windows) or mount folder")
    bk.add_argument("--stick", help="usb: the folder of the stick, named directly")
    bk.add_argument("--check", action="store_true", help="usb: list the copies on the stick; change nothing")
    bk.add_argument("--quiet", action="store_true", help="remind: do not say it aloud")
    bk.set_defaults(fn=cmd_backup)

    cvp = sub.add_parser("cv", parents=[common], help="check a CV against fixed rules; write CVs from the profile")
    cvp.add_argument("action", choices=["init", "import", "check", "render"])
    cvp.add_argument("file", nargs="?")
    cvp.add_argument("--trade", action="append", help="check: a trade's rules (driver, security, warehouse ...); "
                                                      "default: the trades the CV looks like")
    cvp.add_argument("--variant", help="render: which cv_variants entry of the profile")
    cvp.add_argument("--name", help="init: the candidate's full name (asked for, never assumed)")
    cvp.add_argument("--lang", choices=["en", "es", "pt", "it", "de", "ro"], help="English, Spanish, Portuguese, Italian, German or Romanian")
    cvp.set_defaults(fn=cmd_cv)

    jb = sub.add_parser("jobs", parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter,
                        help="the UK job hunt: search official APIs, score, packs, tracking (never submits)",
                        description="run [--top N] [--no-search] | add JOBS.yaml | ingest-advert KEY --text-file F | "
                                    "verify KEY ... --note SRC | rescore | list | pack KEY | check-letter KEY | "
                                    "apply KEY | status KEY STATUS | takehome --gross N | salary TITLE | export F.csv\n"
                                    "exit: 0 ok, 2 bad input, 3 no source could search, 4 no API key, "
                                    "5 search errors, 6 letter sentence not approved")
    jb.add_argument("action", choices=["run", "add", "ingest-advert", "verify", "rescore", "list", "pack",
                                       "check-letter", "apply", "status", "takehome", "salary", "export"])
    jb.add_argument("key", nargs="?", help="the job key (reed:123, adzuna:456, manual:...), or a file for add/export")
    jb.add_argument("status", nargs="?", choices=["new", "shortlisted", "pack_ready", "submitted_by_user",
                                                  "interview", "offer", "rejected", "skipped"])
    jb.add_argument("--top", type=int, default=8)
    jb.add_argument("--no-search", action="store_true")
    jb.add_argument("--pension", type=float, default=0.0, help="employee pension %% (net pay arrangement)")
    jb.add_argument("--gross", type=float)
    jb.add_argument("--text-file")
    jb.add_argument("--apply-url")
    jb.add_argument("--salary-min", type=float)
    jb.add_argument("--salary-max", type=float)
    jb.add_argument("--closes")
    jb.add_argument("--note")
    jb.add_argument("--where")
    jb.add_argument("--all", action="store_true", help="list: also excluded jobs, with the reason")
    jb.add_argument("--min-score", type=int, default=0)
    jb.set_defaults(fn=cmd_jobs)

    ic = sub.add_parser("icons", add_help=False,
                        help="crisp desktop icons: why they are soft, rebuild the icon cache, make and check .ico "
                             "(fieldkit icons --help)")
    ic.add_argument("args", nargs=argparse.REMAINDER)
    ic.set_defaults(fn=cmd_icons)

    for name, (_, text) in METHODS.items():
        mp = sub.add_parser(name, add_help=False, help=text)
        mp.add_argument("args", nargs=argparse.REMAINDER)

    ac = sub.add_parser("academic", add_help=False,
                        help="academic work in 7 countries: the deadline asked, references, style, language, words, "
                             "plagiarism pre-check, the next step (fieldkit academic)")
    ac.add_argument("args", nargs=argparse.REMAINDER)
    ac.set_defaults(fn=lambda a: __import__("fieldkit.academic.cli", fromlist=["main"]).main(a.args))
    hd = sub.add_parser("hdmi", add_help=False,
                        help="the HDMI Harness: status, diagnostics, connect the TV (with the GPU-driver repair, "
                             "elevated) (fieldkit hdmi)")
    hd.add_argument("args", nargs=argparse.REMAINDER)
    hd.set_defaults(fn=lambda a: __import__("fieldkit.hdmi.cli", fromlist=["main"]).main(a.args))
    pe = sub.add_parser("peers", add_help=False,
                        help="messages between AI sessions on this computer, in Gorilla OpenCode's protocol: list, "
                             "send, wait (fieldkit peers --help)")
    pe.add_argument("args", nargs=argparse.REMAINDER)
    pe.set_defaults(fn=lambda a: __import__("fieldkit.peers.peers", fromlist=["main"]).main(a.args))

    w11 = sub.add_parser("win11", add_help=False,
                         help="the Windows 11 tweaks framework: modules, fit to this PC, OEM check, run a module "
                              "safely (fieldkit win11)")
    w11.add_argument("args", nargs=argparse.REMAINDER)
    w11.set_defaults(fn=lambda a: __import__("fieldkit.win11.cli", fromlist=["main"]).main(a.args))
    ev = sub.add_parser("evidence", add_help=False,
                        help="research evidence that can be checked later: fetch pages into a store, import a run's "
                             "findings, check every claim (fieldkit evidence)")
    ev.add_argument("args", nargs=argparse.REMAINDER)
    ev.set_defaults(fn=lambda a: __import__("fieldkit.evidence.cli", fromlist=["main"]).main(a.args))

    sub.add_parser("audio", parents=[common],
                   help="Linux: every stage the sound passes through, what is done twice, and the fix (changes nothing)"
                   ).set_defaults(fn=cmd_audio)

    t = sub.add_parser("tools", parents=[common])
    t.add_argument("action", choices=["list", "check"])
    t.add_argument("--harness")
    t.add_argument("--all", action="store_true", help="also every script discovered in toolbox/")
    t.add_argument("--run-tests", action="store_true")
    t.add_argument("--id", nargs="*")
    t.set_defaults(fn=cmd_tools)

    o = sub.add_parser("office", parents=[common])
    o.add_argument("action", choices=["read", "create", "check", "scrub", "deliver", "render"])
    o.add_argument("files", nargs="+")
    o.add_argument("--engine", default="builtin", choices=["builtin", "markitdown", "docling"])
    o.add_argument("--out")
    o.add_argument("--check", action="store_true", help="scrub: report only")
    o.add_argument("--term", action="extend", nargs="+", metavar="WORD",
                   help="scrub: extra words to hunt; put them after the files, or repeat: --term A --term B")
    o.add_argument("--no-backup", action="store_true",
                   help="scrub/deliver: keep no backup (backups go to state/office-backups, never beside the file)")
    o.add_argument("--force", action="store_true", help="create: replace an existing output file")
    o.add_argument("--scale", type=float, default=0.6, help="render: pixels per point")
    o.add_argument("--pages", type=int, default=6, help="render: how many pages to draw")
    o.add_argument("--sheet", action="store_true", help="render: also one contact sheet of the pages side by side")
    o.set_defaults(fn=cmd_office)

    p = sub.add_parser("pipeline", parents=[common])
    p.add_argument("action", choices=["list", "plan", "run", "status", "reset"])
    p.add_argument("name", nargs="?")
    p.add_argument("--var", nargs="+", action="extend", help="k=v; repeat --var or list several after one")
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
    rl.add_argument("--before-publish", action="store_true",
                    help="check: only the gates that need nothing published (privacy, tests) - run it before publishing")
    rl.set_defaults(fn=cmd_release)

    sub.add_parser("mcp", help="serve the agent interface over MCP (stdio)").set_defaults(fn=cmd_mcp, json=False)

    cd = sub.add_parser("cards", parents=[common])
    cd.add_argument("action", choices=["list", "show", "check", "draft"],
                    help="check: every reviewed card against its command; draft FILE.py: a card to review")
    cd.add_argument("id", nargs="?")
    cd.add_argument("--level", choices=["gathered", "carded", "tested", "verified"])
    cd.set_defaults(fn=cmd_cards)

    rd = sub.add_parser("readiness", parents=[common])
    rd.add_argument("--write-docs", action="store_true", help="refresh local/READINESS.md (this machine's numbers, never published)")
    rd.set_defaults(fn=cmd_readiness)

    sw = sub.add_parser("sweep", parents=[common])
    sw.add_argument("--run-tests", action="store_true", help="also run the tests of the registered tools under the sweep roots")
    sw.set_defaults(fn=cmd_sweep)

    gd = sub.add_parser("guard", parents=[common],
                        help="run any compile job (cargo build, go build, make ...) with the fan check and the build governor")
    gd.add_argument("cmd", nargs=argparse.REMAINDER, help="-- then the command")
    gd.set_defaults(fn=cmd_guard)

    ad = sub.add_parser("adopt", parents=[common],
                        help="move a tool out of Documents/Scripts into local/kits and repoint everything (plan first)")
    ad.add_argument("names", nargs="*", help="folders (or loose files, with --into) under Documents/Scripts")
    ad.add_argument("--into", help="the local/kits folder that loose files share")
    ad.add_argument("--apply", action="store_true", help="do it (backed up, tested, rolled back on any failure)")
    ad.add_argument("--finish", action="store_true", help="remove old copies of adopted tools that nothing runs from")
    ad.add_argument("--no-tests", action="store_true", help="skip running the moved tools' card tests")
    ad.set_defaults(fn=cmd_adopt)

    nx = sub.add_parser("next", parents=[common])
    nx.add_argument("name", help="pipeline name or file")
    nx.add_argument("--var", nargs="+", action="extend", help="k=v; repeat --var or list several after one")
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
    mc = ks.add_parser("migrate-check", parents=[common], help="before porting: what carries over to the new kernel")
    mc.add_argument("--project", required=True, help="the kernel project (patches/, registry, shipped files)")
    mc.add_argument("--old", required=True, help="pristine tree of the version the patches were made for")
    mc.add_argument("--new", required=True, help="pristine tree of the new version")
    mc.add_argument("--out", help="also write the report here (evidence for the release notes)")
    ma = ks.add_parser("migrate-apply", parents=[common], help="write the ported files when every anchor is unambiguous")
    ma.add_argument("--project", required=True)
    ma.add_argument("--new", required=True, help="pristine tree of the new version")
    mv = ks.add_parser("migrate-verify", parents=[common], help="after porting: same lines, byte-for-byte rebuild")
    mv.add_argument("--project", required=True)
    mv.add_argument("--new", required=True, help="pristine tree of the new version")
    mv.add_argument("--old-patches", required=True, help="a copy of the patches/ folder before the migration")
    mv.add_argument("--out", help="also write the report here")
    k.set_defaults(fn=cmd_kernel)

    lp = sub.add_parser("laptop", parents=[common], help="a laptop's everyday repairs: cpu, alerts, sound, volume, "
                                                         "video, wallpaper, rules (fieldkit/laptop)")
    lp.add_argument("area", choices=["cpu", "alerts", "sound", "volume", "video", "wallpaper", "rules"])
    lp.add_argument("action", nargs="?", help="cpu: check|guard|install|uninstall|status; alerts: say|hook|watch|"
                                              "install; sound: diagnose|fix|undo|open|drivers; rules: check|sync")
    lp.add_argument("args", nargs="*", help="alerts say: done|focus|look; sound open: sound|devices|settings|maker; "
                                            "rules: SOURCE COPY...")
    lp.add_argument("--apply", action="store_true", help="sound fix, video, alerts install: do it (without: the plan)")
    lp.add_argument("--set", type=int, help="volume: set the master volume to this percent")
    lp.add_argument("--claude-code", dest="claude_code", action="store_true", help="alerts install: Claude Code's hooks")
    lp.add_argument("--antigravity", action="store_true", help="alerts install: the watcher, started at login")
    lp.set_defaults(fn=cmd_laptop)

    vp = sub.add_parser("voice", parents=[common], help="a voice worth listening to, on Linux: doctor, install, "
                                                        "say, clean (fieldkit/voice). Windows is untouched.")
    vp.add_argument("action", choices=["doctor", "install", "say", "clean"])
    vp.add_argument("args", nargs="*", help="say: the sentence to speak")
    vp.add_argument("--apply", action="store_true", help="install, clean: do it (without: the plan)")
    vp.set_defaults(fn=cmd_voice)

    wd = sub.add_parser("watchdog", parents=[common], help="keep this laptop online, or say so out loud: "
                                                           "status, run, test, pause, install (fieldkit/watchdog)")
    wd.add_argument("action", choices=["status", "run", "test", "pause", "install", "uninstall"])
    wd.add_argument("args", nargs="*", help="pause: how many minutes")
    wd.add_argument("--apply", action="store_true", help="install: do it (without: the plan)")
    wd.add_argument("--alarm-after", type=int, default=90, help="offline seconds before the alarm (90)")
    wd.add_argument("--reconnect-after", type=int, default=20, help="offline seconds before rejoining (20)")
    wd.set_defaults(fn=cmd_watchdog)

    wf = sub.add_parser("wifi", parents=[common], help="why the wifi is slow, answered from the "
                                                       "hardware: doctor, scan, channels, card, "
                                                       "upgrade (fieldkit/wifi)")
    # devices and calibrate exist in the standalone file and were missing here.
    # The two entry points must offer the same things as well as print the same
    # numbers: the layman track described `fieldkit wifi devices` and it did not
    # exist (found 2026-10-13, second divergence between these two front doors).
    wf.add_argument("action", choices=["doctor", "scan", "channels", "card", "upgrade", "gui",
                                       "watch", "rate", "devices", "calibrate"])
    wf.add_argument("--mbps", type=float, help="rate: the 'connection speed' a device reports")
    wf.add_argument("--ssid", help="watch: the network name, exactly, punctuation included")
    wf.add_argument("--bssid", help="watch: the radio's MAC, when one name covers both bands")
    wf.add_argument("--samples", type=int, default=8, help="watch: how many scans (8)")
    wf.add_argument("--band", default="2.4", choices=["2.4", "5"], help="which band (2.4)")
    wf.add_argument("--avoid", default="", help="channels you control elsewhere, e.g. 6")
    wf.add_argument("--check", type=int, metavar="CH", help="what does channel CH overlap")
    wf.add_argument("--why", action="store_true", help="the argued rationale only")
    wf.add_argument("--networks", action="store_true", help="every network, not the channels")
    wf.add_argument("--profile", help="a named tuning profile. NEVER automatic: without this "
                                      "the values are neutral and suit any machine")
    wf.add_argument("--list-profiles", action="store_true", help="what profiles exist")
    wf.add_argument("--mine", action="append", default=None, metavar="SSID",
                    help="your OWN access point, never counted against itself")
    wf.add_argument("--count-mine", action="store_true", help="include your own AP (biased)")
    wf.add_argument("--devices", choices=["mixed", "local"], default=None,
                    help="'local': YOU confirm every device here can use channels 12 and 13. "
                         "Never guessed, because only you can know it")
    wf.add_argument("--technical", action="store_true", help="the full engineering table")
    wf.add_argument("--no-sweep", action="store_true",
                    help="devices: read the address table without waking anything")
    wf.add_argument("--anonymise", "--anonymize", action="store_true", dest="anonymise",
                    help="replace the neighbours' names and the unique half of their "
                         "addresses, so the output can be shown to somebody. Every "
                         "MEASUREMENT stays real")
    wf.add_argument("--at", action="append", default=None, metavar="METRES:DBM",
                    help="calibrate: a reading at a KNOWN distance, e.g. --at 2.88:-34. "
                         "Repeat at two or more DIFFERENT distances to fit this building")
    wf.add_argument("--score", type=int, metavar="CH", help="the arithmetic behind one score")
    wf.add_argument("--case", type=int, metavar="CH", help="argue YOUR channel vs the pick")
    wf.set_defaults(fn=cmd_wifi)

    up = sub.add_parser("update", parents=[common],
                        help="what on this machine is behind what was published, and the "
                             "line that fixes it (fieldkit/update)")
    up.add_argument("--only", choices=["apt", "github", "native"],
                    help="one source instead of all three")
    up.add_argument("--apply", action="store_true",
                    help="do the ones that need no root, then read the artifact back")
    up.set_defaults(fn=cmd_update)

    nt = sub.add_parser("net", parents=[common], help="which interface carries your traffic, "
                                                      "what kind of line it is, and what antenna "
                                                      "the cellular bands need (fieldkit/net)")
    nt.add_argument("action", choices=["route", "compare", "kind", "router", "risk",
                                       "cellular", "antenna"])
    nt.add_argument("--iface", help="interface, for prefer/unprefer")
    nt.add_argument("--ip", help="the router's address (default: the gateway)")
    nt.add_argument("--bands", help="carriers in use, e.g. B3+B3+B7+n78+n78")
    nt.add_argument("--cable-m", type=float, default=5.0, help="planned coax run, metres (5)")
    nt.add_argument("--cable", default="RG174 (thin, supplied with most kits)",
                    help="coax type, for the loss budget")
    nt.set_defaults(fn=cmd_net)

    ex = sub.add_parser("explain", parents=[common], help="a written explanation, from a file: "
                                                          "no model, no tokens (fieldkit/explain)")
    ex.add_argument("topic", nargs="?", help="omit to list what can be explained")
    ex.add_argument("--lang", help="a language code, e.g. ro. Falls back to English and says so")
    ex.add_argument("--check", action="store_true", help="verify every translation has an original")
    ex.set_defaults(fn=cmd_explain)

    tm = sub.add_parser("terminal", parents=[common],
                        help="open a terminal window in front of a person with a command ready: doctor, ask (wait for "
                             "their Enter), run (fieldkit/terminal)")
    tm.add_argument("action", choices=["doctor", "ask", "run", "history"])
    tm.add_argument("command", nargs=argparse.REMAINDER, help="the command, after --; give the program's full path")
    tm.add_argument("--title", default=None)
    tm.add_argument("--say", action="store_true", help="also speak the first line, for a reader who hears better than reads")
    tm.set_defaults(fn=cmd_terminal)

    te = sub.add_parser("teach", parents=[common],
                        help="a teaching log from a session transcript: every command run, in order, with what came "
                             "back (fieldkit/teach)")
    te.add_argument("session", nargs="?", default="newest",
                    help="session id prefix, a .jsonl path, 'newest', or 'propose' (then LOG is the teaching log)")
    te.add_argument("log", nargs="?", help="with propose: the teaching log to read")
    te.add_argument("--out", required=True, help="the markdown log to write; with propose, the folder for the drafts")
    te.add_argument("--name", default=None, help="with propose: the pipeline name for the draft")
    te.add_argument("--since", default="", help="ISO time; commands before it are left out")
    te.add_argument("--tools", default="Bash,PowerShell", help="tool names to include, comma-separated")
    te.add_argument("--max-chars", type=int, default=4000, help="output kept per command")
    te.add_argument("--title", default=None)
    te.set_defaults(fn=cmd_teach)

    st = sub.add_parser("sitrep", parents=[common],
                        help="a situation report a reader can take in a few seconds at a time: template, check, "
                             "render, example, doctor (fieldkit/sitrep)")
    st.add_argument("action", choices=["template", "check", "render", "example", "doctor"])
    st.add_argument("file", nargs="?", help="the filled-in report (check, render)")
    st.add_argument("--lang", default="en", help="heading language: en es pt it de ro")
    st.add_argument("--order", choices=["ask-first", "classic"], default="ask-first",
                    help="ask-first puts 'What I need from you' second; classic is the military order, asks last")
    st.add_argument("--md", action="store_true", help="render as markdown instead of plain text")
    st.set_defaults(fn=cmd_sitrep)

    sh = sub.add_parser("shot", parents=[common], help="read numbers off a screenshot: read, "
                                                       "speedtest, verdict (fieldkit/shot)")
    sh.add_argument("action", choices=["read", "speedtest", "verdict"])
    sh.add_argument("files", nargs="+", help="the image files")
    sh.set_defaults(fn=cmd_shot)

    rp = sub.add_parser("repro", parents=[common],
                        help="two builds compared byte for byte: REPRODUCED, or which bytes differ (inside archives) and why")
    rp.add_argument("action", choices=["compare"])
    rp.add_argument("a", help="the first build: a file or a folder")
    rp.add_argument("b", help="the second build, made elsewhere")
    rp.add_argument("--root", nargs=2, metavar=("ROOT_A", "ROOT_B"),
                    help="the two build folders, so a folder baked into a binary is named as the cause")
    rp.set_defaults(fn=cmd_repro)

    th = sub.add_parser("thermal", parents=[common], help="CPU temperature: proven sources, a thermald-like governor for builds")
    th.add_argument("action", choices=["status", "prove", "watch", "profile", "monitor", "autostart"])
    th.add_argument("value", nargs="?", help="profile: quiet|balanced|performance|off; autostart: on|off")
    th.add_argument("--stop", action="store_true", help="monitor: stop the running monitor (the cap goes back to 100%%)")
    th.add_argument("--seconds", type=int, default=60, help="watch: how long")
    th.add_argument("--target", type=float, default=75.0, help="watch: target C for the cap")
    th.set_defaults(fn=cmd_thermal)

    bh = sub.add_parser("build-harness", parents=[common],
                        help="Firefox & kernel build harness: vault, checked steps, checkpoints")
    bh.add_argument("action", choices=["latest", "vault", "start", "approve", "next", "status", "submit",
                                       "unblock", "rewind", "log", "watch", "report", "drive", "compare", "audit", "preflight", "build-gate", "build-run", "build-verify", "install", "post-install", "truthbound", "repair", "capture", "leakgate", "leakgate-approve", "leakgate-propose", "leakgate-baseline", "leakgate-dispositions", "leakgate-rejudge", "export-hand", "record", "decisions", "claims", "creep", "brief", "briefs", "decide", "deferred", "verify", "snapshot", "visual", "migrate", "netbench", "replay", "techniques", "probe", "release-check", "ui-check", "check-change", "images", "weigh", "about-pages", "about-registry", "hidden-pages-doc", "release-cover", "timebombs", "lists-refresh", "calibrate", "stops", "leakgate-summary", "leakgate-scope", "satellite", "window", "awake", "follow",
                                       "probe-compare", "stats", "leakgate-status", "leakgate-context",
                                       "leakgate-proposal", "leakgate-selftest", "herald", "repro", "proposals", "accept"])
    bh.add_argument("--since", help="stats: only journal events from this date or time on (2026-10-02, 2026-10-02 08:00)")
    bh.add_argument("--until", help="stats: only journal events up to this date (the whole day) or time")
    bh.add_argument("--park", help="migration control: do NOT run this record/repair/build-run/decide; park it as a ticket with this reason")
    bh.add_argument("--static", action="store_true", help="visual, ui-check: only the static layer (the ported tree; no browser is started)")
    bh.add_argument("--out", help="snapshot: where to write the captured set (default Build.Work/snapshot-<version>); "
                                  "netbench: the results folder (default <firefox.root>/bench)")
    bh.add_argument("--prove", action="store_true", help="snapshot: rebuild a pristine copy from the set and compare it with the live tree")
    bh.add_argument("--reopen", action="store_true", help="verify: put every false completion back to pending")
    bh.add_argument("--run", action="store_true", help="repro: make the second build, then compare (hours)")
    bh.add_argument("--compare", action="store_true", help="repro: only compare a second build already made")
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
    bh.add_argument("--moz-log", dest="moz_log", help="netbench: MOZ_LOG modules written per visit into a kept folder "
                                                       "beside the result (e.g. cache2:5,nsHttp:5)")
    bh.add_argument("--keep-profile", dest="keep_profile", action="store_true",
                    help="netbench: keep a copy of every profile (its cache) beside the result")
    bh.add_argument("--no-images", dest="no_images", action="store_true",
                    help="netbench: the profiles load no images (permissions.default.image 2) on top of --profile")
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
    bh.add_argument("--dry-run", action="store_true", help="submit: run the check only; no attempt spent, nothing changed (try it before every submit)")
    bh.add_argument("--hand", action="store_true", help="submit: a PERSON ported this hunk by hand; judge it by meaning (removed lines gone, added text's tokens present), not by the letter")
    bh.add_argument("--adapted", default="", help="submit: the MODEL declares an adapted port (a symbol renamed upstream, code moved) with its reason; judged by meaning, saved as a proposal for the owner's one-go acceptance, no attempt spent")
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


def cmd_wifi(a):
    from . import wifi
    if getattr(a, "anonymise", False):
        # Through the environment, not an argument: the sanitising happens at the
        # scan boundary so EVERY view inherits it, including the window, the JSON
        # and anything written later that forgets to ask for it.
        from .wifi import channelsurvey as _cs
        os.environ[_cs.ANONYMISE_ENV] = "1"
    if a.action == "doctor":
        d = wifi.doctor()
        _emit(d, a.json, lambda x: print("\n".join(wifi.lines(x))))
        return wifi.exit_code(d)
    if a.action == "scan":
        nets = wifi.scan()
        _emit({"networks": nets}, a.json, lambda x: print("\n".join(
            f"  {n['signal']:>3}%  ch {n['channel']:<3} {n['band']:<8} {n['ssid']}"
            for n in sorted(x["networks"], key=lambda n: -n["signal"]))))
        return 0
    if a.action == "upgrade":
        _emit(wifi.upgrade(), a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0
    if a.action == "rate":
        from .wifi import channelsurvey as cs
        if a.mbps is None:
            print("REFUSED: pass --mbps with the number your device shows, e.g. --mbps 2401",
                  file=sys.stderr)
            return 2
        d = cs.decode_rate(a.mbps)
        _emit(d, a.json, lambda x: print("\n".join(cs.rate_lines(x))))
        return 2 if "standard" not in d else 0
    if a.action == "watch":
        from .wifi import channelsurvey as cs
        if not (a.ssid or a.bssid):
            print("REFUSED: pass --ssid or --bssid. Watching 'the network' is ambiguous when "
                  "several are in range.", file=sys.stderr)
            return 2
        d = cs.watch(a.ssid, a.bssid, samples=a.samples)
        _emit(d, a.json, lambda x: print("\n".join(cs.watch_lines(x))))
        return 2 if "error" in d else 0
    if a.action == "devices":
        from .wifi import channelsurvey as cs
        devs = cs.local_devices(sweep=not a.no_sweep)
        _emit(devs, a.json, lambda x: print("\n".join(cs.device_lines(x))))
        return 0
    if a.action == "calibrate":
        from .wifi import channelsurvey as cs
        pts = []
        for item in a.at or ():
            m = re.match(r"\s*([0-9.]+)\s*:\s*(-?[0-9.]+)\s*$", item)
            if not m:
                print(f"REFUSED: {item!r} is not METRES:DBM, e.g. 2.88:-34", file=sys.stderr)
                return 2
            pts.append((float(m.group(1)), float(m.group(2))))
        d = cs.calibrate(pts)
        _emit(d, a.json, lambda x: print("\n".join(cs.calibration_lines(x))))
        return 2 if "error" in d else 0
    if a.action in ("channels", "card", "gui"):
        from .wifi import channelsurvey as cs
        if a.list_profiles:
            for n in cs.list_profiles():
                cfg, _ = cs.load_profile(n)
                print(f"  {n:<12} {cfg.get('description', '')}")
            print("\nWithout --profile the values are neutral and suit any machine.")
            return 0
        if a.action == "gui":
            return cs.gui()
        if a.action == "card":
            c = cs.card_report()
            _emit(c, a.json, lambda x: print(cs.card_lines(x)))
            return 0 if c["read"] else 3
        cfg, prob = cs.load_profile(a.profile)
        if a.devices:
            cfg["devices"] = a.devices
        if prob:
            print("REFUSED: " + prob, file=sys.stderr)
            return 2
        nets, problem = cs.scan()
        if problem:
            print("REFUSED: " + problem, file=sys.stderr)
            return 2
        excl = {int(x) for x in re.findall(r"\d+", a.avoid)}
        # the SAME own-AP rule as the standalone file: the two entry points must
        # never disagree about the numbers they print (they did, 2026-10-13)
        mine = set(a.mine or ())
        if not mine and not a.count_mine:
            here = cs.current_network()
            if here:
                mine.add(here)
        if a.count_mine:
            mine = set()
        rows, rules = cs.analyse(nets, a.band, avoid=excl, cfg=cfg, mine=mine)
        rec = cs.recommend(rows, a.band, cfg=cfg)
        here_ch = next((n.channel for n in nets if n.ssid in mine), None)
        if a.json:
            print(cs.to_json(nets, rows, rec, rules, a.band, cfg))
        elif a.score is not None:
            print("\n".join(cs.breakdown_lines(
                cs.score_breakdown(nets, a.score, a.band, cfg, mine))))
        elif a.case is not None:
            print("\n".join(cs.case_for(rows, rec, a.case, rules, cfg)))
        elif a.check is not None:
            print("\n".join(cs.warn_about(rows, a.check)))
        elif a.networks:
            print(cs.network_table(nets, a.band))
        elif a.why:
            print("\n".join(cs.rationale(rows, rec, rules, a.band, cfg)))
        elif a.technical:
            print(cs.text_report(nets, rows, rec, rules, a.band, cfg))
        else:
            print(cs.plain_report(nets, rows, rec, rules, a.band, cfg, here_ch))
        return 0
    raise SystemExit("fieldkit wifi doctor|scan|channels|card|gui|upgrade|watch|rate|devices|calibrate")


def cmd_update(a):
    """What is behind. Reports by default; --apply does only the rootless ones."""
    from . import update as U
    r = U.survey()
    if a.only:
        keep = {"apt": {"apt": r["apt"]}, "github": {"github": r["github"]},
                "native": {"native": r["native"]}}[a.only]
        r = dict(r, **{k: ([] if k != a.only and isinstance(v, list) else v)
                       for k, v in (("apt", {"status": U.OK, "items": []}),
                                    ("github", []), ("native", []))})
        r.update(keep)
        r["behind_count"] = sum(1 for x in (r["github"] + r["native"]) if x["status"] == U.BEHIND)
        r["behind_count"] += len(r["apt"].get("items") or [])
        r["unknown_count"] = sum(1 for x in (r["github"] + r["native"]) if x["status"] == U.UNKNOWN)
        r["unknown_count"] += 1 if r["apt"]["status"] == U.UNKNOWN else 0

    if not a.apply:
        plan = U.apply_plan(r)
        _emit(dict(r, plan=plan), a.json, lambda x: print("\n".join(U.lines(x))))
        if not a.json and plan["needs_root"]:
            print("\nNEEDS ROOT, so it is printed and not run:")
            for item in plan["needs_root"]:
                print(f"  {item['what']}:\n    {item['command']}")
        return 3 if (r["behind_count"] or r["unknown_count"]) else 0

    plan = U.apply_plan(r)
    done, refused = [], plan["needs_root"]
    for item in plan["doable"]:
        rc, out = U._run(["sh", "-c", item["command"]])
        # Never trust the exit code: read the artefact back (directive 2).
        after = U.survey()
        still = [x for x in after["native"] if x["name"] == item["what"]
                 and x["status"] == U.BEHIND]
        done.append({"what": item["what"], "exit": rc, "fixed": not still,
                     "detail": out.strip()[-200:] if rc else "verified by re-reading the launcher"})
    _emit({"applied": done, "refused": refused}, a.json, lambda x: print(
        "\n".join([f"{'fixed' if d['fixed'] else 'DID NOT TAKE'}  {d['what']}: {d['detail']}"
                    for d in x["applied"]] or ["nothing could be done without root"])
        + ("\n\nNEEDS ROOT, so it is printed and not run:\n" +
           "\n".join(f"  {i['what']}:\n    {i['command']}" for i in x["refused"])
           if x["refused"] else "")))
    return 3 if refused or any(not d["fixed"] for d in done) else 0


def cmd_net(a):
    from .net import route, router, cellular
    # the band maths is pure arithmetic and works anywhere; everything else
    # reads Linux state and must REFUSE rather than report an empty result
    blocked = route.unsupported_here()
    if blocked and a.action not in ("cellular", "antenna"):
        print("REFUSED: " + blocked, file=sys.stderr)
        return 4
    if a.action == "route":
        d = route.route_for()
        _emit(d, a.json, lambda x: print("\n".join(route.lines(x))))
        return route.exit_code(d)
    if a.action == "compare":
        d = route.compare()
        _emit(d, a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0
    if a.action == "kind":
        d = route.kind()
        _emit(d, a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0
    if a.action == "router":
        warn = route.gateway_warning()
        if warn and not a.json:
            print("  ! " + warn + "\n", file=sys.stderr)
        d = router.identify(a.ip)
        if warn:
            d["other_routers"] = route.gateways()
        _emit(d, a.json, lambda x: print("\n".join(router.lines(x))))
        return 0
    if a.action == "risk":
        # reads the lockout policy BEFORE any credential is used, so a probe
        # cannot cost the owner access to their own hub
        d = router.risk(a.ip)
        _emit(d, a.json, lambda x: print("\n".join(router.lines(x))))
        return 0
    if a.action in ("cellular", "antenna"):
        if not a.bands:
            print("REFUSED: pass --bands with the hub's own band line, e.g. "
                  "--bands B3+B3+B7+n78+n78. Without it any antenna advice is a guess, "
                  "and a mismatched antenna receives WORSE than none.", file=sys.stderr)
            return 2
        carriers = cellular.parse_bands(a.bands)
        if a.action == "cellular":
            d = cellular.read_status(a.bands)
            _emit(d, a.json, lambda x: print("\n".join(cellular.lines(x))))
            return 0
        d = cellular.antenna_spec(carriers, cable_m=a.cable_m, cable=a.cable)
        _emit(d, a.json, lambda x: print("\n".join(cellular.spec_lines(x))))
        return 2 if "error" in d else 0
    raise SystemExit("fieldkit net route|compare|kind|router|risk|cellular|antenna")


def cmd_explain(a):
    from . import explain
    if a.check:
        d = explain.check()
        _emit(d, a.json, lambda x: print("\n".join(explain.lines(x))))
        return explain.exit_code(d)
    if not a.topic:
        have = explain.topics()
        _emit({"topics": have}, a.json, lambda x: print("\n".join(
            f"  {t:<22} {'+'.join(langs)}" for t, langs in sorted(x["topics"].items()))))
        return 0
    d = explain.explain(a.topic, a.lang)
    _emit(d, a.json, lambda x: print("\n".join(explain.lines(x))))
    return explain.exit_code(d)


def cmd_terminal(a):
    from . import terminal
    if a.action == "doctor":
        d = terminal.doctor()
        _emit(d, a.json, lambda x: print("\n".join(terminal.lines(x))))
        return 0 if d["ok"] else 3
    if a.action == "history":
        rows = terminal.history()
        _emit({"windows": rows}, a.json, lambda x: print("\n".join(terminal.history_lines(x["windows"]))))
        return 0
    cmd, title = terminal.split_args(a.command, a.title)
    if not cmd:
        raise SystemExit(f"fieldkit terminal {a.action} [--title T] -- COMMAND ...")
    d = terminal.open_window(cmd, mode=a.action, title=title, say=a.say)
    _emit(d, a.json, lambda x: print("\n".join(terminal.lines(x))))
    return 0 if d["ok"] else 3


def cmd_teach(a):
    from . import teach
    if a.session == "propose":
        from .teach import propose
        if not a.log:
            raise SystemExit("fieldkit teach propose LOG.md --out DIR")
        d = propose.write(a.log, a.out, name=a.name)
        _emit(d, a.json, lambda x: print(
            f"  wrote {x['out']}: {x['stages']} stage(s), {x['signatures']} signature stub(s), {x['cards']} card "
            f"candidate(s) from {x['commands']} command(s) ({x['failed']} failed). Edit, then fieldkit pipeline plan."
            if x["ok"] else f"  REFUSED: {x['why']}"))
        return 0 if d["ok"] else 3
    d = teach.write(a.session, a.out, since=a.since, tools=tuple(t for t in a.tools.split(",") if t),
                    max_chars=a.max_chars, title=a.title)
    _emit(d, a.json, lambda x: print(f"  wrote {x['out']}: {x['commands']} command(s), {x['failed']} failed, from {x['source']}"
                                     if x["ok"] else f"  REFUSED: {x['why']}"))
    return 0 if d["ok"] else 3


def cmd_sitrep(a):
    from . import sitrep
    if a.action == "doctor":
        d = sitrep.doctor()
        _emit(d, a.json, lambda x: print("\n".join(
            [f"  languages: {', '.join(x['languages'])}"] + [f"  {p}" for p in x["problems"]] +
            ["  OK: sitrep can run here" if x["ok"] else "  NEXT: fix the lines above"])))
        return 0 if d["ok"] else 3
    if a.action == "template":
        print(sitrep.template(a.lang, a.order), end="")
        return 0
    if a.action == "example":
        print(sitrep.example(), end="")
        return 0
    if not a.file:
        raise SystemExit(f"fieldkit sitrep {a.action} FILE")
    try:
        with open(a.file, encoding="utf-8-sig") as fh:
            text = fh.read()
    except OSError as e:
        raise SystemExit(f"REFUSED: cannot read {a.file}: {e.strerror}")
    if a.action == "check":
        d = sitrep.check(text)
        _emit(d, a.json, lambda x: print("\n".join(sitrep.lines(x))))
        return sitrep.exit_code(d)
    d = sitrep.render(text, lang=a.lang, order=a.order, md=a.md)
    _emit(d, a.json, lambda x: print("\n".join(sitrep.lines(x))))
    return sitrep.exit_code(d)


def cmd_shot(a):
    from . import shot
    if a.action == "read":
        for f in a.files:
            text, err = shot.read_text(f)
            _emit({"file": f, "text": text, "error": err}, a.json,
                  lambda x: print(x["text"] or f"  {x['error']}"))
        return 0
    results = [shot.speedtest(f) for f in a.files]
    if a.action == "speedtest":
        _emit({"results": results}, a.json, lambda x: print("\n".join(
            (f"  {r['file']:<14} down {r['download']:>8.2f}  up {r['upload']:>7.2f}  ping {r['ping']}"
             if r["complete"] else f"  {r['file']:<14} INCOMPLETE: {r['error']}")
            for r in x["results"])))
        return 0 if any(r["complete"] for r in results) else 3
    v = shot.verdict(results)
    _emit(v, a.json, lambda x: print("\n".join(shot.lines(x))))
    return shot.exit_code(v)


def cmd_watchdog(a):
    from . import watchdog
    from .watchdog import install as wdinstall
    if a.action == "status":
        st = watchdog.status()
        _emit(st, a.json, lambda d: print("\n".join(
            [f"online: {d['online']}", f"network: {d['ssid'] or 'unknown'}", f"alarms paused: {d['paused']}",
             f"log: {d['log']}"] + ([f"MISSING: {', '.join(d['missing_tools'])}"] if d["missing_tools"] else []))))
        return 0 if st["ok"] else 3
    if a.action == "test":
        r = watchdog.speak(watchdog.TEST)
        _emit({"result": r}, a.json, lambda d: print(d["result"]))
        return 0 if r == "spoken" else 3
    if a.action == "pause":
        if not a.args:
            raise SystemExit("fieldkit watchdog pause MINUTES")
        _emit(watchdog.set_pause(float(a.args[0])), a.json,
              lambda d: print(f"alarms paused for {d['paused_minutes']} minutes"))
        return 0
    if a.action == "run":
        watchdog.loop(alarm_after=a.alarm_after, reconnect_after=a.reconnect_after)
        return 0
    if a.action == "install":
        r = wdinstall.apply() if a.apply else wdinstall.plan()
        _emit(r, a.json, lambda d: print(json.dumps(d, indent=1, default=str)))
        return 0 if r.get("status") in ("running", "plan", "not-this-platform") else 3
    if a.action == "uninstall":
        _emit(wdinstall.uninstall(), a.json, lambda d: print(json.dumps(d, indent=1, default=str)))
        return 0
    raise SystemExit("fieldkit watchdog status|run|test|pause|install|uninstall")


def cmd_voice(a):
    from . import voice
    if a.action == "doctor":
        d = voice.doctor()
        _emit(d, a.json, lambda x: print("\n".join(voice.lines(x))))
        return voice.exit_code(d)
    if a.action == "say":
        if not a.args:
            raise SystemExit('fieldkit voice say "the sentence to speak"')
        r = voice.speak(" ".join(a.args))
        _emit({"result": r}, a.json, lambda x: print(x["result"]))
        return 0 if r in ("spoken", "not-this-platform") else 3
    if a.action == "install":
        r = voice.apply() if a.apply else voice.plan()
        _emit(r, a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0 if r.get("status") in ("ready", "plan", "not-this-platform") else 3
    if a.action == "clean":
        r = voice.clean(apply_it=a.apply)
        _emit(r, a.json, lambda x: print(json.dumps(x, indent=1, default=str)))
        return 0 if r.get("status") != "refused" else 3
    raise SystemExit("fieldkit voice doctor|install|say|clean")


def cmd_laptop(a):
    from .laptop import alerts, cpu, desktop, rules, sound
    act = a.action
    if a.area == "cpu":
        act = act or "check"
        if act in ("check", "guard"):
            st = cpu.check(fix=act == "guard")
            if act == "guard":
                from .core import usbcopy                     # the stick reminder rides along, as it did
                try:
                    usbcopy.remind(speak=True)
                except Exception:
                    pass
            _emit(st, a.json, lambda d: print("\n".join(cpu.lines(d))))
            return cpu.exit_code(st)
        if act in ("install", "uninstall"):
            r = cpu.install(remove=act == "uninstall")
            _emit(r, a.json, _kv_lines)
            return 0 if r["ok"] else 3
        _emit(cpu.status(), a.json)
        return 0
    if a.area == "alerts":
        if act == "say":
            kind = (a.args or ["look"])[0]
            if kind not in alerts.PHRASES:
                raise SystemExit("fieldkit laptop alerts say done|focus|look")
            from .buildh import herald
            print(herald.say(alerts.sentence(kind)))
            return 0
        if act == "hook":
            raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except ValueError:
                payload = {}
            alerts.hook(payload)
            return 0                                       # a hook never blocks the assistant
        if act == "watch":
            alerts.watch()
            return 0
        if act == "install":
            r = {}
            if a.claude_code or not a.antigravity:
                r["claude_code"] = alerts.install_claude(apply=a.apply)
            if a.antigravity or not a.claude_code:
                r["antigravity"] = alerts.install_antigravity(apply=a.apply)
            r["next"] = "done" if a.apply else "the same with --apply writes it"
            _emit(r, a.json)
            return 0
        raise SystemExit("fieldkit laptop alerts say|hook|watch|install")
    if a.area == "sound":
        act = act or "diagnose"
        fn = {"diagnose": lambda: sound.diagnose(), "fix": lambda: sound.fix(a.apply), "undo": lambda: sound.undo(),
              "drivers": lambda: sound.drivers(),
              "open": lambda: sound.open_window((a.args or ["sound"])[0])}.get(act)
        if not fn:
            raise SystemExit("fieldkit laptop sound diagnose|fix|undo|open|drivers")
        try:
            r = fn()
        except RuntimeError as e:
            print(f"REFUSED: {e}")
            return 2
        _emit(r, a.json, lambda d: print("\n".join(sound.lines(d))))
        return 0 if r["ok"] else 3
    if a.area == "volume":
        r = sound.volume(a.set)
        _emit(r, a.json, lambda d: print("\n".join(sound.lines(d))))
        return 0 if r["ok"] else 3
    if a.area == "video":
        r = desktop.video(a.apply)
        _emit(r, a.json, _kv_lines)
        return 0 if r["ok"] else 3
    if a.area == "wallpaper":
        r = desktop.wallpaper()
        _emit(r, a.json, _kv_lines)
        return 0 if r["ok"] else 3
    rows = a.args or []
    r = (rules.sync if act == "sync" else rules.check)(rows[0] if rows else None, rows[1:])
    _emit(r, a.json, lambda d: print("\n".join(rules.lines(d))))
    return 0 if r["ok"] else 3


def cmd_repro(a):
    from .build import repro
    r = repro.compare(a.a, a.b, roots=a.root or [])
    _emit(r, a.json, lambda d: print("\n".join(repro.lines(d))))
    return 0 if r["identical"] else 3


def cmd_thermal(a):
    from .thermal import governor, sensors
    if a.action in ("profile", "monitor", "autostart"):
        from .thermal import profiles
        if a.action == "profile":
            r = profiles.choose(a.value) if a.value else {"ok": True, "profile": profiles.chosen(),
                                                           "profiles": {k: v["about"] for k, v in profiles.PROFILES.items()}}
            _emit(r, a.json, _kv_lines)
            return 0
        if a.action == "autostart":
            if a.value not in ("on", "off"):
                raise SystemExit("fieldkit thermal autostart on|off")
            r = profiles.autostart(a.value == "on")
            _emit(r, a.json, _kv_lines)
            return 0 if r["ok"] else 3
        if a.stop:
            _emit(profiles.stop_monitor(), a.json, _kv_lines)
            return 0
        profiles.monitor()
        return 0
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
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == ["icons"]:                         # IconKit has its own parser: every argument goes to it, --help too
        from .icons import iconkit
        return iconkit.main(args[1:], prog="fieldkit icons")
    if args[:1] == ["academic"]:                      # so is the academic module
        from .academic import cli as academic_cli
        return academic_cli.main(args[1:])
    if args[:1] == ["hdmi"]:                          # the HDMI Harness, elevated and waited for (fieldkit/hdmi)
        from .hdmi import cli as hdmi_cli
        return hdmi_cli.main(args[1:])
    if args[:1] == ["peers"]:                         # messages between sessions on this computer (fieldkit/peers)
        from .peers import peers as peers_cli
        return peers_cli.main(args[1:])
    if args[:1] == ["win11"]:                         # the Windows 11 tweaks framework (fieldkit/win11)
        from .win11 import cli as win11_cli
        return win11_cli.main(args[1:])
    if args[:1] == ["evidence"]:                      # and the evidence store (fieldkit/evidence)
        from .evidence import cli as evidence_cli
        return evidence_cli.main(args[1:])
    if args[:1] and args[0] in METHODS:              # methods as tools (docs/METHODS.md): each has its own parser
        import importlib
        mod = importlib.import_module(f".method.{METHODS[args[0]][0]}", __package__)
        return mod.main(args[1:], prog=f"fieldkit {args[0]}")
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
