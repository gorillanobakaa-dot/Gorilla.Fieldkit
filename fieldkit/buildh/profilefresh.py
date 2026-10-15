# Version: 1.0.0 · updated 26-10-15-21-58
"""A fresh profile for a new Firefox version that carries the person's DATA and none of the old version's SETTINGS.

    fieldkit build-harness profile-fresh TASK from=<old profile> to=<new profile> [machine=<pref>=<value> ...] [--apply]

Born 2026-10-15. The owner ran Gorilla 158 on ~/.mozilla/ff154-main, a 154-era profile with a 196-line user.js and
saved settings that overrode the new build: the serif font saved as Inter (the "Inter, not Segoe" screenshot), the
recommended-stories feed saved on (the black new tab), six search engines saved on. The owner: "make sure that we only
keep the newest profile specifically tailored for the 158. Import everything that works from there and make sure it
does not impact the new 158".

Deterministic, no model:
  DATA      the files that hold the person's things (bookmarks, history, passwords, cookies, site permissions, form
            history): copied with SQLite's own backup (a consistent copy even with a -wal file beside it), the rest
            byte for byte
  NEVER     settings and caches of the old version (prefs.js, user.js, the search list, the toolbar layout, the
            add-on registry, startup caches, user stylesheets): never copied; a fresh profile takes the build's own
  stranded  every setting of the old user.js that the build does NOT give the same value: reported, so a real fix
            that lived only in a profile is moved into the build before the old profile is retired (PREF-001)
  machine   settings that belong to THIS machine, not the build (e.g. the VAIO's PipeWire loudness sink makes the
            browser's own compressor redundant): written into the new profile's user.js, each with its reason
Refuses when a browser is running from either profile, or when the new profile folder already exists and is not empty.
Nothing is deleted: the old profile stays where it is until a person retires it.
"""
import os
import re
import shutil
import sqlite3
from pathlib import Path

DATA = ("places.sqlite", "favicons.sqlite", "cookies.sqlite", "permissions.sqlite", "formhistory.sqlite",
        "logins.json", "key4.db", "logins-backup.json", "handlers.json")
NEVER = ("prefs.js", "user.js", "search.json.mozlz4", "xulstore.json", "extensions.json", "addonStartup.json.lz4",
         "compatibility.ini", "startupCache", "chrome", "times.json", "sessionstore.jsonlz4", "sessionstore-backups")
USER_PREF = re.compile(r'^\s*user_pref\("([^"]+)",\s*(.+?)\);')


def in_use(profile):
    """A running Firefox holds the profile: its `lock` link names a live process (host:+pid), or .parentlock is held.
    A link to a process that is gone is a leftover of an unclean exit, not a running browser (2026-10-15: the
    first version trusted the link and refused a closed profile)."""
    p = Path(profile)
    lock = p / "lock"
    if lock.is_symlink():
        m = re.search(r"\+(\d+)$", os.readlink(lock))
        if m and Path(f"/proc/{m.group(1)}").exists():
            return True
    return (p / ".parentlock").exists() and _locked(p / ".parentlock")


def _locked(path):
    import fcntl
    try:
        with open(path, "a") as f:
            fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.lockf(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def stranded(old_userjs, shipped):
    """Settings of the old user.js the build does not give the same value -> [(name, profile value, build value)]."""
    out = []
    p = Path(old_userjs)
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = USER_PREF.match(line)
        if not m:
            continue
        name, val = m.group(1), m.group(2).split("//")[0].strip()
        got = shipped.get(name)
        if got is None or str(got[0]).strip().strip('"') != val.strip('"'):
            out.append((name, val, None if got is None else got[0]))
    return out


def plan(src, dst):
    src, dst = Path(src), Path(dst)
    copy = [f for f in DATA if (src / f).is_file()]
    skipped = sorted(p.name for p in src.iterdir() if p.name in NEVER) if src.is_dir() else []
    return {"copy": copy, "never": skipped, "src": str(src), "dst": str(dst)}


def _sqlite_copy(a, b):
    with sqlite3.connect(f"file:{a}?mode=ro", uri=True) as s, sqlite3.connect(b) as d:
        s.backup(d)


def apply(src, dst, machine=None):
    """machine: {pref: (value, reason)} -> written into the new profile's user.js."""
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        raise FileNotFoundError(f"no profile at {src}")
    if in_use(src) or (dst.is_dir() and in_use(dst)):
        raise RuntimeError("a browser is running from one of the profiles: close it first")
    if dst.exists() and any(dst.iterdir()):
        raise RuntimeError(f"{dst} exists and is not empty: refusing to mix profiles")
    dst.mkdir(parents=True, exist_ok=True)
    os.chmod(dst, 0o700)
    done = []
    for f in plan(src, dst)["copy"]:
        if f.endswith(".sqlite"):
            _sqlite_copy(src / f, dst / f)
        else:
            shutil.copy2(src / f, dst / f)
        os.chmod(dst / f, 0o600)
        done.append(f)
    if machine:
        lines = ["// Settings for THIS machine, not the build (fieldkit build-harness profile-fresh, " +
                 "written once; the build's own defaults govern everything else)"]
        for name, (val, why) in machine.items():
            lines += [f"// {why}", f"user_pref({name!r}, {val});".replace("'", '"')]
        (dst / "user.js").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"copied": done, "dst": str(dst)}


def check(dst):
    """Right after apply (before the browser first starts and writes its own): the data is there and no old setting."""
    dst = Path(dst)
    problems = [f"{n} came across" for n in NEVER if (dst / n).exists() and n != "user.js"]
    for f in ("places.sqlite", "logins.json", "key4.db"):
        if not (dst / f).is_file():
            problems.append(f"{f} missing")
    return {"ok": not problems, "problems": problems}
