"""fieldkit capture: zero-loss capture of everything an agent makes or runs (owner, 2026-11-01).

    fieldkit capture hook --agent claude|gemini|opencode     one hook event on stdin -> the journal; always exit 0
    fieldkit capture scan                                    (not built yet)
    fieldkit capture find WORDS... [--json]                  records matching every word, with full context
    fieldkit capture ledger [--json] [--strict]              every item and its state; --strict exits 3 on a loss
    fieldkit capture verify [--json]                         hash chain, duplicates, blobs, orphan events
    fieldkit capture status [--json]                         last capture, store size, hook install state
    fieldkit capture coverage [--json]                       every agent's transcripts on this computer: read, or listed with a reason
    fieldkit capture requests [--json]                       how much of the noisy `problem` the clean `request` recovers
    fieldkit capture episodes [--dry-run] [--json]           store episodes (session + request -> ordered steps) in the journal
    fieldkit capture distill pairs|refusals|intents [--apply]  P18 stages 2-4 (no model): signatures, refusal table, intent groups
    fieldkit capture distill recipes --intent release [--name] [--apply]  P18 stage 5: align an intent group into a recipe
    fieldkit capture self-test --first-use|--context|--failed|--before-processing|--crash

The store is state/capture/ (override: FIELDKIT_CAPTURE_DIR), append-only, hash-chained, fsync per record. Nothing is
discarded: "refused" means kept in the queue. Exit: 0 fine, 2 bad usage or not built yet, 3 findings.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

from . import store as st

LATER_FLAGS = ["kinds", "roots", "transform", "procedure", "failure-to-check", "languages", "workflows", "registration",
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


def cmd_coverage(a):
    from . import episodes
    cov = episodes.coverage(st.Store().state())
    gaps = sum(1 for g in cov["agents"].values() for u in g["uncaptured"] if u["reason"].startswith("GAP"))
    cov["next"] = "run the capture scan for the GAP sessions" if gaps else "every transcript is read or listed with a reason"
    lines = [f"machine {cov['machine']} ({cov['platform']})"]
    for name, g in cov["agents"].items():
        why = collections.Counter(u["reason"] for u in g["uncaptured"])
        lines.append(f"{name}: {g['on_disk']} on disk, {g['captured']} captured, {len(g['uncaptured'])} listed" +
                     (f" ({g['reason']})" if g.get("reason") else ""))
        lines += [f"    {n} x {r}" for r, n in why.most_common()]
    lines.append(cov["other_machines"])
    _emit(a, cov, lines)
    return 3 if (a.strict and gaps) else 0


def cmd_requests(a):
    from . import episodes
    r = episodes.request_report(st.Store().state())
    r["next"] = "nothing to do"
    lines = [f"{r['items']} items: {r['items_with_clean_request']} with a clean request, {r['items_without_clean_request']} without; "
             f"{r['distinct_requests']} distinct requests"] + [f"    {k}: {v}" for k, v in sorted(r["by_source"].items(), key=lambda x: -x[1])]
    _emit(a, r, lines)
    return 0


def cmd_episodes(a):
    from . import episodes
    s = st.Store()
    if a.dry_run:
        eps, un = episodes.build_episodes(s.state())
        res = {"episodes_found": len(eps), "with_real_request": sum(1 for e in eps.values() if e["real_request"]),
               "with_3plus_steps": sum(1 for e in eps.values() if e["n_steps"] >= 3),
               "items_without_session": un, "episodes_written": 0}
    else:
        s.recover()
        res = episodes.store_episodes(s)
    res["next"] = "stage 2: failure -> fix pairs" if not a.dry_run else "run without --dry-run to store them"
    _emit(a, res, [f"{k}: {v}" for k, v in res.items() if k != "next"])
    return 0


def cmd_distill(a):
    from . import distill as D
    s = st.Store()
    s.recover()
    if a.stage == "pairs":
        sm, sigs, marks = D.stage2(s)
        if a.apply:
            D.dump_yaml(D.SIG_FILE, {"success": [], "signatures": sigs},
                        "# Failure -> fix signatures distilled from the zero-loss capture (P18 stage 2), regenerated by\n"
                        "# `fieldkit capture distill pairs --apply`. Do not edit by hand.\n")
            sm["states_written"] = D.apply_marks(s, marks)
        data = dict(sm, next="stage 3: `fieldkit capture distill refusals`" if a.apply else "rerun with --apply to write them")
    elif a.stage == "refusals":
        sm, marks = D.stage3(s)
        if a.apply:
            D.dump_yaml(D.TABLE_FILE, {"rules": sm["table"]}, "# Router and hook refusals -> what the agent did next (P18 stage 3), regenerated by\n"
                        "# `fieldkit capture distill refusals --apply`.\n")
            sm["states_written"] = D.apply_marks(s, marks)
        data = {k: v for k, v in sm.items() if k != "table"}
        data["top"] = [f"{r['count']} {r['rule']} -> {r['correction']}" for r in sm["table"][:10]]
        data["next"] = "stage 4: `fieldkit capture distill intents`"
    elif a.stage == "recipes":
        from . import recipes as R
        sigs = {x["id"]: x.get("fix", "") for x in (__import__("yaml").safe_load(open(D.SIG_FILE, encoding="utf-8")) or {}).get("signatures", [])}
        rec = R.build(s, a.intent, R.run_sonnet if a.name else None, sigs)
        path = f"fieldkit/method/workflows/{a.intent}.yaml"
        if a.apply and rec["accepted"]:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            D.dump_yaml(path, rec, "# Workflow recipe distilled from captured episodes (P18 stage 5), regenerated by\n"
                        f"# `fieldkit capture distill recipes --intent {a.intent} --name --apply`.\n")
        data = {"recipe": rec["id"], "accepted": rec["accepted"], "named_by": rec["named_by"], "steps": len(rec["steps"]),
                "replayed_ok": f"{rec['episodes']['replayed_ok']} of {rec['episodes']['in_group']}",
                "written": path if (a.apply and rec["accepted"]) else "no",
                "next": "stage 6: weak-model test" if rec["accepted"] else "not accepted: the recipe did not replay"}
        if rec["naming_note"]:
            data["naming_note"] = rec["naming_note"]
    else:
        sm, assigned = D.stage4(s)
        data = dict(sm, next="rerun with --apply to store the intent on each episode")
        if a.apply:
            data["episodes_written"] = D.apply_intents(s, assigned)
            data["next"] = "stage 5: workflow recipes"
    lines = []
    for k, v in data.items():
        if k == "next":
            continue
        if isinstance(v, dict):
            lines.append(f"{k}:")
            lines += [f"    {g}: {x['episodes']} episodes ({x['with_3plus_steps']} with 3+ steps)" if isinstance(x, dict) else f"    {g}: {x}"
                      for g, x in v.items()]
        elif isinstance(v, list):
            lines += [f"    {x}" for x in v]
        else:
            lines.append(f"{k}: {v}")
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
    c = sub.add_parser("coverage")
    c.add_argument("--json", action="store_true")
    c.add_argument("--strict", action="store_true", help="exit 3 if a transcript has tool calls but no captured item")
    sub.add_parser("requests").add_argument("--json", action="store_true")
    e = sub.add_parser("episodes", help="store episodes in the journal")
    e.add_argument("--json", action="store_true")
    e.add_argument("--dry-run", action="store_true")
    dd = sub.add_parser("distill", help="P18 stages 2-4: failure->fix signatures, refusal table, intent groups")
    dd.add_argument("stage", choices=["pairs", "refusals", "intents", "recipes"])
    dd.add_argument("--intent", default="release", help="recipes: the intent group to align")
    dd.add_argument("--name", action="store_true", help="recipes: one Sonnet call (spend guard, dollar cap) writes the sentences")
    dd.add_argument("--apply", action="store_true", help="write the states, the data file and the intents")
    dd.add_argument("--json", action="store_true")
    t = sub.add_parser("self-test", help="prove capture in a sandbox store")
    from . import selftest
    for k in list(selftest.TESTS) + [x.replace("-", "_") for x in LATER_FLAGS]:
        t.add_argument("--" + k.replace("_", "-"), dest=k, action="store_true")
    a = ap.parse_args(argv)
    if a.action == "scan":
        return _not_built("scan")
    return {"hook": cmd_hook, "find": cmd_find, "ledger": cmd_ledger, "verify": cmd_verify, "status": cmd_status,
            "coverage": cmd_coverage, "requests": cmd_requests, "episodes": cmd_episodes, "distill": cmd_distill, "self-test": cmd_selftest}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
