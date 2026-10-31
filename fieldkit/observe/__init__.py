# Version: 1.0.0 · updated 26-10-31
"""observe: see inside the machine. One question, answered natively on Linux, Windows and macOS. Read-only.

    fieldkit observe open-file PATH        who has this file open / locked (so it cannot be deleted)
    fieldkit observe port N                who uses port N, with the process
    fieldkit observe proc PID              exe, cwd, command line, user, children, memory map
    fieldkit observe watch DIR SECONDS     what changes under DIR
    fieldkit observe trace PID SECONDS     files and programs PID opens or starts (needs admin: it says so, asks nothing)
    fieldkit observe usage PID SECONDS     CPU, memory and disk of PID over time
    fieldkit observe events WORDS ...      system log lines holding every word

Readers (one module per system, each returns None on any other system): linux.py (/proc, inotify, strace,
journalctl), windows.py (Restart Manager, GetExtendedTcpTable, Toolhelp, ReadDirectoryChangesW, Get-WinEvent;
kits/wintrace), macos.py (lsof, vmmap, fs_usage, log show). Owner 2026-10-31 (I-07, plan row P10): a Qwen model could
not delete a file because something held it. Exit 0 answered, 1 error or nothing could be read, 3 needs admin.
"""
import argparse
import importlib
import json
import sys

# question -> reader function name and the arguments it takes
QUESTIONS = {"open-file": ("open_file", ["path"]), "port": ("port", ["n"]), "proc": ("proc", ["pid"]),
             "watch": ("watch", ["directory", "seconds"]), "trace": ("trace", ["pid", "seconds"]),
             "usage": ("usage", ["pid", "seconds"]), "events": ("events", ["words"])}
SYSTEMS = {"linux": "linux", "windows": "windows", "macos": "macos"}


def reader(system=None):
    """The reader module for SYSTEM (default: this one)."""
    if system is None:
        system = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    return importlib.import_module(f"{__name__}.{SYSTEMS[system]}")


def ask(question, *args, system=None):
    """Run QUESTION on this system's reader. Returns the reader's answer (None when the reader is for another system)."""
    return getattr(reader(system), QUESTIONS[question][0])(*args)


def _show(question, answer):
    if isinstance(answer, list):
        answer = {"rows": answer}
    for k, v in answer.items():
        if isinstance(v, list):
            print(f"{k}: {len(v)}")
            for item in v[:200]:
                print("  " + (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)))
        elif v is not None:
            print(f"{k}: {v}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit observe", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="question", required=True)
    for q, (_, params) in QUESTIONS.items():
        sp = sub.add_parser(q)
        for p in params:
            sp.add_argument(p, nargs="+" if p == "words" else None)
        sp.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    q = ns.question
    args = [getattr(ns, p) for p in QUESTIONS[q][1]]
    args = [float(a) if p == "seconds" else int(a) if p in ("pid", "n") else a for a, p in zip(args, QUESTIONS[q][1])]
    try:
        answer = ask(q, *args)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"ERROR: {e}\nNEXT: check the argument; nothing was changed")
        return 1
    if answer is None:
        print(f"NOT-THIS-PLATFORM: no reader for {sys.platform}\nNEXT: none")
        return 1
    if ns.json or getattr(ns, "json", False):
        print(json.dumps(answer, ensure_ascii=False, default=str))
    else:
        _show(q, answer)
    if isinstance(answer, dict) and answer.get("needs_admin"):
        print(f"NEEDS-ADMIN: {answer['error']}\nNEXT: run the same command as an administrator or root")
        return 3
    if isinstance(answer, dict) and answer.get("error"):
        print(f"ERROR: {answer['error']}\nNEXT: fix that, then run it again")
        return 1
    n = len(answer) if isinstance(answer, list) else sum(len(v) for v in answer.values() if isinstance(v, list))
    print(f"VERDICT: {q} answered ({n} rows)\nNEXT: act on the process by PID, never by name")
    return 0
