"""fieldkit github: guided, verified GitHub setup with profiles (plan item G1, owner order 2026-10-30).

    fieldkit github doctor                  prerequisites, each missing piece with the exact step
    fieldkit github profiles                the profiles, one paragraph each
    fieldkit github explain PROFILE         every setting of a profile in plain words (read only)
    fieldkit github plan PROFILE            the live repository against the profile: now -> wanted (read only; 3 = changes)
    fieldkit github apply PROFILE           at a person's terminal only: snapshot, then ask before each change, verify each
    fieldkit github check [PROFILE]         the live repository against the profile and every known mistake (read only; 3 = findings)
    fieldkit github undo SNAPSHOT|latest    put the settings back from a snapshot
    fieldkit github menu                    the guided menu the double-click launchers start

Options: --repo OWNER/NAME (default: this folder's origin), --checks A,B (the required checks, instead of the ones that
ran), --json. Exit: 0 fine, 1 error or refused, 2 bad usage, 3 findings or changes. No model; every GitHub call goes
through `gh`, and a token or password is never handled here: `gh auth login` is typed by the person.
"""
import argparse
import json
import sys
from pathlib import Path

from . import engine
from .engine import GhError

NEXT_DEFAULT = "NEXT: fieldkit github doctor"


def _profile(pid):
    cat = engine.catalogue()["profiles"]
    if pid not in cat:
        raise SystemExit(f"unknown profile '{pid}'. Profiles: {', '.join(cat)}")
    return cat[pid]


def _repo(a):
    repo = engine.resolve_repo(a.repo)
    if not repo:
        raise SystemExit("no GitHub repository found in this folder. Run: fieldkit github doctor   (or pass --repo OWNER/NAME)")
    return repo


def _emit(a, data, text):
    print(json.dumps(data, indent=1, default=str) if a.json else text)


def cmd_doctor(a):
    steps = engine.doctor(a.repo)
    lines = []
    for s in steps:
        lines.append(f"[{'ok' if s['ok'] else 'MISSING'}] {s['text']}" + ("" if s["ok"] else f"\n        Do this: {s['fix']}"))
    bad = [s for s in steps if not s["ok"]]
    lines.append("NEXT: " + (bad[0]["fix"] if bad else "fieldkit github profiles"))
    _emit(a, {"steps": steps}, "\n".join(lines))
    return 3 if bad else 0


def cmd_profiles(a):
    cat = engine.catalogue()["profiles"]
    lines = []
    for i, (pid, p) in enumerate(cat.items(), 1):
        lines.append(f"{i}. {p['title']}   ({pid})\n   For: {p['who']}\n   {p['changes']}")
    lines.append("Offered but recommended against: " + "; ".join(engine.catalogue()["not_recommended"].values()))
    lines.append("NEXT: fieldkit github explain PROFILE")
    _emit(a, {"profiles": cat}, "\n".join(lines))
    return 0


def cmd_explain(a):
    p, g = _profile(a.profile), engine.Gloss()
    cat = engine.catalogue()
    repo = engine.resolve_repo(a.repo) or "OWNER/NAME"
    lines = [g.add(f"{p['title']}: {p['changes']}"), f"What GitHub learns and who can act through it: {p['threat']}"]
    for sid, want in p["settings"].items():
        lines.append("\n" + engine._explain_block(sid, repo, g, None, want, show_change=False)
                     + f"\n  This profile wants: {engine.fmt(want)}")
    lines.append(f"\nNEXT: fieldkit github plan {a.profile}")
    _emit(a, {"profile": p}, "\n".join(lines))
    return 0


def cmd_plan(a):
    p, repo = _profile(a.profile), _repo(a)
    st = engine.read_state(repo)
    plan = engine.make_plan(p, st, a.check_names)
    g, cat = engine.Gloss(), engine.catalogue()["settings"]
    lines = [f"{repo}: profile '{p['title']}'"]
    for n in plan["notes"]:
        lines.append(f"note: {n}")
    for c in plan["changes"]:
        s = cat[c["id"]]
        lines.append(g.add(f"- {s['title']}: now {engine.fmt(c['current'])}, wanted {engine.fmt(c['wanted'])}\n"
                           f"    Changes for you: {s['impact']}" + (f"\n    BLOCKED: {c['blocked']}" if c["blocked"] else "")))
    if not plan["changes"]:
        lines.append("Nothing to change: the repository already matches this profile.")
    lines.append(f"NEXT: fieldkit github apply {a.profile}" if plan["changes"] else "NEXT: fieldkit github check " + a.profile)
    _emit(a, plan, "\n".join(lines))
    return 3 if plan["changes"] else 0


