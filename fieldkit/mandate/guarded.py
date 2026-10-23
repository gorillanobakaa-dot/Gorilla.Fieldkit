# Version: 1.1.0 · updated 26-10-23-18-38
"""The mandate's trust store and stop list, where the agent's own OS user cannot write.

The Windows review of M1.1 (2026-10-23): the hard lines read COMMAND TEXT, and a model runs code as the same OS user,
so a write by indirection (a script in scratch, Path.home() / ".fieldkit" / "mandate", an environment variable built
from pieces) reached the trusted keys, the stop list and the log. A regex cannot close that; the OS can. What decides
trust lives here:

    trusted/<fp>.pub      the owner's public signing key(s)                     (fieldkit mandate trust)
    reviewers/<fp>.pub    the other computer's REVIEW key (it signs APPROVE)   (fieldkit mandate trust-reviewer)
    stopped.txt           sha256 of every mandate the owner stopped            (fieldkit mandate stop)

Linux and macOS: /etc/fieldkit/mandate-trust, owned by root, written only through pkexec (Linux) or sudo (macOS), so
every write is the owner typing their password. Windows: %ProgramData%\\Fieldkit\\mandate-trust with an
Administrators-only write ACL, written through the elevate kit (the Windows side's part, stubbed below).

A reader refuses a root this user can write (`guarded()`): if the protection is not there, nothing in it counts.
"""
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HEXLINE = re.compile(r"[0-9a-f]{64}( [^\n]{0,200})?")
NAME = re.compile(r"(trusted|reviewers)/[0-9a-f]{4}(-[0-9a-f]{4}){3}\.pub|stopped\.txt")


def root():
    if sys.platform == "win32":
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Fieldkit" / "mandate-trust"
    return Path("/etc/fieldkit/mandate-trust")


def guarded(path=None):
    """-> (ok, why): the root (or a file in it) exists and this OS user cannot change it."""
    p = Path(path) if path else root()
    if sys.platform == "win32":
        return _guarded_windows(p)
    if not p.exists():
        return False, f"{p} does not exist (the owner sets it up: fieldkit mandate trust)"
    for q in [p] + ([] if p == root() else [root()]):
        st = q.stat()
        if st.st_uid != 0 or os.access(q, os.W_OK) or st.st_mode & 0o022:
            return False, f"{q} can be changed without the owner's password (owner uid {st.st_uid}, " \
                          f"mode {oct(st.st_mode & 0o777)}): nothing in it counts"
    return True, "guarded"


def read(name):
    """The text of a guarded file, or "" when it is absent or not guarded."""
    p = root() / name
    if not p.is_file() or not guarded(p)[0]:
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def listing(folder):
    d = root() / folder
    if not d.is_dir() or not guarded(d)[0]:
        return []
    return sorted(q for q in d.glob("*.pub") if guarded(q)[0])


def write(name, line, append=False, run=subprocess.run):
    """Write one line to a guarded file, as the owner (a password prompt). `name` and `line` are validated: hex and a
    short note only, so nothing reaches the privileged shell but what is shown here."""
    if not NAME.fullmatch(name) or not HEXLINE.fullmatch(line) or "'" in line:
        raise ValueError(f"refused: {name!r} / {line!r} is not a trust-store line")
    if sys.platform == "win32":
        return _write_windows(name, line, append)
    target = root() / name
    script = (f"umask 022; mkdir -p {shlex.quote(str(target.parent))} && "
              f"printf '%s\\n' {shlex.quote(line)} {'>>' if append else '>'} {shlex.quote(str(target))}")
    lift = ["pkexec"] if sys.platform.startswith("linux") else ["sudo"]
    r = run(lift + ["/bin/sh", "-c", script])
    if getattr(r, "returncode", 1) != 0:
        return False, "not written (the password was not given, or the write failed)"
    # the artefact: what is there now, read back, is what was meant
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    ok = line in text.splitlines() and guarded(target)[0]
    return ok, (f"written: {target}" if ok else f"{target} does not hold the line, or is not guarded")


# ---- Windows: %ProgramData%\Fieldkit\mandate-trust, Administrators-only write (owner, 2026-10-23: "construieste
# partea windows a folderului protejat"; the Windows side's facts, link note #67) ------------------------------------
ADMINS, SYSTEM_ = ("BUILTIN\\Administrators", "S-1-5-32-544"), ("NT AUTHORITY\\SYSTEM", "S-1-5-18")
WIDE = ("BUILTIN\\Users", "S-1-5-32-545", "NT AUTHORITY\\Authenticated Users", "S-1-5-11", "Everyone", "S-1-1-0",
        "NT AUTHORITY\\INTERACTIVE", "S-1-5-4")
WRITE_RIGHTS = ("FullControl", "Modify", "Write", "CreateFiles", "CreateDirectories", "AppendData", "Delete",
                "ChangePermissions", "TakeOwnership")


