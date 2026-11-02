"""The refusal other commands share: `ship push` and `release-page compose` stop with exit 3 while the repository they
act on has a capability that is not built in (docs/INTEGRATION.md). A folder that is neither a Gorilla.Opencode nor a
Fieldkit checkout is not this tool's business and passes. Fail closed: if the branch point cannot be found, the whole
repository is checked."""
import subprocess

from . import core


def branch_point(repo, base):
    """-> the commit this branch left `origin/<base>` (or `<base>`) at, or None."""
    for ref in (f"origin/{base}", base):
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "HEAD", ref], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def last_tag(repo):
    r = subprocess.run(["git", "-C", str(repo), "describe", "--tags", "--abbrev=0"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def guard(repo=".", base=None, since=None):
    """-> {"ok", "skipped", "gaps", "lines"}. base: check only what the branch changed against it; since: only what
    changed since this ref (a release gate passes the last tag); neither: everything."""
    since = since or (branch_point(repo, base) if base else None)
    r = core.run_check(repo, since)
    if r.get("error"):
        return {"ok": True, "skipped": True, "gaps": [], "lines": []}
    lines = [f"GAP {g['kind']}:{g['capability']} [{g['surface']}] {g['what']} -> fix {g['fix']}" for g in r["gaps"]]
    return {"ok": not r["gaps"], "skipped": False, "gaps": r["gaps"], "lines": lines}


def refusal(res):
    """One text for the error field: every gap, then the way out."""
    return "not built in (docs/INTEGRATION.md):\n" + "\n".join(res["lines"][:20])
