"""A goal never runs a whole test suite on the laptop (owner, 2026-10-27, permanent: "Da: pe laptop doar testele
fișierelor atinse, suita completă doar în CI." / "no more thousands of tests").

The windows-capabilities goal's criteria and job verify lists said `go test -count=1 ./...`; the verifier ran the
whole Gorilla OpenCode suite on the laptop again and again. A whole-suite command is recognized here and replaced by:

- the tests of what the branch touched (Go: `go test -count=1 ./internal/x/...` per touched package directory; Rust:
  `cargo test --manifest-path CRATE/Cargo.toml` per touched crate; Python: `fieldkit testcost run --fast`), and
- CI green on the branch's exact head (`gh run list --commit SHA`): the only evidence for the full suite.

FIELDKIT_FULL_SUITE=1 (the owner's word only) keeps the original command.

    python -m fieldkit.method.suitegate check CMD                 the kind of whole suite CMD runs (exit 3), or none (0)
    python -m fieldkit.method.suitegate run --orig-b64 B [--base REF] [--cwd DIR]   the touched tests + CI green
"""
import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "origin/main"

CI_NOTE = ("Owner rule (2026-10-27): a full test suite never runs on this laptop. Where this question asks for a whole "
           "suite (`go test ./...`, `cargo test` on a workspace, `pytest` with no paths), do NOT run it: the evidence "
           "for that line is CI green on the branch's exact head (`git rev-parse HEAD`, then `gh run list --commit "
           "SHA --json name,status,conclusion`); locally run only the tests of the packages the branch touched.")


def full_suite_allowed(env=None):
    return (os.environ if env is None else env).get("FIELDKIT_FULL_SUITE") == "1"


def _words(cmd):
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def _segments(cmd):
    """A shell line -> its commands (split on ; && || |), each a word list (a leading PowerShell `&` dropped)."""
    out = []
    for s in re.split(r"\s*(?:;|&&|\|\||\|)\s*", cmd or ""):
        w = [x for x in _words(s) if x != "&"]
        if w:
            out.append(w)
    return out


_PYTEST_VALUE = {"-k", "-m", "-p", "-c", "-o", "-n", "--rootdir", "--maxfail", "--timeout", "--deselect", "--basetemp"}


def classify(cmd):
    """-> "go" | "cargo" | "python" when `cmd` runs a whole suite, else None. A named package is not a suite."""
    for w in _segments(cmd):
        exe = Path(w[0].strip('"')).name.lower().removesuffix(".exe")
        if exe == "go" and len(w) > 1 and w[1] == "test":
            if any(a in ("./...", "...", ".\\...") for a in w[2:]):
                return "go"
        elif exe == "cargo" and len(w) > 1 and w[1] == "test":
            rest = w[2:]
            if "--workspace" in rest or "--all" in rest:
                return "cargo"
            if not any(a in ("-p", "--package", "--manifest-path") or a.startswith(("--package=", "--manifest-path="))
                       for a in rest):
                return "cargo"
        else:
            if exe in ("python", "python3", "py") and len(w) > 2 and w[1:3] == ["-m", "pytest"]:
                rest = w[3:]
            elif exe in ("pytest", "py.test"):
                rest = w[1:]
            else:
                continue
            paths, skip = [], False
            for a in rest:
                if skip:
                    skip = False
                elif a in _PYTEST_VALUE:
                    skip = True
                elif not a.startswith("-"):
                    paths.append(a.replace("\\", "/").rstrip("/") or ".")
            if all(p in ("tests", "test", ".") for p in paths):
                return "python"
    return None


def asks_full_suite(question):
    """A verifier question that asks for a whole local suite (it then gets CI_NOTE)."""
    q = question or ""
    return bool(re.search(r"(?i)\b(go|cargo)\s+test\b[^`'\"]*?(\./\.\.\.|--workspace)|\b(full|whole)\s+(test\s+)?suite",
                          q) or any(classify(m) for m in re.findall(r"`([^`]+)`", q)))


def _git(cwd, *args, run=subprocess.run):
    r = run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def touched(cwd, base=DEFAULT_BASE, run=subprocess.run):
    """The files the branch's commits changed against base (from the merge base)."""
    return [p for p in _git(cwd, "diff", "--name-only", f"{base}...HEAD", run=run).splitlines() if p.strip()]


