"""fieldkit win11: the Windows 11 tweaks framework (gorillanobakaa-dot/windows.11.tweaks.optimisation), driven safely.

    fieldkit win11 modules                         the ten modules: is each complete (check, change, undo)?
    fieldkit win11 fit [--profile light|moderate|super]   which service-profile entries would be WRONG on this PC
    fieldkit win11 oem                             the maker, and its hardware-button services (Lenovo: protected)
    fieldkit win11 run MODULE check|preview|apply|undo [--PARAM VALUE]   one module, safely (see run.py)

The repository is used where Fieldkit keeps it (fieldkit gather -> toolbox/windows-11-tweaks), or --repo PATH /
FIELDKIT_WIN11. It is never changed: the ThinkPad it was measured on keeps its profiles, and fit tells any other
computer which entries do not hold there. Exit 0 fine, 3 findings (or nothing changed), 2 bad input, 1 failure.
"""
import argparse
import sys
from pathlib import Path

from ..method import emit
from . import fit as fitmod
from . import oem as oemmod
from . import run as runmod


def _modules_dir(a):
    d = runmod.repo_dir(a.repo) / "modules"
    if not d.is_dir():
        raise FileNotFoundError(f"no framework at {d.parent}: fieldkit gather --only windows-11-tweaks, or --repo PATH")
    return d


def conformance(mdir):
    """Each module against MODULE-STANDARD R2.1: its check, its change and its undo exist; the change can preview."""
    rows = []
    for m in runmod.modules().values():
        folder = mdir / m["folder"]
        files = {"check": folder / m["check"][0], "change": folder / m["apply"], "undo": folder / m["undo"][0]}
        missing = [k for k, p in files.items() if not p.is_file()]
        preview = files["change"].is_file() and bool(
            __import__("re").search(r"SupportsShouldProcess|\$WhatIf", files["change"].read_text(errors="replace")))
        rows.append({"module": m["id"], "folder": m["folder"], "missing": missing, "preview": preview,
                     "admin": m.get("admin", False), "title": m["title"]})
    return rows


def main(argv=None, prog="fieldkit win11"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="the framework's folder (default: Fieldkit's toolbox copy)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("modules", help="the modules and whether each is complete")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("fit", help="service-profile entries that would be wrong on this computer (read-only)")
    s.add_argument("--profile", choices=fitmod.ORDER, default="super")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("oem", help="the maker and its hardware-button services (read-only)")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("run", help="one module: check, preview, apply or undo")
    r.add_argument("module", choices=list(runmod.modules()))
    r.add_argument("action", choices=["check", "preview", "apply", "undo"])
    for name in sorted({p["name"] for m in runmod.modules().values() for p in m.get("params") or []}):
        spec = next(p for m in runmod.modules().values() for p in m.get("params") or [] if p["name"] == name)
        flag = "--" + name.replace("_", "-")
        if spec.get("switch"):
            r.add_argument(flag, dest=name, action="store_true", help=spec["help"])
        else:
            r.add_argument(flag, dest=name, help=spec["help"])
    r.add_argument("--preview", action="store_true", help="show what apply would change, changing nothing (-WhatIf)")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "run":
            if sys.platform != "win32":
                print("REFUSED: the Windows 11 framework runs on Windows only")
                return 2
            mod = runmod.modules()[a.module]
            values = {p["name"]: getattr(a, p["name"], None) for p in mod.get("params") or []}
            stray = sorted(n for n in {p["name"] for m in runmod.modules().values() for p in m.get("params") or []}
                           if getattr(a, n, None) not in (None, False) and n not in values)
            if stray:
                print(f"REFUSED: {a.module} has no {', '.join('--' + s.replace('_', '-') for s in stray)}")
                return 2
            action = "preview" if a.preview and a.action == "apply" else a.action
            code, text = runmod.run(a.module, action, values, a.repo)
            print(text)
            return code
        mdir = _modules_dir(a)
        if a.cmd == "modules":
            rows = conformance(mdir)
            bad = [x for x in rows if x["missing"] or not x["preview"]]
            emit({"ok": not bad, "modules": rows, "next": "fieldkit win11 fit" if not bad else
                  "a module without its check, preview or undo must not be run"}, a.json,
                 lambda d: [f"{'ok  ' if not x['missing'] and x['preview'] else 'BAD '} {x['module']:20} "
                            f"{'admin' if x['admin'] else 'user ':5}  {x['title']}"
                            + (f"  missing: {', '.join(x['missing'])}" if x["missing"] else "")
                            + ("" if x["preview"] else "  no -WhatIf preview") for x in d["modules"]])
            return 0 if not bad else 3
        if a.cmd == "fit":
            if sys.platform != "win32":
                print("REFUSED: fit reads this computer's state: run it on the Windows machine")
                return 2
            r = fitmod.fit(mdir / "06-services" / "profiles.json", a.profile)
            r["ok"] = not r["findings"]
            r["next"] = ("" if r["ok"] else f"leave these out of the {a.profile} profile on this computer, or use a "
                         "lower profile")
            emit(r, a.json, lambda d: [f"{d['checked']} machine-specific entries of {d['profile']} checked here"]
                 + [f"WRONG HERE  {x['service']} ({x['tier']}): the profile assumes {x['assumes']}"
                    for x in d["findings"]]
                 + [f"check it    {x['service']} ({x['tier']}): {x['assumes']}? (could not tell)" for x in d["unknown"]])
            return 0 if r["ok"] else 3
        if a.cmd == "oem":
            if sys.platform != "win32":
                print("REFUSED: oem reads this computer: run it on the Windows machine")
                return 2
            r = oemmod.check(mdir)
            r["ok"] = "finding" not in r
            emit(r, a.json, lambda d: [f"maker: {d['maker']}", f"hardware-button services here: "
                                       f"{', '.join(d['present']) or 'none'}"]
                 + ([f"FINDING: {d['finding']}"] if d.get("finding") else [])
                 + ([f"note: {d['note']}"] if d.get("note") else []))
            return 0 if r["ok"] else 3
    except (ValueError, FileNotFoundError) as e:
        print(f"REFUSED: {e}")
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
