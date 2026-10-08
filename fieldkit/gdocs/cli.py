"""`fieldkit docs ...`: the command line of Gorilla.Documentation.IBM.Style.

    fieldkit docs plan [GROUP...]              groups, state (fresh/stale/never), files to fill, the next step
    fieldkit docs prep [GROUP...] [--force]    stage, dual_track prep, writer brief (default: stale groups)
    fieldkit docs fill [GROUP...]              which .filled.json files are missing or invalid (exit 3 if any)
    fieldkit docs render [GROUP...]            dual_track render + Gorilla checks (default: stale groups; exit 3)
    fieldkit docs check [GROUP...] [--strict]  coverage + Gorilla checks on committed docs (exit 3); --strict: stale fails
    fieldkit docs index                        write docs/dual-track/README.md
    fieldkit docs guide                        print the guide to writing for a reader who has never opened a terminal
    fieldkit docs philosophy                   print the Gorilla Open Source Philosophy: why any of this is done
    fieldkit docs release --manifest F | [PLAIN.md...] --source F... [--source-head F...] [--layman-doc F]
                          [--developer-doc F]  a release's documents against their own sources (exit 3; releasedocs.py)
    fieldkit release-page compose --opening F --layman F --developer F [--extra F] --out F
    fieldkit release-page check PAGE [--layman F] [--developer F]   (exit 3 with every reason)

Registered in fieldkit/cli.py by `register(sub, common)`.
"""
import json
from pathlib import Path

# The long explanation of the layman track: why each rule exists and how to
# follow it, for any project. WRITER_BRIEF.md beside it is the short rulebook the
# checker enforces. Kept as a separate file on purpose: the brief's hash is part
# of every group's freshness, so a word changed there makes every document stale.
GUIDE = Path(__file__).resolve().parent / "LAYMAN_GUIDE.md"


def register(sub, common):
    d = sub.add_parser("docs", parents=[common],
                       help="dual-track documentation (Gorilla.Documentation.IBM.Style): plan, prep, render, check, guide")
    d.add_argument("action", choices=["plan", "prep", "fill", "render", "check", "index", "guide", "philosophy", "release"])
    d.add_argument("groups", nargs="*", help="group names from docs/groups.yaml (default: see each action); "
                                             "release: plain documents (numbers, web addresses, privacy)")
    d.add_argument("--force", action="store_true", help="prep: re-prep even when the prep files are current")
    d.add_argument("--strict", action="store_true", help="check: a stale group is a failure (release gate)")
    # `docs release`: a release's documents against the material they were written from (releasedocs.py)
    d.add_argument("--manifest", help="release: a release-docs.yaml naming sources and documents")
    d.add_argument("--source", action="append", default=[], help="release: a source file (repeat)")
    d.add_argument("--source-head", dest="source_head", action="append", default=[],
                   help="release: a source of which only the first --head-lines lines count (repeat)")
    d.add_argument("--head-lines", dest="head_lines", type=int, default=60, help="release: see --source-head (default 60)")
    d.add_argument("--layman-doc", dest="layman_doc", action="append", default=[], help="release: a layman-track document (repeat)")
    d.add_argument("--developer-doc", dest="developer_doc", action="append", default=[],
                   help="release: a developer-track document (repeat)")
    d.set_defaults(fn=cmd_docs)

    # The release page is built and checked by code, because a page written by
    # hand hid the plain-language track behind a link three releases running.
    # See releasepage.py.
    r = sub.add_parser("release-page", parents=[common],
                       help="build or check a release page: both tracks in full on the page, plain language first")
    r.add_argument("action", choices=["compose", "check"])
    r.add_argument("page", nargs="?", help="check: the page to check")
    r.add_argument("--opening", help="compose: the opening (what it is, should you download it, why it matters)")
    r.add_argument("--layman", help="the rendered plain-language track")
    r.add_argument("--developer", help="the rendered developer track")
    r.add_argument("--extra", help="compose: optional material placed between the two tracks")
    r.add_argument("--out", help="compose: where to write the page")
    r.set_defaults(fn=cmd_release_page)
    return d


