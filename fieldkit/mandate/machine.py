"""Machine prerequisites: declared in the mandate, approved ONCE by the owner, applied by the harness at a session start.

    fieldkit mandate prereqs [check]          each prerequisite of the mandate(s) in force: present or missing
    fieldkit mandate prereqs apply            the missing ones: user-level ones at once, administrator ones in ONE
                                              elevated run (one UAC click); each script's sha256 checked first
    fieldkit mandate prereqs undo ID          one prerequisite reverted (same path; the owner's own decision)

Owner, 2026-10-26: "this click should go in AT THE BEGINING OF THE SESSION. into the mandate list of the
preapprovals. Android does that in their apps." A mandate may carry `machine_prerequisites:`; the approve and renew
screens list each one (what it changes, what it costs, the script and its sha256) beside the doors, and the owner's
typed yes + passphrase approves them with the rest. Nothing is asked again: `fieldkit mandate start` (a session's
startup check) applies what is missing, once.

An entry:
    - id: longpaths-enabled           # verify: the name of a read-only check below (VERIFY)
      os: windows                     # windows | linux | darwin | any
      admin: true                     # true: run elevated (one UAC click for all of them together)
      what: "..."                     # shown on the approve screen
      cost: "..."                     # who can do what through it afterwards
      script: fieldkit/mandate/prereqs/longpaths-enabled.ps1    # relative to the Fieldkit folder
      sha256: <hex>                   # the script must hash to this, or nothing runs
      apply: [-Action, apply]         # the script's arguments (or command: [argv] for a user-level command)
      undo:  [-Action, undo]
      verify: longpaths_enabled

What Windows does not let any program do: click its own UAC prompt. An administrator prerequisite still shows ONE
UAC prompt the first time it is applied (at a session start, never as a morning chore); the setting then persists
and the check finds it present, so it is never asked again. A broker that holds admin rights (the 2026-10-22
proposal) would remove that one click; it is not built.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ATTEMPT_EVERY_S = 12 * 3600          # a missing admin prerequisite pops the UAC prompt at most twice a day


def _root():
    from ..core import settings
    return settings.ROOT


def _state():
    d = _root() / "state" / "mandate-prereqs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- the read-only checks, by name -------------------------------------------------------------------------------
def _longpaths():
    from ..core import longpath
    return longpath.long_paths_enabled()


def _git_longpaths():
    from ..core import longpath
    return longpath.git_longpaths()


def _scratch_exclusion():
    from .. import scratch
    return scratch.exclusion()["present"]


VERIFY = {"longpaths_enabled": _longpaths, "git_longpaths": _git_longpaths, "scratch_exclusion": _scratch_exclusion}


def entries(m):
    return [e for e in (m.get("machine_prerequisites") or []) if isinstance(e, dict) and e.get("id")]


def here(e, platform=None):
    from ..core import longpath
    return e.get("os", "any") in ("any", longpath.system(platform))


def door_lines(m):
    """What the approve and renew screens show for each prerequisite."""
    out = []
    for e in entries(m):
        how = "ELEVATED (one UAC click the first time)" if e.get("admin") else "as you"
        src = f"script {e['script']} sha256 {str(e.get('sha256', '?'))[:16]}..." if e.get("script") else \
            f"command {' '.join(map(str, e.get('command') or []))}"
        out.append(f"MACHINE PREREQUISITE {e['id']} ({e.get('os', 'any')}, {how}): {e.get('what', '')}; COST: "
                   f"{e.get('cost', 'none stated')}; {src}; undo: fieldkit mandate prereqs undo {e['id']}")
    return out


def sha_ok(e, root=None):
    """-> (ok, why). A script entry must exist and hash to its pinned sha256; a command entry has nothing to pin."""
    if not e.get("script"):
        return bool(e.get("command")), "a command entry" if e.get("command") else "neither script nor command"
    p = Path(root or _root()) / e["script"]
    try:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError as x:
        return False, f"{e['script']}: {x}"
    if h != str(e.get("sha256", "")).lower():
        return False, f"{e['script']} hashes to {h[:16]}..., the mandate pins {str(e.get('sha256'))[:16]}...: refused"
    return True, "sha256 matches"


def check(m, verify=None):
    """-> [{id, present: True|False|None, admin, why}] for the entries of this OS."""
    verify = verify or VERIFY
    rows = []
    for e in entries(m):
        if not here(e):
            continue
        fn = verify.get(e.get("verify"))
        try:
            present = fn() if fn else None
        except Exception as x:          # a broken check is "unknown", never "present"
            present, why = None, f"check failed: {x}"
        else:
            why = "" if fn else f"no check named {e.get('verify')!r}"
        rows.append({"id": e["id"], "present": present, "admin": bool(e.get("admin")), "why": why})
    return rows


def _ps_quote(s):
    return "'" + str(s).replace("'", "''") + "'"


_PS_PARAM = re.compile(r"-[A-Za-z][A-Za-z0-9]*")


def _ps_arg(a):
    """A parameter name (-Action) goes bare, or PowerShell binds the quoted '-Action' as a positional VALUE
    (2026-10-26: the first real apply failed on ValidateSet that way); every value is single-quoted."""
    return str(a) if _PS_PARAM.fullmatch(str(a)) else _ps_quote(a)


def runner_text(todo, root=None, action="apply", marker="PREREQS: done"):
    """The ONE elevated script: each pinned script's sha256 checked again inside the elevated process, then run."""
    root = Path(root or _root())
    lines = ["$ErrorActionPreference = 'Stop'", "$failed = 0"]
    for e in todo:
        p = root / e["script"]
        args = " ".join(_ps_arg(a) for a in (e.get(action) or []))
        lines += [f"Write-Host 'PREREQ {e['id']}: {action}'",
                  # .NET, not Get-FileHash: that cmdlet lives in a module that fails to load when powershell.exe
                  # inherits PowerShell 7's PSModulePath (seen on the CI runner, 2026-10-26)
                  f"$h = [System.BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash("
                  f"[System.IO.File]::ReadAllBytes({_ps_quote(p)}))).Replace('-', '').ToLower()",
                  f"if ($h -ne {_ps_quote(str(e['sha256']).lower())}) {{ Write-Host 'PREREQ {e['id']}: FAILED sha256 "
                  f"changed, not run'; $failed++ }} else {{ try {{ & {_ps_quote(p)} {args} }} catch {{ "
                  f"Write-Host \"PREREQ {e['id']}: FAILED $_\"; $failed++ }} }}"]
    lines.append(f"Write-Host \"{marker} ($failed failed)\"")
    return "\r\n".join(lines) + "\r\n"