def acl_verdict(acl, me=None):
    """acl: {"owner": str, "access": ["IDENTITY|RIGHTS|Allow or Deny", ...]} (Get-Acl, as the reader returns it).
    -> (ok, why). os.access(W_OK) on Windows sees only the read-only attribute, never the ACL (Windows, #67), so the
    ACL itself decides: owned by Administrators or SYSTEM, and no Allow entry giving write to this user, Users,
    Authenticated Users, Everyone or INTERACTIVE."""
    owner = str(acl.get("owner") or "")
    if not any(owner.lower() == x.lower() for x in ADMINS + SYSTEM_):
        return False, f"owned by {owner or 'nobody known'}, not Administrators or SYSTEM"
    who = [w.lower() for w in WIDE + ((me,) if me else ())]
    for entry in acl.get("access") or []:
        ident, _, rest = str(entry).partition("|")
        rights, _, kind = rest.partition("|")
        if kind.strip().lower() != "allow" or ident.strip().lower() not in who:
            continue
        if any(r.lower() in rights.lower() for r in WRITE_RIGHTS):
            return False, f"{ident.strip()} may write ({rights.strip()})"
    return True, "Administrators-only write"


def _read_acl(p, run=subprocess.run):
    ps = ("$a = Get-Acl -LiteralPath $env:FK_PATH; [pscustomobject]@{owner=$a.Owner; access=@($a.Access | "
          "ForEach-Object { $_.IdentityReference.Value + '|' + $_.FileSystemRights + '|' + $_.AccessControlType })} | "
          "ConvertTo-Json -Compress")
    import json
    import os
    r = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
            env=dict(os.environ, FK_PATH=str(p)), timeout=60)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def _guarded_windows(p, acl_reader=None):
    import os
    me = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    for q in [p] + ([] if p == root() else [root()]):
        if not Path(q).exists():
            return False, f"{q} does not exist (the owner sets it up: fieldkit mandate trust, one UAC click)"
        try:
            acl = (acl_reader or _read_acl)(q)
        except Exception as e:  # noqa: BLE001 - unreadable: fail closed
            return False, f"the ACL of {q} could not be read ({e}): nothing in it counts"
        if not acl:
            return False, f"the ACL of {q} could not be read: nothing in it counts"
        ok, why = acl_verdict(acl, me)
        if not ok:
            return False, f"{q} can be changed without elevation: {why}: nothing in it counts"
    return True, "guarded"


def ps1_for(target_root, name, line, append):
    """The elevated script: creates the folder WITH its ACL before anything is written (nothing unelevated touches
    it, not even the first mkdir; Windows, #67), shows what it pins (the UAC prompt is the owner's act), writes one
    validated line. SIDs, not names, so a Windows in another language works."""
    return "\r\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$root = '{target_root}'",
        "if (-not (Test-Path -LiteralPath $root)) {",
        "  New-Item -ItemType Directory -Path $root -Force | Out-Null",
        "  icacls $root /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "
        "'*S-1-5-32-545:(OI)(CI)RX' | Out-Null",
        "}",
        f"$t = Join-Path $root '{name.replace('/', chr(92))}'",
        "New-Item -ItemType Directory -Path (Split-Path $t) -Force | Out-Null",
        f"Write-Host 'Fieldkit pins into the protected store: {name} = {line[:16]}...'",
        ("Add-Content" if append else "Set-Content") + f" -LiteralPath $t -Value '{line}' -Encoding ascii",
        "Write-Host 'FIELDKIT-GUARDED-DONE'",
        ""])


def _elevate_kit():
    return Path.home() / "Documents" / "Scripts" / "Elevate" / "elevate.py"


def _write_windows(name, line, append, run=subprocess.run):
    """Through the elevate kit: one UAC Yes from the owner, then read back and checked like Linux."""
    import tempfile
    kit = _elevate_kit()
    if not kit.is_file():
        return False, f"the elevate kit is not on this computer ({kit})"
    d = Path(tempfile.mkdtemp(prefix="fk-guarded-"))
    ps1, log = d / "pin.ps1", d / "pin.log"
    ps1.write_text(ps1_for(str(root()), name, line, append), encoding="utf-8")
    run([sys.executable, str(kit), "run", str(ps1), "--log", str(log)])
    w = run([sys.executable, str(kit), "wait", str(log), "--marker", "FIELDKIT-GUARDED-DONE", "--timeout", "15"])
    if getattr(w, "returncode", 1) != 0:
        return False, f"not written: the elevated step did not finish (UAC declined, or see {log})"
    target = root() / name
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    ok = line in text.splitlines() and guarded(target)[0]
    return ok, (f"written: {target}" if ok else f"{target} does not hold the line, or is not guarded")
