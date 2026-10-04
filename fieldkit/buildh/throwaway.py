"""Throwaway browser profiles for the harness's headless checks (proof, leaks, capture, startup errors).

Each is made by `profile()` in the system temp folder with a known prefix and a marker file, and removed by
`discard()` after the run. `discard()` deletes ONLY a folder that is directly in the temp folder, carries one of
the harness's prefixes AND the marker this module wrote: a path from anywhere else is refused, never deleted.
Logs a check keeps as evidence are copied out first by `keep()` into fieldkit-logs/<profile name>/ (logs only:
no cookies, no cache, no prefs).
"""
import shutil
import tempfile
import time
from pathlib import Path

MARK = ".fieldkit-throwaway"
PREFIXES = ("gcap_", "gleaks_", "gproof_", "gegress_", "gadblock_", "gstartup_", "gnetbench_", "gprobe_")
USER_JS = ('user_pref("browser.shell.checkDefaultBrowser", false);\n'
           'user_pref("browser.aboutwelcome.enabled", false);\n')


def root():
    return Path(tempfile.gettempdir())


def profile(prefix, user_js=USER_JS):
    """A fresh throwaway profile -> its path."""
    if prefix not in PREFIXES:
        raise ValueError(f"unknown throwaway prefix {prefix!r}")
    prof = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root())))
    (prof / MARK).write_text("made by fieldkit build-harness; deleted after the check\n", encoding="utf-8")
    if user_js:
        (prof / "user.js").write_text(user_js, encoding="utf-8")
    return prof


def ours(prof):
    """True only for a folder this module made: directly in the temp folder, a harness prefix, the marker."""
    try:
        p = Path(prof).resolve()
        return (p.parent == root().resolve() and p.name.startswith(PREFIXES) and p.is_dir()
                and not p.is_symlink() and (p / MARK).is_file())
    except OSError:
        return False


def keep(prof, *patterns):
    """Copy the log files matching `patterns` out of the profile -> the folder they were copied to."""
    dest = root() / "fieldkit-logs" / Path(prof).name
    dest.mkdir(parents=True, exist_ok=True)
    for pat in patterns:
        for f in sorted(Path(prof).glob(pat)):
            if f.is_file():
                shutil.copy2(f, dest / f.name)
    return dest


def discard(prof, tries=3, sleep=time.sleep):
    """Delete a throwaway profile this module made. -> True when it is gone. Anything else is refused (False)."""
    if not ours(prof):
        return False
    p = Path(prof)
    for n in range(tries):
        shutil.rmtree(p, ignore_errors=True)       # a just-killed browser can hold a file for a moment
        if not p.exists():
            return True
        if n + 1 < tries:
            sleep(1)
    return not p.exists()