def _go_flags(cmd):
    """The flags of the original `go test` worth keeping (-tags, -run, -timeout, -v, -race, -short); -count is 1."""
    w = next((w for w in _segments(cmd) if len(w) > 1 and w[1] == "test"), [])
    keep, i = [], 2
    while i < len(w):
        a = w[i]
        if a in ("-tags", "-run", "-timeout") and i + 1 < len(w):
            keep += [a, w[i + 1]]
            i += 2
            continue
        if a.startswith(("-tags=", "-run=", "-timeout=")) or a in ("-v", "-race", "-short"):
            keep.append(a)
        i += 1
    return keep


def local_commands(kind, cmd, files, cwd=".", base=DEFAULT_BASE):
    """-> [argv, ...] for the touched part (empty when the branch touched nothing of that kind). Never a whole suite."""
    cwd = Path(cwd)
    if kind == "go":
        dirs = sorted({Path(f).parent.as_posix() for f in files
                       if f.endswith(".go") or Path(f).name in ("go.mod", "go.sum")})
        pkgs = [("./" + d + "/...") if d != "." else "." for d in dirs]
        return [["go", "test", "-count=1", *_go_flags(cmd), *pkgs]] if pkgs else []
    if kind == "cargo":
        crates = set()
        for f in files:
            if not (f.endswith(".rs") or Path(f).name in ("Cargo.toml", "Cargo.lock")):
                continue
            d = (cwd / f).parent
            while True:
                toml = d / "Cargo.toml"
                if toml.is_file() and "[package]" in toml.read_text(encoding="utf-8", errors="replace"):
                    crates.add(Path(os.path.relpath(toml, cwd)).as_posix())
                    break
                if d.resolve() == cwd.resolve() or d == d.parent:
                    break
                d = d.parent
        return [["cargo", "test", "--manifest-path", c] for c in sorted(crates)]
    if kind == "python":
        if not any(f.endswith(".py") for f in files):
            return []
        return [[sys.executable, "-m", "fieldkit", "testcost", "run", "--fast", "--base", base, "--repo", str(cwd)]]
    return []


