"""Run a Windows 11 tweaks module the way an agent can rely on: check, preview, apply, undo.

    fieldkit win11 run MODULE check|preview|apply|undo [--PARAM VALUE ...] [--repo PATH]

Why not just call the scripts: a module that needs administrator rights refuses when it is not elevated (exit 4,
nothing changed), and its double-click launchers elevate by opening a NEW window - so a caller sees the first
process end long before the change is made, and never sees its output. Here the elevated run is waited for
(Start-Process -Verb RunAs -Wait), its output is captured to a file and printed, and its exit code is returned.
Windows still shows its own "allow this app to make changes" prompt: that is the owner's consent, every time.

Exit codes follow the framework's own contract (MODULE-STANDARD.md R16): its 0 is 0; its 3 (no verified backup,
refused), 4 (nothing to do, or not elevated) and 6 (a safety stop) all mean NOTHING WAS CHANGED, and are reported
here as 3; anything else is 1. preview never elevates (R6.16). Windows only.
"""
import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from ..core import settings

HERE = Path(__file__).resolve().parent
UNCHANGED = {3: "refused: no verified backup could be written", 4: "nothing to do (or not elevated)",
             6: "stopped by a safety check"}


def modules():
    return {m["id"]: m for m in yaml.safe_load((HERE / "modules.yaml").read_text(encoding="utf-8"))["modules"]}


def repo_dir(repo=None):
    return Path(repo or os.environ.get("FIELDKIT_WIN11") or settings.ROOT / "toolbox" / "windows-11-tweaks")


def ps_args(module, values):
    """The change script's PowerShell arguments from Fieldkit's --param values."""
    out = []
    for p in module.get("params") or []:
        v = values.get(p["name"])
        if v in (None, False, ""):
            if p.get("required"):
                raise ValueError(f"--{p['name'].replace('_', '-')} is required for {module['id']}")
            continue
        if p.get("switch"):
            out.append(f"-{p['ps']}")
        else:
            if p.get("choices") and str(v) not in [str(c) for c in p["choices"]]:
                raise ValueError(f"--{p['name'].replace('_', '-')} must be one of {', '.join(map(str, p['choices']))}")
            out += [f"-{p['ps']}", str(v)]
    return out


def command(module, action, values, repo=None):
    """-> (script argv, needs elevation)."""
    folder = repo_dir(repo) / "modules" / module["folder"]
    if action == "check":
        script, args, elevate = module["check"][0], list(module["check"][1:]), False
    elif action in ("preview", "apply"):
        script, args = module["apply"], ps_args(module, values) + (["-WhatIf"] if action == "preview" else [])
        elevate = action == "apply" and module.get("admin", False)
    elif action == "undo":
        script, args, elevate = module["undo"][0], list(module["undo"][1:]), module.get("admin", False)
    else:
        raise ValueError(f"unknown action {action!r}")
    path = folder / script
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)] + args, elevate


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _quote(a):
    return "'" + str(a).replace("'", "''") + "'"


def elevated_wrapper(argv, outfile):
    """A PowerShell command that runs argv elevated, waits, and leaves its output in outfile and its exit code as ours."""
    inner = f"& {_quote(argv[0])} {' '.join(_quote(a) for a in argv[1:])} *> {_quote(outfile)}; exit $LASTEXITCODE"
    return ["powershell", "-NoProfile", "-Command",
            "$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
            f"-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-Command',{_quote(inner)}); exit $p.ExitCode"]


def run(module_id, action, values=None, repo=None, runner=subprocess.run, admin=None):
    """-> (exit code for Fieldkit, text). runner and admin are for tests."""
    mods = modules()
    if module_id not in mods:
        raise ValueError(f"unknown module {module_id!r}: {', '.join(mods)}")
    m = mods[module_id]
    argv, elevate = command(m, action, values or {}, repo)
    if not Path(argv[5]).is_file():
        raise FileNotFoundError(f"{argv[5]} is not here: fieldkit gather --only windows-11-tweaks, or --repo PATH")
    if elevate and not (is_admin() if admin is None else admin):
        fd, out = tempfile.mkstemp(prefix="fieldkit-win11-", suffix=".txt")
        os.close(fd)
        try:
            r = runner(elevated_wrapper(argv, out), capture_output=True, text=True)
            text = Path(out).read_text(encoding="utf-8", errors="replace") if Path(out).is_file() else ""
            text = (text + (r.stderr or "")).strip()
        finally:
            try:
                os.unlink(out)
            except OSError:
                pass
        code = r.returncode
    else:
        r = runner(argv, capture_output=True, text=True)
        code, text = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    if action == "check":
        return (0 if code == 0 else 1), text
    if code == 0:
        return 0, text
    if code in UNCHANGED:
        return 3, text + f"\nFIELDKIT: the script exited {code} - {UNCHANGED[code]}. Nothing was changed."
    return 1, text + f"\nFIELDKIT: the script exited {code}: a failure. Run check to see the state."
