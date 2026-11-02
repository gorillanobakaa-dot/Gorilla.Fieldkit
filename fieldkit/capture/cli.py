"""fieldkit capture: zero-loss capture of everything an agent makes or runs (owner, 2026-11-01).

    fieldkit capture hook --agent claude|gemini|opencode     one hook event on stdin -> the journal; always exit 0
    fieldkit capture scan [--root NAME] [--list] [--json]    gather scripts and commands from the roots and transcripts
    fieldkit capture find WORDS... [--json]                  records matching every word, with full context
    fieldkit capture ledger [--json] [--strict]              every item and its state; --strict exits 3 on a loss
    fieldkit capture verify [--json]                         hash chain, duplicates, blobs, orphan events
    fieldkit capture status [--json]                         last capture, store size, hook install state
    fieldkit capture self-test --first-use|--context|--failed|--before-processing|--crash|--roots|--kinds

The store is state/capture/ (override: FIELDKIT_CAPTURE_DIR), append-only, hash-chained, fsync per record. Nothing is
discarded: "refused" means kept in the queue. Exit: 0 fine, 2 bad usage or not built yet, 3 findings.
"""
import argparse
import json
import sys
from pathlib import Path

from . import store as st

LATER_FLAGS = ["transform", "procedure", "failure-to-check", "languages", "workflows", "registration",
               "fresh-session"]
AGENT_FILES = {"claude": "~/.claude/settings.json", "gemini": "~/.gemini/settings.json",
               "opencode": "~/.config/opencode/opencode.json"}


def _not_built(what):
    print(f"NOT BUILT YET: {what} belongs to a later job of goal harvest-automatic", file=sys.stderr)
    return 2


def _emit(a, data, lines):
    if a.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    else:
        print("\n".join(lines))
        print(f"NEXT: {data.get('next', 'nothing to do')}")


def cmd_hook(a):
    try:
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
        from . import hook
        hook.handle(a.agent, text)
    except Exception as exc:                               # noqa: BLE001 - the agent never sees a failure
        print(f"capture hook: {exc}", file=sys.stderr)
    return 0


def cmd_find(a):
    s = st.Store()
    s.recover()
    recs = s.find(a.words)
    if a.json:
        print(json.dumps(recs, indent=1, ensure_ascii=False))
    else:
        for r in recs:
            print(f"{r['id']} {r['state']} {r['kind']} x{r['count']} exit={r['exit_code']} {r['timestamp']} "
                  f"{(r.get('command') or '')[:100]}")
        print(f"{len(recs)} record(s)\nNEXT: `fieldkit capture find WORDS --json` for the full context")
    return 0


def cmd_ledger(a):
    if a.peer:
        return _not_built("ledger --peer")
    s = st.Store()
    s.recover()
    led = s.ledger()
    bad = led["lost"] + led["unaccounted"] + led["stuck"]
    led["next"] = ("fix what is listed in lost / unaccounted / stuck" if bad else
                   f"{len(led['outstanding'])} item(s) wait for a worker" if led["outstanding"] else "nothing to do")
    lines = [f"{len(led['items'])} items, {led['hook_events_total']} hook events, {led['deduplicated']} repeats counted, "
             f"{len(led['outstanding'])} outstanding"]
    for k in ("lost", "unaccounted", "stuck"):
        lines += [f"{k.upper()}: {i}" for i in led[k]]
    _emit(a, led, lines)
    return 3 if (a.strict and bad) else 0


def cmd_verify(a):
    s = st.Store()
    problems, info = s.verify()
    data = dict(info, ok=not problems, problems=problems,
                next="nothing to do" if not problems else "do not edit the journal; restore state/capture/ from `fieldkit backup`")
    lines = [f"journal: {info['entries']} entries, {info['items']} items, {info['deduplicated']} repeats, "
             f"{info['torn_bytes_kept']} torn bytes kept"] + [f"PROBLEM {p}" for p in problems]
    lines.append("verify: " + ("chain intact, no duplicate item, every version kept" if not problems else f"{len(problems)} problem(s)"))
    _emit(a, data, lines)
    return 3 if problems else 0


def cmd_status(a):
    if a.peer:
        return _not_built("status --peer")
    s = st.Store()
    rec = s.recover()
    state = s.state()
    hooks = {}
    for agent, f in AGENT_FILES.items():
        p = Path(f).expanduser()
        try:
            hooks[agent] = p.is_file() and "capture hook" in p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            hooks[agent] = False
    data = {"store": str(s.root), "last_capture": state["last_ts"], "items": len(state["items"]),
            "hook_events": len(state["events"]), "hooks_installed": hooks, "recovered": rec,
            "next": "nothing to do" if any(hooks.values()) else "no agent hook runs `fieldkit capture hook` yet"}
    lines = [f"store {s.root}", f"last capture: {state['last_ts'] or 'never'}; {len(state['items'])} items, "
             f"{len(state['events'])} hook events"] + [f"hook {k}: {'installed' if v else 'NOT installed'}" for k, v in hooks.items()]
    _emit(a, data, lines)
    return 0


def cmd_selftest(a):
    from . import selftest
    chosen = [k for k in selftest.TESTS if getattr(a, k)]
    later = [k for k in LATER_FLAGS if getattr(a, k.replace("-", "_"))]
    if later:
        return _not_built("self-test --" + later[0])
    rc = 0
    for k in chosen or selftest.TESTS:
        print(f"== self-test --{k.replace('_', '-')}")
        rc = max(rc, selftest.TESTS[k]())
    print("NEXT: " + ("nothing to do" if rc == 0 else "fix the FAIL lines above"))
    return rc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["scan"]:                               # own module: scan.py (roots.py holds the root table)
        from . import scan
        return scan.main(argv[1:])
    if argv[:1] == ["self-test"] and {"--roots", "--kinds"} & set(argv):     # own module: selftest_scan.py
        from . import selftest_scan
        rc = selftest_scan.main([x[2:] for x in argv if x in ("--roots", "--kinds")])
        rest = [x for x in argv if x not in ("--roots", "--kinds")]
        return max(rc, main(rest) if len(rest) > 1 else 0)
    ap = argparse.ArgumentParser(prog="fieldkit capture", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    h = sub.add_parser("hook", help="journal one agent hook event from stdin")
    h.add_argument("--agent", required=True, choices=["claude", "gemini", "opencode"])
    sub.add_parser("scan", help="(not built yet)").add_argument("rest", nargs="*")
    f = sub.add_parser("find", help="records matching every word")
    f.add_argument("words", nargs="+")
    f.add_argument("--json", action="store_true")
    for name in ("ledger", "verify", "status"):
        p = sub.add_parser(name)
        p.add_argument("--json", action="store_true")
        if name != "verify":
            p.add_argument("--peer", action="store_true", help="the same on the Linux computer (not built yet)")
        if name == "ledger":
            p.add_argument("--strict", action="store_true", help="exit 3 if any item is lost, unaccounted or stuck")
    t = sub.add_parser("self-test", help="prove capture in a sandbox store")
    from . import selftest
    for k in list(selftest.TESTS) + [x.replace("-", "_") for x in LATER_FLAGS]:
        t.add_argument("--" + k.replace("_", "-"), dest=k, action="store_true")
    a = ap.parse_args(argv)
    if a.action == "scan":
        return _not_built("scan")
    return {"hook": cmd_hook, "find": cmd_find, "ledger": cmd_ledger, "verify": cmd_verify, "status": cmd_status,
            "self-test": cmd_selftest}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