def _emit(data, as_json, human):
    if as_json:
        print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
    else:
        human(data)


def _guide(as_json):
    """Print the layman-writing guide. Reads one file that ships with the package;
    changes nothing and needs no group, so it works in any folder."""
    try:
        text = GUIDE.read_text(encoding="utf-8")
    except OSError as e:
        print(f"fieldkit docs: the guide could not be read ({GUIDE}): {e}")
        return 1
    if as_json:
        print(json.dumps({"path": str(GUIDE), "lines": text.count("\n"), "text": text},
                         indent=1, ensure_ascii=False))
    else:
        print(text)
        print("NEXT: fieldkit docs plan   (for Fieldkit's own documents), or write from the guide above")
    return 0


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def cmd_release_page(a):
    from . import releasepage as RP
    try:
        if a.action == "compose":
            missing = [n for n in ("opening", "layman", "developer", "out") if not getattr(a, n)]
            if missing:
                print("fieldkit release-page compose: needs --" + " --".join(missing))
                return 2
            page = RP.compose(_read(a.opening), _read(a.layman), _read(a.developer),
                              _read(a.extra) if a.extra else "")
            findings = RP.check(page, _read(a.layman), _read(a.developer))
            if findings:
                # Nothing is written: a page that fails is not left lying about
                # where it can be published by mistake.
                for f in findings:
                    print("REFUSED: " + f)
                print(f"\nNEXT: fix the opening ({a.opening}) and compose again; "
                      "`fieldkit docs guide` explains what the opening must say")
                return 3
            Path(a.out).write_text(page, encoding="utf-8", newline="\n")
            _emit({"written": a.out, "bytes": len(page.encode("utf-8"))}, a.json,
                  lambda r: print(f"wrote {r['written']} ({r['bytes']} bytes): both tracks in full, plain language first\n"
                                  f"NEXT: fieldkit release-page check {r['written']} --layman {a.layman} --developer {a.developer}"))
            return 0
        if not a.page:
            print("fieldkit release-page check: needs the page to check")
            return 2
        findings = RP.check(_read(a.page), _read(a.layman) if a.layman else "",
                            _read(a.developer) if a.developer else "")
        _emit({"page": a.page, "findings": findings}, a.json,
              lambda r: print("\n".join("FAIL: " + f for f in r["findings"]) if r["findings"]
                              else f"OK: {r['page']} carries both tracks in full, plain language first, with no link away"))
        return 3 if findings else 0
    except OSError as e:
        print(f"fieldkit release-page: {e}")
        return 1


def _release(a):
    """`docs release`: the release's documents against its own source material (releasedocs.py). Exit 3 on findings."""
    from . import releasedocs as RD
    try:
        spec = RD.load_manifest(a.manifest) if a.manifest else {k: [] for k in RD.KEYS} | {"head_lines": a.head_lines}
    except (OSError, ValueError) as e:
        print(f"fieldkit docs release: {e}")
        return 2
    spec["sources"] += [Path(p) for p in a.source]
    spec["source_heads"] += [Path(p) for p in a.source_head]
    spec["layman"] += [Path(p) for p in a.layman_doc]
    spec["developer"] += [Path(p) for p in a.developer_doc]
    spec["plain"] += [Path(p) for p in a.groups]
    r = RD.check(**spec)
    _emit(r, a.json, lambda r: print("\n".join(RD.lines(r))))
    return 0 if r["ok"] else 3


def cmd_docs(a):
    if a.action == "guide":
        return _guide(a.json)
    if a.action == "release":
        return _release(a)
    if a.action == "philosophy":
        from . import releasepage as RP
        text = RP.philosophy_text()
        if a.json:
            print(json.dumps({"path": str(RP.PHILOSOPHY), "text": text}, indent=1, ensure_ascii=False))
        else:
            print(text)
            print("NEXT: fieldkit docs guide   (how to write the plain-language track this asks for)")
        return 0
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
