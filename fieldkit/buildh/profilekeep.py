# Version: 1.0.0 · updated 26-10-16-21-40
"""profile-keep: an install must open the profile that holds the user's saved logins (D-157-28).

2026-10-16, owner: "make sure that 158 does not erase our passwords and login details every time when you close the
browser". Measured that day: closing the browser erased nothing (the 157 profile kept 60 logins and 147 cookies across
restarts). What lost the logins was the install FOLDER: Firefox gives every install folder its own profile
(installs.ini, "dedicated profiles"), so 155.0.1 in Program Files and 157 in the user folder opened two different
profiles, and the second one started empty.

The profile an install folder opens is the one whose compatibility.ini LastPlatformDir is that folder (Firefox writes
it at every start). The keeper is the profile with the most saved logins (then cookies). A target folder that is not
the keeper's folder is refused: the new build would open a fresh profile and the user would see every login gone.
Counts only, read-only (sqlite immutable): no login, cookie or site is ever read or printed."""
import configparser
import json
import os
import sqlite3
from pathlib import Path


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p))) if p else ""


def _count(db, tables):
    if not db.is_file():
        return 0
    try:
        c = sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            have = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            return sum(c.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables if t in have)
        finally:
            c.close()
    except sqlite3.Error:
        return 0


def logins(profile):
    """Saved logins in a profile: logins.db (the newer store) or logins.json."""
    n = _count(profile / "logins.db", ("loginsL", "loginsM"))
    j = profile / "logins.json"
    if j.is_file():
        try:
            n = max(n, len(json.loads(j.read_text(encoding="utf-8")).get("logins") or []))
        except (OSError, ValueError):
            pass
    return n


def profiles(root):
    """-> [{"name", "path", "dir" (LastPlatformDir), "version", "logins", "cookies"}] from profiles.ini."""
    ini = Path(root) / "profiles.ini"
    if not ini.is_file():
        return []
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    cp.read(ini, encoding="utf-8")
    out = []
    for sec in cp.sections():
        if not sec.startswith("Profile") or "Path" not in cp[sec]:
            continue
        s = cp[sec]
        p = Path(root) / s["Path"] if s.get("IsRelative", "1") == "1" else Path(s["Path"])
        compat = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            compat.read(p / "compatibility.ini", encoding="utf-8")
            c = compat["Compatibility"] if compat.has_section("Compatibility") else {}
        except (configparser.Error, UnicodeDecodeError):
            c = {}
        out.append({"name": s.get("Name", p.name), "path": str(p), "dir": c.get("LastPlatformDir", ""),
                    "version": c.get("LastVersion", "").split("_")[0], "logins": logins(p),
                    "cookies": _count(p / "cookies.sqlite", ("moz_cookies",))})
    return out


def keeper(rows):
    """The profile holding the user's logins: most logins, then most cookies; None when none holds any."""
    rows = [r for r in rows if r["logins"] or r["cookies"]]
    return max(rows, key=lambda r: (r["logins"], r["cookies"])) if rows else None


def check(target, root):
    """-> {"check", "ok", "evidence", "keeper", "rows"}. Fail closed: a target that would open another profile than the
    one with the logins is refused; with no logins anywhere there is nothing to lose."""
    rows = profiles(root)
    k = keeper(rows)
    name = "the install folder opens the profile with the saved logins (D-157-28)"
    if not k:
        return {"check": name, "ok": True, "keeper": None, "rows": rows,
                "evidence": f"no profile under {root} holds logins or cookies: nothing to lose"}
    if not k["dir"]:
        return {"check": name, "ok": False, "keeper": k, "rows": rows,
                "evidence": f"profile {k['name']} holds {k['logins']} logins but names no install folder "
                            "(compatibility.ini LastPlatformDir): cannot prove which folder opens it"}
    ok = _norm(target) == _norm(k["dir"])
    other = next((x for x in rows if _norm(x["dir"]) == _norm(target)), None)
    opens = (f"profile {other['name']} ({other['logins']} logins, last {other['version']})" if other
             else "a NEW, empty profile")
    ev = (f"{target} opens profile {k['name']} ({k['logins']} logins, {k['cookies']} cookies, last {k['version']})" if ok
          else f"{target} would open {opens}: the {k['logins']} saved logins and {k['cookies']} cookies "
               f"are in profile {k['name']}, used by {k['dir']} (last {k['version']}). Install into {k['dir']}")
    return {"check": name, "ok": ok, "keeper": k, "rows": rows, "evidence": ev}


def lines(r):
    out = [f"  [{'ok' if r['ok'] else 'FAIL'}] {r['check']}: {r['evidence']}"]
    for x in r["rows"]:
        out.append(f"      {x['name']:<22} logins {x['logins']:>4}  cookies {x['cookies']:>5}  {x['version']:<10} {x['dir']}")
    return out