def cmd_check(a):
    repo = _repo(a)
    if a.profile:
        _profile(a.profile)
    st = engine.read_state(repo)
    res = engine.check(st, a.profile)
    lines = [f"{repo}: checked against {'profile ' + res['profile'] if res['profile'] else 'the known mistakes'}"]
    for n in st["notes"]:
        lines.append(f"note: {n}")
    if not st.get("ran_readable"):
        lines.append("note: recent check runs could not be read, so required check names were not verified")
    for f in res["findings"]:
        lines.append(f"- {f['text']}\n    Fix: {f['fix']}")
    lines.append("NEXT: " + (res["findings"][0]["fix"] if res["findings"] else "nothing to fix"))
    if not res["findings"]:
        lines.insert(1, "Clean: no known mistake and no difference from the profile.")
    _emit(a, res, "\n".join(lines))
    return 3 if res["findings"] else 0


def cmd_apply(a):
    p = _profile(a.profile)
    if p.get("mode") == "first-repo":
        return engine.first_repository(cwd=".")
    if p.get("mode") == "audit":
        return cmd_check(a)
    return engine.apply_profile(a.profile, _repo(a), checks=a.check_names)


def cmd_undo(a):
    if a.snapshot == "latest":
        snap = engine.latest_snapshot(_repo(a))
        if not snap:
            print("no snapshot found for this repository")
            return 1
    else:
        snap = Path(a.snapshot)
    return engine.undo(snap)


def cmd_menu(a):
    print("Welcome. Nothing changes on GitHub unless you type y to a question.\n")
    steps = engine.doctor(a.repo)
    bad = [s for s in steps if not s["ok"]]
    for s in steps:
        print(f"[{'ok' if s['ok'] else 'MISSING'}] {s['text']}" + ("" if s["ok"] else f"\n        Do this: {s['fix']}"))
    cat = engine.catalogue()["profiles"]
    ids = list(cat)
    if bad and bad[0]["id"] != "remote" and bad[0]["id"] != "admin":
        print("\nFix the step above, then start this again.")
        return 3
    print("\nWhat do you want to do?")
    for i, pid in enumerate(ids, 1):
        print(f"  {i}. {cat[pid]['title']}\n       {cat[pid]['who']}")
    print("  0. Quit")
    choice = input("Number: ").strip()
    if not choice.isdigit() or not 0 < int(choice) <= len(ids):
        print("Nothing chosen. Nothing changed.")
        return 0
    pid = ids[int(choice) - 1]
    a.profile = pid
    mode = cat[pid].get("mode")
    if mode == "first-repo":
        return engine.first_repository()
    if mode == "audit":
        return cmd_check(a)
    print()
    cmd_explain(a)
    if input("\nShow what would change in your repository now? [y/N]: ").strip().lower() != "y":
        return 0
    cmd_plan(a)
    if input("\nGo through the changes one by one (each asked first)? [y/N]: ").strip().lower() != "y":
        return 0
    return cmd_apply(a)


COMMANDS = {"doctor": cmd_doctor, "profiles": cmd_profiles, "explain": cmd_explain, "plan": cmd_plan,
            "apply": cmd_apply, "check": cmd_check, "undo": cmd_undo, "menu": cmd_menu}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(prog="fieldkit github", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=list(COMMANDS))
    p.add_argument("target", nargs="?", help="PROFILE (explain, plan, apply, check) or SNAPSHOT (undo)")
    p.add_argument("--repo", help="OWNER/NAME (default: origin of this folder)")
    p.add_argument("--checks", help="comma list of required checks instead of the ones that ran")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    a.profile = a.snapshot = a.target
    a.check_names = [x for x in (a.checks or "").split(",") if x] or None
    if a.command in ("explain", "plan", "apply", "undo") and not a.target:
        p.error(f"{a.command} needs {'a SNAPSHOT' if a.command == 'undo' else 'a PROFILE'}")
    try:
        return COMMANDS[a.command](a)
    except GhError as e:
        print(f"GitHub said: {e.text}\nNEXT: fieldkit github doctor")
        return 1
    except SystemExit as e:
        if isinstance(e.code, str):
            print(e.code)
            return 1
        raise
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Whatever was already confirmed stays; nothing else changed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