def ci_green(cwd, run=subprocess.run):
    """-> (ok, why): every GitHub Actions run on the exact HEAD of cwd completed green (read with gh)."""
    sha = _git(cwd, "rev-parse", "HEAD", run=run)
    if not sha:
        return False, f"no git HEAD in {cwd}"
    try:
        r = run(["gh", "run", "list", "--commit", sha, "--limit", "50", "--json", "name,status,conclusion"],
                cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"gh could not run: {e}"
    if r.returncode != 0:
        return False, f"gh run list failed: {(r.stderr or r.stdout or '').strip()[:300]}"
    try:
        runs = json.loads(r.stdout or "[]")
    except ValueError:
        return False, "gh printed no JSON"
    if not runs:
        return False, f"no CI run on {sha[:12]} yet (push the branch: CI runs the full suite)"
    bad = [f"{x.get('name')}: {x.get('status')}/{x.get('conclusion')}" for x in runs
           if x.get("status") != "completed" or x.get("conclusion") not in ("success", "skipped", "neutral")]
    if bad:
        return False, f"CI on {sha[:12]} is not green: " + "; ".join(bad)[:400]
    return True, f"CI green on {sha[:12]} ({len(runs)} run(s))"


def gate_command(orig, base=DEFAULT_BASE):
    """The command that stands in for a whole-suite `orig` in a goal (any shell: no quoting of the original)."""
    b64 = base64.urlsafe_b64encode(orig.encode("utf-8")).decode("ascii")
    return f"python -m fieldkit.method.suitegate run --base {base} --orig-b64 {b64}"


def mark(c, env=None, base=DEFAULT_BASE):
    """A criterion or verify entry whose run is a whole suite -> (rewritten entry, warning); else (c, None).
    The original is kept in `replaced`; FIELDKIT_FULL_SUITE=1 keeps the entry as it is."""
    if not c.get("run") or c.get("replaced") or full_suite_allowed(env):
        return c, None
    kind = classify(c["run"])
    if not kind:
        return c, None
    new = dict(c, run=gate_command(c["run"], base), replaced=c["run"], suite=kind)
    new.pop("expect", None)                    # the gate's exit code decides (a -v line of the suite no longer prints)
    return new, (f"{c.get('id') or c['run'][:60]}: `{c['run']}` is a whole {kind} suite; rewritten: the laptop runs "
                 f"only the tests of the touched packages, the full suite is CI green on the branch's exact head "
                 f"(FIELDKIT_FULL_SUITE=1 keeps it, on the owner's word only)")


def mark_goal(spec, env=None):
    """Every criterion, seed-job verify entry and agent question of a goal spec -> (spec, warnings)."""
    spec, warns = dict(spec), []
    base = spec.get("base") or DEFAULT_BASE
    crit = []
    for c in spec.get("criteria") or []:
        c, w = mark(c, env, base)
        if w:
            warns.append("criterion " + w)
        if c.get("judge") == "agent" and asks_full_suite(c.get("question")) and not full_suite_allowed(env) \
                and CI_NOTE not in c["question"]:
            c = dict(c, question=c["question"] + "\n\n" + CI_NOTE)
            warns.append(f"criterion {c.get('id')}: its question asks for a whole suite; the verifier is told that CI "
                         "green on the exact head is the evidence for that line")
        crit.append(c)
    if "criteria" in spec:
        spec["criteria"] = crit
    jobs = []
    for j in spec.get("seed_jobs") or []:
        vs = []
        for v in j.get("verify") or []:
            v, w = mark(v, env, base)
            if w:
                warns.append(f"seed job {j.get('id')} verify " + w)
            vs.append(v)
        jobs.append(dict(j, verify=vs) if "verify" in j else j)
    if "seed_jobs" in spec:
        spec["seed_jobs"] = jobs
    return spec, warns


def run_gate(orig, cwd=".", base=DEFAULT_BASE, run=subprocess.run, env=None, say=print):
    """The stand-in for a whole-suite command: touched tests locally, CI green for the rest. -> exit code."""
    if full_suite_allowed(env):
        say(f"suitegate: FIELDKIT_FULL_SUITE=1: running the whole suite as written: {orig}")
        return run(orig, shell=True, cwd=str(cwd)).returncode
    kind = classify(orig)
    if not kind:
        say(f"suitegate: `{orig}` is not a whole suite: run as written")
        return run(orig, shell=True, cwd=str(cwd)).returncode
    files = touched(cwd, base, run=run)
    cmds = local_commands(kind, orig, files, cwd, base)
    rc = 0
    if not cmds:
        say(f"suitegate: the branch touched no {kind} code against {base} ({len(files)} file(s)): no local tests")
    for argv in cmds:
        say("suitegate: local (touched only): " + " ".join(argv))
        r = run(argv, cwd=str(cwd))
        say(f"suitegate: exit {r.returncode}")
        rc = rc or r.returncode
    ok, why = ci_green(cwd, run=run)
    say(f"suitegate: full suite = {why}")
    if rc:
        say("SUITEGATE: FAIL (the touched tests failed)")
        return rc
    if not ok:
        say("SUITEGATE: FAIL (the full suite has no green CI on this head)")
        return 3
    say("SUITEGATE: PASS (touched tests green locally; full suite green in CI)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fieldkit.method.suitegate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="exit 3 when CMD is a whole suite (prints its kind)")
    c.add_argument("command")
    r = sub.add_parser("run", help="the stand-in: touched tests locally + CI green on the exact head")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--orig", help="the whole-suite command it stands in for")
    g.add_argument("--orig-b64", help="the same, url-safe base64 (what goal new writes: no shell quoting)")
    r.add_argument("--base", default=DEFAULT_BASE)
    r.add_argument("--cwd", default=".")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        k = classify(a.command)
        print(k or "none")
        return 3 if k else 0
    orig = a.orig if a.orig is not None else base64.urlsafe_b64decode(a.orig_b64.encode("ascii")).decode("utf-8")
    return run_gate(orig, a.cwd, a.base)


if __name__ == "__main__":
    sys.exit(main())
