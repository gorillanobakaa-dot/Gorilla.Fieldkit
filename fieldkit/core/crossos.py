# Version: 1.0.0 · updated 26-11-11-18-00
"""One command, both systems: what a single-system fieldkit command does on the OTHER system (P9).

Owner, 2026-11-11: "de câte ori creăm o regulă în harness, de câte ori creăm o sculă deterministică, de câte ori
creăm un workflow, ăstea se presupune că trebuie să funcționeze automat și pe Linux și pe Windows. Ce este porcăria
asta că nu funcționează decât pe Windows?" That day `fieldkit build-harness build-run firefox-160-linux` on the
Debian laptop said "REFUSED: ... runs on windows only ... a Linux build is `fieldkit pipeline run firefox-linux`":
the harness knew the answer and made the person type it.

The platform gate (core/platform_gate.py) used to refuse. It now asks this module, which reads crossos.yaml (data:
one row per single-system command) and returns one of:
  run        the command is for this system (or has no card): nothing to do
  dispatch   the same job has its own command here: `fieldkit <that>` runs instead, and says so
  offload    the job needs the other system's machine: it runs there over the cable (orchestrate job run), the
             signed result and the log tail come back here; exit 0 on pass, 3 otherwise
  not-this-platform  it cannot exist here (a registry, /sys, this machine's own hardware): one line with the reason,
             exit 0, so a job that calls it carries on (rule 6)
`--on peer` anywhere in a command line sends that command to the other machine, whatever its row says.

A command with a single-system card and NO row is still answered (offload when the card is a build-harness card,
else not-this-platform), and tests/test_crossos.py fails the tree until the row is written.
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().with_name("crossos.yaml")
ROUTES = ("dispatch", "offload", "none")


def load(path=None):
    from . import settings
    return dict(settings.read_file(Path(path or DATA)).get("commands") or {})


def row_for(words, rows):
    """-> (command key, row) for the longest leading match of `words`, or (None, None)."""
    for n in range(len(words), 0, -1):
        key = " ".join(words[:n])
        if key in rows:
            return key, rows[key]
    return None, None


def _other(platforms):
    """The system a single-system card is for, as a household alias ('windows' / 'linux'), or None."""
    p = set(platforms or [])
    if p & {"windows"} and not p & {"linux", "debian"}:
        return "windows"
    if p & {"linux", "debian"} and "windows" not in p:
        return "linux"
    return None


def strip_on_peer(args):
    """-> (args without `--on peer`, True if it was there)."""
    out, hit, i = [], False, 0
    while i < len(args):
        if args[i] == "--on" and i + 1 < len(args) and args[i + 1] == "peer":
            hit, i = True, i + 2
            continue
        if args[i] == "--on=peer":
            hit, i = True, i + 1
            continue
        out.append(args[i])
        i += 1
    return out, hit


def decide(args, index=None, rows=None, here=None):
    """-> {"action": "run"|"dispatch"|"offload"|"not-this-platform", ...}. Pure: reads the index and the rows only."""
    from . import platform_gate
    from .host import platform_ok, this_platform
    if here is None:
        here, fits = this_platform(), platform_ok
    else:                                  # tests: another system, given by name
        fits = (lambda ps: not ps or here in ps or (here == "linux" and "debian" in ps)
                or (here == "macos" and "darwin" in ps))
    args, on_peer = strip_on_peer(list(args))
    if any(a in ("-h", "--help") for a in args):
        return {"action": "run", "args": args}
    index = platform_gate.load_index() if index is None else index
    rows = load() if rows is None else rows
    words = platform_gate._words(args)
    if on_peer:
        peer = "windows" if here != "windows" else "linux"
        return {"action": "offload", "args": args, "device": peer, "command": " ".join(words),
                "why": "--on peer: run on the other machine as asked"}
    hit = platform_gate.matching(words, index)
    if hit is None:
        return {"action": "run", "args": args}
    key, platforms, card_why = hit
    if fits(platforms):
        return {"action": "run", "args": args}
    target = _other(platforms) or (platforms[0] if platforms else "?")
    rkey, row = row_for(words, rows)
    if row is None:      # no answer written yet: still never a refusal; the test fails the tree until a row exists
        build = words[:1] == ["build-harness"]
        row = {"route": "offload" if build else "none", "why": card_why or "", "unwritten": True}
        rkey = key
    route = row.get("route")
    why = row.get("why") or card_why or ""
    if route == "dispatch":
        alt = (row.get("dispatch") or {}).get(here)
        if alt:
            rest = args[len(rkey.split()):]
            argv = []
            for w in alt:
                if w == "{args}":
                    argv += rest
                else:
                    argv.append(str(w))
            if "--json" in args and "--json" not in argv:
                argv.append("--json")
            return {"action": "dispatch", "args": args, "argv": argv, "command": rkey, "why": why}
        route = "offload"                 # dispatch only names the other way round: the job runs where it can
    if route == "offload" and target in ("windows", "linux"):
        return {"action": "offload", "args": args, "device": target, "command": rkey, "why": why,
                "unwritten": row.get("unwritten", False)}
    return {"action": "not-this-platform", "args": args, "command": key, "platforms": list(platforms), "why": why,
            "port": bool(row.get("port")), "unwritten": row.get("unwritten", False)}


def offload(args, device, wall=None, say=print, sender=None, waiter=None, result=None):
    """Run `fieldkit ARGS` on the household device for `device` ('windows'/'linux') over the cable. -> exit code."""
    from ..emerge.runner import offload_target
    try:
        row, name, why = offload_target(device)
    except Exception as e:  # noqa: BLE001 - an unreadable household is "no machine to run it on", said plainly
        row, name, why = None, None, f"{type(e).__name__}: {e}"
    if not name:
        say(f"OFFLOAD FAILED: no {device} machine on the cable ({why}). Nothing ran. NEXT: fieldkit machine reach")
        return 3
    from .. import orchestrate_work as ow
    from ..mandate import jobs
    from . import settings
    tree = settings.state_root() / "state" / "crossos" / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "README.txt").write_text("fieldkit crossos: an offloaded command needs no files; this folder is its "
                                     "empty tree.\n", encoding="utf-8")
    wall = int(wall or 240)
    send = sender or ow.send
    jid, said, t = send("run", name, str(tree), "Gorilla.Fieldkit", argv=list(args), wall=wall)
    if not jid or not (said == "sent" or str(said).startswith("already")):
        say(f"OFFLOAD FAILED: {row['id']} {row['tag']} did not take `fieldkit {' '.join(args)}`: {said}. "
            "NEXT: fieldkit orchestrate job status")
        return 3
    say(f"OFFLOADED: `fieldkit {' '.join(args)}` runs on {row['id']} {row['tag']} ({name}) as job {jid}")
    (waiter or jobs.wait)(jid, wall * 60)
    r = (result or ow.verified_result)(jid, "Gorilla.Fieldkit", (t or {}).get("digest"))
    if r is None:
        say(f"OFFLOAD: no signed result for {jid} within {wall} min. NEXT: fieldkit orchestrate job status")
        return 3
    tail = r.get("tail") or r.get("evidence") or ""
    if tail:
        say(str(tail))
    say(f"OFFLOAD RESULT: {r.get('status')} on {r.get('host') or name}")
    return 0 if r.get("status") == "pass" else 3


def handle(args, main, say=None, err=None):
    """The command line's door. -> None to run `args` here as typed, else the exit code of what was done instead.
    `main` is cli.main (a dispatch re-enters it with the other command line)."""
    say = say or print
    err = err or (lambda s: print(s, file=sys.stderr))
    d = decide(args)
    js = "--json" in args
    a = d["action"]
    if a == "run":
        return None if d["args"] == list(args) else main(d["args"])
    if a == "dispatch":
        line = (f"DISPATCHED: `fieldkit {d['command']}` is the {_other_word()} route; on {_here_word()} the same job "
                f"is `fieldkit {' '.join(d['argv'])}` ({d['why']})")
        (err if js else say)(line)
        return main(d["argv"])
    if a == "offload":
        return offload(d["args"], d["device"], say=err if js else say)
    why = d["why"] or "the card names another system"
    if js:
        say(json.dumps({"ok": True, "status": "not-this-platform", "command": d["command"],
                        "platforms": d["platforms"], "why": why, "port": d["port"]}))
    else:
        say(f"not-this-platform: `fieldkit {d['command']}` runs on {', '.join(d['platforms'])} ({why}). Nothing to "
            f"do on this machine; the job carries on. To run it on the other machine: add --on peer.")
    return 0


def _here_word():
    from .host import this_platform
    return this_platform()


def _other_word():
    return "windows" if _here_word() != "windows" else "linux"


def table(index=None, rows=None):
    """-> the rows of docs/PLATFORMS.md: every single-system command, before (refused) and after (its route)."""
    from . import platform_gate
    index = platform_gate.load_index(local=False) if index is None else index
    rows = load() if rows is None else rows
    out = []
    for key, r in sorted(index.items()):
        other = _other(r["platforms"])
        if not other:
            continue
        rkey, row = row_for(key.split(), rows)
        route = (row or {}).get("route", "MISSING")
        alt = ""
        if row and route == "dispatch":
            alt = "; ".join(f"{p}: fieldkit {' '.join(w for w in v if w != '{args}')}"
                            for p, v in (row.get("dispatch") or {}).items())
        out.append({"command": key, "platforms": r["platforms"], "route": route, "dispatch": alt,
                    "port": bool((row or {}).get("port")), "why": (row or {}).get("why") or r.get("why") or ""})
    return out


def markdown(rows):
    after = {"dispatch": "runs the equivalent here", "offload": "runs on the other machine over the cable",
             "none": "not-this-platform (exit 0, the job carries on)", "MISSING": "NO ROW (test fails)"}
    lines = ["| command | card says | before (2026-11-11) | after | why |", "|---|---|---|---|---|"]
    for r in rows:
        a = after.get(r["route"], r["route"])
        if r["dispatch"]:
            a += f" ({r['dispatch']})"
        if r["port"]:
            a += "; PORT TO DO"
        lines.append(f"| `{r['command']}` | {', '.join(r['platforms'])} | REFUSED (exit 2) | {a} | "
                     f"{r['why'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fieldkit.core.crossos",
                                 description="the single-system commands and their route on the other system")
    ap.add_argument("--markdown", action="store_true", help="print the docs/PLATFORMS.md table")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = table()
    if a.json:
        print(json.dumps(rows, indent=1))
    elif a.markdown:
        print(markdown(rows), end="")
    else:
        for r in rows:
            print(f"{r['route']:9} {r['command']}  ({', '.join(r['platforms'])})")
        missing = [r for r in rows if r["route"] == "MISSING"]
        print(f"{len(rows)} single-system command(s), {len(missing)} with no row")
        print("NEXT: " + ("add the missing rows to fieldkit/core/crossos.yaml" if missing else
                          "python -m fieldkit.core.crossos --markdown > the table in docs/PLATFORMS.md"))
        return 3 if missing else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
