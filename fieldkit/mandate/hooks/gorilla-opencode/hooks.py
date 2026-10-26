"""Gorilla.Opencode git hooks, v1.1.0, 2026-10-25. Private (scripts/ is gitignored).

pre-commit: refuses files forced past .gitignore, and known secret signatures.
pre-push:   refuses unless GORILLA_OWNER_APPROVED=1 OR the push comes through
            `fieldkit ship push` and `fieldkit mandate gate` finds the owner's
            signed mandate in force for these exact branches/tags (2026-10-25:
            one approval at the start, not a flag per push); always refuses
            branch deletion, non-fast-forward pushes and commits adding secret
            signatures, mandate or not.
Secret and ignore checks copied from Gorilla.firefox harness/hooks/pre_commit.py
(commit c036a09), where they are tested.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip())

# High-confidence secret SIGNATURES, not a generic "KEY=" scan. A generic
# scan flags every .env.example, which is SUPPOSED to hold placeholders
# ("API_KEY=your_key_here") and would train everyone to click past the
# warning. These match the fixed-format prefixes real secrets actually have.
SECRET_PATTERNS = [
    (r"\bghp_[A-Za-z0-9]{36}\b", "a GitHub personal access token"),
    (r"\bgh[oust]_[A-Za-z0-9]{36}\b", "a GitHub OAuth/app/refresh/server token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "an AWS access key id"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "a Slack token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    (r"\bAIzaSy[A-Za-z0-9_-]{33}\b", "a Google API key"),
    (r"\bsk-[A-Za-z0-9]{20,}\b", "an API key in the sk- family (OpenAI/Anthropic-style)"),
]


def run(*args):
    return subprocess.run(list(args), cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")


def force_added_files(staged):
    """Staged files .gitignore would otherwise exclude: forced past it with
    something like `git add -f`.

    Plain `git check-ignore` says nothing for a path once it is already in
    the index (tested: staging with `git add -f` alone made a genuinely
    ignored `.env` read back as "not ignored"), which would make this check
    blind to precisely the case it exists for. `--no-index` evaluates the
    .gitignore patterns on their own, the way they would apply to a path
    that was never staged, so a forced file still reads as ignored. It is
    checked only against files new to git (not in HEAD), so an ignore
    pattern added AFTER a file was legitimately tracked does not flag it
    on every later commit.
    """
    tracked_before = set(run("git", "ls-tree", "-r", "--name-only", "HEAD").stdout.split())
    new_files = [f for f in staged if f not in tracked_before]
    if not new_files:
        return []
    hit = run("git", "check-ignore", "--no-index", "--", *new_files)
    return [line for line in hit.stdout.splitlines() if line.strip()]


def secret_hits(staged):
    """Newly ADDED lines (git diff --cached) matching a secret signature.
    Only additions are checked: a secret being REMOVED is not a new leak.
    """
    diff = run("git", "diff", "--cached", "-U0", "--", *staged).stdout
    found = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pat, what in SECRET_PATTERNS:
            if re.search(pat, line):
                found.append((what, line[1:].strip()[:80]))
    return found



def pre_commit():
    staged = run("git", "diff", "--cached", "--name-only").stdout.split()
    refused = []
    forced = force_added_files(staged)
    if forced:
        refused.append("staged file(s) that .gitignore excludes (forced in with git add -f): "
                       + ", ".join(forced))
    for what, line in secret_hits(staged):
        refused.append("staged content looks like %s: %s" % (what, line))
    return refused


def _fieldkit():
    """The fieldkit command, or None (then only the owner's flag opens a push: fail closed)."""
    exe = shutil.which("fieldkit")
    if exe:
        return [exe]
    if importlib.util.find_spec("fieldkit") is not None:
        return [sys.executable, "-m", "fieldkit"]
    return None


def mandate_gate(stdin_text, env, run=subprocess.run):
    """-> "" when the owner's signed mandate lets this push go without the flag, else why not.
    The gate (Fieldkit fieldkit/mandate/gate.py) checks the seal, the trusted key, the expiry, the stop, this
    repository, every ref against the branch and tag patterns, fast-forward, no deletes, no moved tags."""
    if env.get("FIELDKIT_SHIP_PUSH") != "1":
        return "not pushed through fieldkit ship push"
    cmd = _fieldkit()
    if not cmd:
        return "fieldkit is not installed here, so no mandate can be checked"
    try:
        r = run(cmd + ["mandate", "gate", "--repo", str(ROOT)], input=stdin_text, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=600, env=dict(env))
    except (OSError, subprocess.TimeoutExpired) as e:
        return "the mandate gate did not run (%s)" % e
    if r.stderr:
        sys.stderr.write(r.stderr)
    return "" if r.returncode == 0 else "the mandate gate refused (reasons above)"


def pre_push(stdin_text, env=None):
    env = os.environ if env is None else env
    refused = []
    if env.get("GORILLA_OWNER_APPROVED") != "1":
        why = mandate_gate(stdin_text, env)
        if why:
            refused.append("pushing needs the owner: run the push with GORILLA_OWNER_APPROVED=1 set, or push with "
                           "fieldkit ship push under the owner's mandate (%s)" % why)
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        if set(local_sha) == {"0"}:
            refused.append("deleting remote branch %s" % remote_ref)
            continue
        if set(remote_sha) != {"0"}:
            base = run("git", "merge-base", "--is-ancestor", remote_sha, local_sha)
            if base.returncode != 0:
                refused.append("%s is not a fast-forward of the remote (history rewrite)" % remote_ref)
            rng = "%s..%s" % (remote_sha, local_sha)
        else:
            rng = local_sha + " --not --remotes"
        diff = run("git", "log", "-p", "--format=", *rng.split()).stdout
        for d in diff.splitlines():
            if d.startswith("+") and not d.startswith("+++"):
                for pat, what in SECRET_PATTERNS:
                    if re.search(pat, d):
                        refused.append("a pushed commit adds %s: %s" % (what, d[1:].strip()[:80]))
    return refused


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "pre-commit":
        problems = pre_commit()
    elif mode == "pre-push":
        problems = pre_push(sys.stdin.read())
    else:
        print("usage: hooks.py pre-commit | pre-push")
        sys.exit(2)
    if problems:
        print("\n  %s REFUSED\n" % mode.upper(), file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)