def _elevate_py():
    p = _root() / "local" / "kits" / "Elevate" / "elevate.py"
    return p if p.is_file() else None


def elevated(todo, action="apply", timeout_min=10, run=subprocess.run, say=print):
    """Write the runner, put ONE UAC prompt on screen through the elevate kit, wait for it. -> (ok, why)."""
    el = _elevate_py()
    if not el:
        return False, "the elevate kit (local/kits/Elevate/elevate.py) is not on this machine"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    script = _state() / f"runner-{stamp}.ps1"
    log = _state() / f"runner-{stamp}.log"
    script.write_text(runner_text(todo, action=action), encoding="utf-8")
    say(f"  one UAC prompt for: {', '.join(e['id'] for e in todo)} (the owner approved these in the mandate)")
    r = run([sys.executable, str(el), "run", str(script), "--log", str(log), "--close"], capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"elevate run: exit {r.returncode} {(r.stderr or r.stdout).strip()[:200]}"
    w = run([sys.executable, str(el), "wait", str(log), "--marker", "ELEVATE: script finished",
             "--timeout", str(timeout_min)], capture_output=True, text=True)
    return w.returncode == 0, ("finished" if w.returncode == 0 else
                               f"not finished in {timeout_min} min (UAC not answered?): exit {w.returncode}")


def _attempted_recently(now=None):
    p = _state() / "last-attempt.json"
    try:
        return (now or time.time()) - json.loads(p.read_text(encoding="utf-8"))["t"] < ATTEMPT_EVERY_S
    except (OSError, ValueError, KeyError):
        return False


def _mark_attempt(now=None):
    (_state() / "last-attempt.json").write_text(json.dumps({"t": now or time.time()}), encoding="utf-8")


def apply(m, in_force, verify=None, run=subprocess.run, elevate_fn=None, say=print, force=False, now=None, nt=None):
    """Apply the missing prerequisites of an IN-FORCE mandate. -> (code, lines). 0 all present, 3 something missing."""
    if not in_force:
        return 3, ["PREREQS: no mandate in force: nothing is applied (the owner approves them in the mandate)"]
    rows = check(m, verify)
    missing = [r["id"] for r in rows if r["present"] is not True]
    if not missing:
        return 0, [f"PREREQS: all {len(rows)} present"]
    by_id = {e["id"]: e for e in entries(m)}
    out, todo_admin = [], []
    for i in missing:
        e = by_id[i]
        ok, why = sha_ok(e)
        if not ok:
            out.append(f"  {i}: NOT applied: {why}")
            continue
        if e.get("admin"):
            todo_admin.append(e)
            continue
        cmd = [shutil.which(e["command"][0]) or e["command"][0]] + [str(x) for x in e["command"][1:]]
        r = run(cmd, capture_output=True, text=True)
        out.append(f"  {i}: {'applied' if r.returncode == 0 else 'FAILED exit ' + str(r.returncode)} (as you)")
    if todo_admin:
        if not ((os.name == "nt") if nt is None else nt):
            out.append(f"  {', '.join(e['id'] for e in todo_admin)}: administrator entries run on Windows only")
        elif _attempted_recently(now) and not force:
            out.append(f"  {', '.join(e['id'] for e in todo_admin)}: the UAC prompt was put up less than "
                       f"{ATTEMPT_EVERY_S // 3600} h ago; next session start (or prereqs apply --force)")
        else:
            _mark_attempt(now)
            ok, why = (elevate_fn or elevated)(todo_admin, say=say)
            out.append(f"  elevated run: {why}")
    after = check(m, verify)
    still = [r["id"] for r in after if r["present"] is not True]
    out.append(f"PREREQS: {len(after) - len(still)} of {len(after)} present"
               + (f"; missing: {', '.join(still)}" if still else ""))
    return (0 if not still else 3), out


def undo(m, ident, run=subprocess.run, elevate_fn=None, say=print):
    e = {x["id"]: x for x in entries(m)}.get(ident)
    if not e:
        return 2, [f"no prerequisite {ident} in the mandate"]
    ok, why = sha_ok(e)
    if not ok:
        return 3, [f"{ident}: NOT undone: {why}"]
    if e.get("admin"):
        ok, why = (elevate_fn or elevated)([e], action="undo", say=say)
        return (0 if ok else 3), [f"{ident}: elevated undo {why}"]
    cmd = [str(x) for x in (e.get("undo_command") or [])]
    if not cmd:
        return 2, [f"{ident}: no undo_command"]
    r = run([shutil.which(cmd[0]) or cmd[0]] + cmd[1:], capture_output=True, text=True)
    return (0 if r.returncode == 0 else 3), [f"{ident}: undo exit {r.returncode}"]


def start_lines(verify=None, apply_fn=None):
    """`fieldkit mandate start`: apply what is missing from the mandates in force (FIELDKIT_PREREQS=0 skips)."""
    if os.environ.get("FIELDKIT_PREREQS") == "0":
        return []
    from . import mandate as M
    out = []
    for p in M.in_force():
        try:
            m = M.load(p)
        except (OSError, ValueError):
            continue
        if not entries(m):
            continue
        _code, lines = (apply_fn or apply)(m, True, verify)
        out += [f"{p.stem} {lines[-1]}"] + [line for line in lines[:-1]]
    return out


def main(r, args, say=print):
    from . import mandate as M
    sub = r[0] if r else "check"
    force = M.in_force()
    ms = []
    for p in force:
        try:
            ms.append((p, M.load(p)))
        except (OSError, ValueError):
            pass
    ms = [(p, m) for p, m in ms if entries(m)]
    if not ms:
        say("PREREQS: no mandate in force here lists machine_prerequisites")
        return 3
    code = 0
    for p, m in ms:
        if sub == "check":
            for row in check(m):
                st = {True: "PRESENT", False: "MISSING", None: "UNKNOWN"}[row["present"]]
                say(f"  {st:<8} {row['id']:<28} {'admin' if row['admin'] else 'user '} {row['why']}")
                code = max(code, 0 if row["present"] else 3)
        elif sub == "apply":
            c, lines = apply(m, True, force="--force" in args, say=say)
            for line in lines:
                say(line)
            code = max(code, c)
        elif sub == "undo" and len(r) >= 2:
            c, lines = undo(m, r[1], say=say)
            for line in lines:
                say(line)
            code = max(code, c)
        else:
            say("prereqs [check] | apply [--force] | undo ID")
            return 2
    return code
