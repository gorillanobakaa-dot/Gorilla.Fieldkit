# Version: 1.0.0 · updated 26-10-20
"""The stranger test: download a project the way someone else would, on this machine, and see whether it works.

    fieldkit stranger [REPO] [--ref REF] [--extras test] [--keep] [--json]

Born 2026-10-19/20 (owner: "fieldkit has to be able to be downloaded by someone on a windows or linux computer ...
let's make sure it works"). Everything passed on the owner's laptop while a fresh clone failed 18 tests and 19 cards,
and GitHub CI on the branch had failed for three days unread. Then the stranger test itself passed two tests that CI
failed: they read the owner's THEME.yaml from the home folder. So a stranger here has:

  - a fresh clone of REF (default: the current branch of REPO, default: this Fieldkit) in an empty temp folder,
  - a fresh virtual environment, and only `pip install -e ".[EXTRAS]"` as INSTALL.md says,
  - a fresh, empty home folder (HOME and USERPROFILE): no Documents, no owner files, no private config,
  - none of the checkout's ignored folders (local/, state/, fieldkit.local.json do not exist in a clone).

Steps, each a row: clone, venv, install, test suite (`pytest -q -rf`, the failing tests named), and for Fieldkit itself
`fieldkit host` and `fieldkit cards check`. Heavy (the suite): run it under `fieldkit guard --`. The work folder goes to
the Recycle Bin afterwards unless --keep. Exit 0 only when every row passes. What it cannot show: another operating
system (Linux and macOS are proven where they run: GitHub CI for Linux, the Air for macOS 10.13).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FK = Path(__file__).resolve().parents[2]


def _venv_bin(venv, name):
    d = Path(venv) / ("Scripts" if os.name == "nt" else "bin")
    return d / (name + (".exe" if os.name == "nt" else ""))


#: the only variables a stranger inherits: what a shell needs to start programs, nothing that names the owner's tools,
#: models, keys or folders (the worker's review, 2026-10-20: "start the child from a minimal environment, not
#: os.environ minus a few keys" - PFIND_CHROMA_PYTHON and the like would hide a stranger's failures)
KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "SYSTEMDRIVE", "SystemDrive", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "TMPDIR", "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "LANG", "LC_ALL",
        "LC_CTYPE", "TERM")


def stranger_env(home, base=None):
    """The environment a stranger has: an allowlist of the system's own variables, and a fresh home everywhere a
    program looks for one (HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG_*)."""
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if k in KEEP}
    home = Path(home)
    env.update({"HOME": str(home), "USERPROFILE": str(home), "PYTHONNOUSERSITE": "1",
                "APPDATA": str(home / "AppData" / "Roaming"), "LOCALAPPDATA": str(home / "AppData" / "Local"),
                "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
                "XDG_CACHE_HOME": str(home / ".cache")})
    return env


def failing_tests(text):
    return re.findall(r"^FAILED (\S+)", text or "", re.M)


def run(repo=None, ref=None, extras="test", keep=False, say=print, runner=subprocess.run, work=None):
    """-> {"ok", "rows", "work"}."""
    repo = Path(repo or FK)
    rows = []

    def row(step, ok, evidence):
        rows.append({"step": step, "ok": bool(ok), "evidence": evidence})
        say(f"  [{'ok' if ok else 'FAIL'}] {step}: {evidence}")
        return ok
    ref = ref or runner(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                        text=True).stdout.strip()
    base = Path(work or tempfile.mkdtemp(prefix="fk-stranger-"))
    base.mkdir(parents=True, exist_ok=True)
    src, venv, home = base / "src", base / "venv", base / "home"
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    env = stranger_env(home)
    r = runner(["git", "clone", "-q", "--branch", ref, str(repo), str(src)], capture_output=True, text=True)
    if not row("fresh clone", r.returncode == 0, f"{ref} of {repo.name}" + (f": {r.stderr.strip()[:200]}" if r.returncode else "")):
        return _end(rows, base, keep, say, runner)
    head = runner(["git", "-C", str(src), "log", "-1", "--format=%h %s"], capture_output=True, text=True).stdout.strip()
    say(f"  cloned {head[:90]}")
    r = runner([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True)
    if not row("fresh virtual environment", r.returncode == 0, str(venv)):
        return _end(rows, base, keep, say, runner)
    py = _venv_bin(venv, "python")
    spec = f".[{extras}]" if extras else "."
    t0 = time.time()
    r = runner([str(py), "-m", "pip", "install", "-q", "-e", spec], cwd=str(src), capture_output=True, text=True, env=env)
    if not row(f"pip install -e \"{spec}\"", r.returncode == 0, f"{time.time() - t0:.0f} s" + (
            f": {(r.stderr or r.stdout).strip()[-300:]}" if r.returncode else "")):
        return _end(rows, base, keep, say, runner)
    leaked = [n for n in ("local", "state", "fieldkit.local.json") if (src / n).exists()]
    row("no private files in the clone", not leaked, "none of local/, state/, fieldkit.local.json" if not leaked else f"present: {leaked}")
    t0 = time.time()
    r = runner([str(py), "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider"], cwd=str(src), capture_output=True,
               text=True, encoding="utf-8", errors="replace", env=env)
    out = r.stdout or ""
    summary = next((l for l in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed)", l)), "no summary")
    failed = failing_tests(out)
    row("test suite with a fresh home folder", r.returncode == 0,
        f"{summary.strip('= ').strip()} in {time.time() - t0:.0f} s" + (f"; failing: {failed[:12]}" if failed else ""))
    fk = _venv_bin(venv, "fieldkit")
    if fk.is_file():                                              # Fieldkit itself: its front door and its cards
        r = runner([str(fk), "host"], cwd=str(src), capture_output=True, text=True, env=env)
        row("fieldkit host", r.returncode == 0, "answers" if r.returncode == 0 else (r.stderr or r.stdout)[-200:])
        r = runner([str(fk), "cards", "check"], cwd=str(src), capture_output=True, text=True, env=env)
        last = (r.stdout or "").strip().splitlines()[-1:] or [""]
        bad = re.findall(r"^FAIL (\S+)", r.stdout or "", re.M)
        row("fieldkit cards check", r.returncode == 0 and not bad, last[0] + (f"; failing: {bad[:12]}" if bad else ""))
    return _end(rows, base, keep, say, runner)


def _end(rows, base, keep, say, runner):
    ok = bool(rows) and all(r["ok"] for r in rows)
    if not keep and base.exists():                                # the Recycle Bin, never a permanent delete
        runner([sys.executable, "-m", "fieldkit", "size", "recycle", str(base), "--whole-folder", "--plan-files", "200000",
                "--plan-bytes", "20G", "--json"], capture_output=True, text=True)
        say(f"  work folder to the Recycle Bin: {base}" if not base.exists() else f"  work folder kept (recycle failed): {base}")
    say("STRANGER TEST PASS: a fresh download works here" if ok else "STRANGER TEST FAIL: a fresh download does not work (rows above)")
    return {"ok": ok, "rows": rows, "work": str(base)}


def main(argv=None, prog="fieldkit stranger"):
    ap = argparse.ArgumentParser(prog=prog, description="Download a project the way someone else would, and see whether it works.")
    ap.add_argument("repo", nargs="?", help="the repository to clone (default: this Fieldkit)")
    ap.add_argument("--ref", help="branch or tag to clone (default: the repository's current branch)")
    ap.add_argument("--extras", default="test", help="pip extras to install (default: test; empty for none)")
    ap.add_argument("--keep", action="store_true", help="keep the work folder (default: to the Recycle Bin)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = run(a.repo, a.ref, a.extras, a.keep, say=(lambda m: None) if a.json else print)
    if a.json:
        print(json.dumps(r, indent=1))
    return 0 if r["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
