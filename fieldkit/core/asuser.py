"""An elevated fieldkit acts for the person who invoked it (P27, 2026-11-13).

`sudo fieldkit ...` runs with HOME=/root, so Path.home(), settings (${HOME}, ${DOCUMENTS}, ${LOCAL:...}) and the vault
resolved under /root: `leakgate firefox-160-linux` looked for the .deb in /root and refused. And every file written
under state/ came out root-owned, locking the owner out of their own state.

adopt() runs once at start (cli.main). Linux/macOS: euid 0 and SUDO_USER set (sudo sets it; a root login has none, so
nothing changes) -> HOME, USER, LOGNAME point at that user's home (pwd database, not the environment), so children
inherit it too; at exit every root-owned file or folder created or changed since start under the user's state/ and
fieldkit cache folders is chowned back to SUDO_UID:SUDO_GID (symlinks never followed).
Windows: an elevated run by the same account keeps its profile, nothing to do. An elevated run by ANOTHER account
(over-the-shoulder UAC) gets the administrator's profile; the invoking user is known from where the Fieldkit checkout
lives (the profile folder under Users): when the profile in USERPROFILE is a different one, USERPROFILE and HOME are set to it.
"""
import atexit
import os
import sys
import time
from pathlib import Path

_STATE = {"uid": None, "gid": None, "since": 0.0, "roots": []}


def _is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _invoker(env):
    """(name, uid, gid, home) of the user who ran sudo, or None."""
    name = env.get("SUDO_USER")
    if not name or name == "root":
        return None
    try:
        import pwd
        pw = pwd.getpwnam(name)
    except (ImportError, KeyError):
        return None
    if pw.pw_uid == 0:
        return None
    return name, pw.pw_uid, pw.pw_gid, pw.pw_dir


def windows_profile(checkout, userprofile):
    """The profile folder that owns `checkout` when it differs from `userprofile`, else None (pure, testable)."""
    parts = str(checkout).replace("\\", "/").split("/")
    for i, p in enumerate(parts[:-1]):
        if p.lower() == "users" and i >= 1 and parts[i + 1]:
            prof = "/".join(parts[:i + 2])
            if str(userprofile).replace("\\", "/").rstrip("/").lower() == prof.rstrip("/").lower():
                return None
            return prof.replace("/", "\\")
    return None


def adopt(env=None, root=None, now=None):
    """Point HOME at the invoking user when elevated. Returns the home it set, or None. `env` defaults to os.environ."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        prof = windows_profile(root or Path(__file__).resolve(), env.get("USERPROFILE", ""))
        if prof and env is os.environ and _windows_admin():
            env["USERPROFILE"] = env["HOME"] = prof
            return prof
        return None
    if not _is_root():
        return None
    who = _invoker(env)
    if who is None:
        return None
    name, uid, gid, home = who
    env["HOME"], env["USER"], env["LOGNAME"] = home, name, name
    for key in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        if env.get(key, "").startswith("/root"):
            del env[key]
    _STATE.update(uid=uid, gid=gid, since=(time.time() if now is None else now) - 2,
                  roots=_chown_roots(home, root))
    atexit.register(restore_ownership)
    return home


def _windows_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def _chown_roots(home, root=None):
    from . import settings
    st = Path(root) if root else settings.state_root()
    cands = [st / "state", Path(home) / ".cache" / "fieldkit-scratch", Path(home) / ".cache" / "fieldkit",
             Path(home) / ".config" / "fieldkit", Path(home) / ".local" / "share" / "fieldkit"]
    return [c for c in dict.fromkeys(cands) if c.exists()]


def restore_ownership(uid=None, gid=None, since=None, roots=None, owned_by=0):
    """chown back to the invoking user every root-owned entry changed since start. Returns how many it fixed."""
    uid = _STATE["uid"] if uid is None else uid
    gid = _STATE["gid"] if gid is None else gid
    since = _STATE["since"] if since is None else since
    roots = _STATE["roots"] if roots is None else roots
    if uid is None or not _is_root():
        return 0
    fixed = 0

    def fix(p):
        nonlocal fixed
        try:
            s = os.lstat(p)
            if s.st_uid == owned_by and s.st_ctime >= since:
                os.lchown(p, uid, gid)
                fixed += 1
        except OSError:
            pass

    for r in roots:
        fix(r)
        for dirpath, dirs, files in os.walk(r, followlinks=False):
            for n in dirs + files:
                fix(os.path.join(dirpath, n))
    return fixed
