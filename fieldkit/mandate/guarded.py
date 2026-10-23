# Version: 1.0.0 · updated 26-10-23-12-12
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


def _guarded_windows(p, acl_reader=None):
    # The Windows side's part (elevate kit). What it must check (Windows, link note #67): os.access(W_OK) sees only
    # the read-only attribute, never the ACL, so read the DACL (GetNamedSecurityInfo via ctypes, or icacls): owner
    # BUILTIN\Administrators or SYSTEM, and no ACE granting write/modify/full to the current user, Users,
    # Authenticated Users, Everyone or INTERACTIVE. acl_reader(path) -> the ACL, a seam for tests. Until it lands, a
    # Windows trust store is never treated as guarded: fail closed.
    return False, "the Windows guarded store is not built yet (the Windows side, M1.2): nothing in it counts"


def _write_windows(name, line, append):
    # The elevated step creates the folder with its ACL in one go (icacls /inheritance:r /grant:r Administrators:F
    # SYSTEM:F Users:RX; nothing written there unelevated, not even the first mkdir) and shows the fingerprint it pins:
    # the UAC prompt is the owner's act.
    raise NotImplementedError("the Windows guarded store is the Windows side's part (elevate kit), M1.2")
