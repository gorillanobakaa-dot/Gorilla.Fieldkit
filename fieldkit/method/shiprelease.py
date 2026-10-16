# Version: 1.0.0 · updated 26-10-16-02-40
"""Is the work on main, and is main released? The two questions nothing asked until 2026-10-16.

That night the Fieldkit release page still showed v0.3.0 of 13 October while main was 135 commits ahead: every pull
request had been merged, nothing had been released, and no tool said so. The owner: "a deterministic tool that does
all this automatically, on Linux and Windows, so it never happens again: commit, push, everything goes into main,
and properly released."

    fieldkit ship audit   [--repo R] [--offline]      rows OK/DO, each DO with its exact command
    fieldkit ship release vX.Y.Z [--repo R] [--scaffold] [--wait S]

audit (git + the GitHub API; --offline: git only, for the session-start brief):
  checkout    the checkout is on the base, clean, and equal to origin/<base>
  branches    no remote branch carries commits the base lacks (work that never reached main), merged ones ignored
  pulls       open pull requests, each with its CI state (green ones are waiting to be merged)
  release     commits and days between the newest v* tag and origin/<base>; DUE past --max-commits or --max-days

release (refuses until every condition holds, then does it all; never forces, never edits history):
  1. on the base, clean, equal to origin/<base>      2. CI green on that commit (every check run completed, success)
  3. the package version equals the tag              4. releases/notes/<tag>/ has opening, layman, developer;
                                                        the page is composed and `release-page check` passes
  5. the tag does not exist yet                      6. an annotated tag is pushed; the release workflow publishes
  7. it waits for the release to appear on GitHub (--wait seconds) and checks its page is the composed page
--scaffold writes the three note files when they are missing: the developer file lists the commits since the last
tag; the plain-language file is a heading only, because a person (or a model, in full words) must write it. It stops
after writing them: a scaffold is never published.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from .ship import git, _rev

MAX_COMMITS = 20
MAX_DAYS = 7


def _gh(args, timeout=60):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return (r.stdout if r.returncode == 0 else None), (r.stderr or "").strip()


def repo_slug(repo):
    url = git(repo, "remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(\.git)?/?$", url)   # names may hold dots: Gorilla.Fieldkit
    return m.group(1) if m else None


def last_tag(repo, ref):
    r = git(repo, "describe", "--tags", "--abbrev=0", "--match", "v*", ref)
    return r.stdout.strip() or None


def release_gap(repo, base="main"):
    """-> {"tag", "commits", "days"} between the newest v* tag reachable from origin/<base> and origin/<base>."""
    ref = f"origin/{base}"
    tag = last_tag(repo, ref)
    if not tag:
        n = git(repo, "rev-list", "--count", "--no-merges", ref).stdout.strip()
        return {"tag": None, "commits": int(n or 0), "days": None}
    n = int(git(repo, "rev-list", "--count", "--no-merges", f"{tag}..{ref}").stdout.strip() or 0)
    t = git(repo, "log", "-1", "--format=%ct", tag).stdout.strip()
    days = round((time.time() - int(t)) / 86400, 1) if t else None
    return {"tag": tag, "commits": n, "days": days}


def content_on_base(repo, ref, base="main"):
    """True when every change `ref` makes since it left the base is already in origin/<base>: its diff applies in
    REVERSE to the base's tree (a temporary index, no checkout). A squash-merged branch passes; real new work fails."""
    import os
    import tempfile
    mb = git(repo, "merge-base", f"origin/{base}", ref).stdout.strip()
    diff = git(repo, "diff", "--binary", mb, ref).stdout
    if not diff.strip():
        return True
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(d) / "index")}
        subprocess.run(["git", "-C", str(repo), "read-tree", f"origin/{base}"], env=env, capture_output=True)
        r = subprocess.run(["git", "-C", str(repo), "apply", "--cached", "--check", "-R", "-"], input=diff, env=env,
                           capture_output=True, text=True)
    return r.returncode == 0


def audit(repo=".", base="main", offline=False, max_commits=MAX_COMMITS, max_days=MAX_DAYS, gh=_gh):
    """-> {"ok", "rows": [{check, state OK|DO, say, run}]}"""
    rows = []
    row = lambda c, ok, say, run="": rows.append({"check": c, "state": "OK" if ok else "DO", "say": say, "run": run})
    if not offline:
        git(repo, "fetch", "--quiet", "--prune", "origin")
    branch = git(repo, "branch", "--show-current").stdout.strip() or "(detached)"
    dirty = [l for l in git(repo, "status", "--porcelain").stdout.splitlines() if l and not l.startswith("??")]
    head, remote = _rev(repo, "HEAD"), _rev(repo, f"origin/{base}")
    if dirty:
        row("checkout", False, f"{len(dirty)} changed file(s) not committed on {branch}",
            "commit them on a branch (the pre-commit hook runs the tests), then fieldkit ship push")
    elif branch != base:
        row("checkout", False, f"the checkout is on {branch}, not {base}: the installed tools run that branch's code",
            f"after its pull request merges: git switch {base} && git merge --ff-only origin/{base}")
    elif head != remote:
        behind = git(repo, "merge-base", "--is-ancestor", "HEAD", f"origin/{base}").returncode == 0
        row("checkout", False, f"{base} is {'behind' if behind else 'not equal to'} origin/{base}",
            f"git merge --ff-only origin/{base}" if behind else "never push to the base: open a pull request")
    else:
        row("checkout", True, f"on {base}, clean, equal to origin/{base}")
    unmerged, stale = [], []
    slug = None if offline else repo_slug(repo)
    for line in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").stdout.split():
        name = line.split("/", 1)[-1]
        if name in (base, "HEAD") or line == "origin":
            continue
        n = git(repo, "rev-list", "--count", "--no-merges", f"origin/{base}..{line}").stdout.strip()
        if not n or int(n) == 0:
            continue
        # a squash-merged branch keeps commits the base never got by name; only GitHub knows its pull request merged
        # (2026-10-16: two branches squash-merged in #15 and changed since looked like lost work to git alone)
        merged = None
        if slug:
            out, _ = gh(["pr", "list", "-R", slug, "--state", "all", "--head", name, "--json", "number,state",
                         "-q", '.[] | "\\(.number) \\(.state)"'])
            prs = [l.split() for l in (out or "").splitlines() if l.strip()]
            if any(s == "OPEN" for _, s in prs):
                continue                                    # in a pull request now: the pulls row reports it
            merged = [p for p, s in prs if s == "MERGED"] or None
        if not merged and content_on_base(repo, line, base):
            merged = ["content"]                            # every change it makes is already in the base
        (stale if merged else unmerged).append((name, int(n), merged))
    if not offline:
        row("branches", not unmerged,
            "every remote branch's work is on " + base if not unmerged else
            f"{len(unmerged)} remote branch(es) carry work {base} lacks, with no merged pull request: "
            + ", ".join(f"{b} ({n})" for b, n, _ in unmerged[:6]),
            "" if not unmerged else "open or finish a pull request for each, or say why it stays out")
        if stale:
            row("stale-branches", True, "merged long ago, safe to delete on GitHub when the owner says so: "
                + ", ".join(f"{b} ({'#' + m[0] if m[0] != 'content' else 'its changes are on ' + base})"
                            for b, _, m in stale[:8]))
    if not offline:
        out, err = gh(["pr", "list", "-R", slug, "--state", "open", "--json", "number,title,headRefName,statusCheckRollup"]) \
            if slug else (None, "no GitHub remote")
        if out is None:
            row("pulls", False, f"open pull requests not readable: {err[:120]}", "gh auth status")
        else:
            prs = json.loads(out or "[]")
            desc = []
            for p in prs:
                checks = p.get("statusCheckRollup") or []
                ci = ("green" if checks and all(c.get("conclusion") == "SUCCESS" for c in checks) else
                      "red" if any(c.get("conclusion") in ("FAILURE", "CANCELLED", "TIMED_OUT") for c in checks) else
                      "running" if checks else "no CI")
                desc.append(f"#{p['number']} {ci}")
            row("pulls", not prs, "no open pull request" if not prs else "open: " + ", ".join(desc),
                "" if not prs else f"merge the green ones (gh pr merge N -R {slug} --merge), fix the red ones")
    gap = release_gap(repo, base)
    due = gap["commits"] > max_commits or (gap["days"] is not None and gap["days"] > max_days and gap["commits"] > 0)
    row("release", not due,
        (f"origin/{base} is {gap['commits']} commit(s) and {gap['days']} day(s) past {gap['tag']}" if gap["tag"] else
         f"no v* release tag yet; {gap['commits']} commit(s) on {base}") + (": a release is DUE" if due else ""),
        "" if not due else "fieldkit ship release vX.Y.Z --scaffold, write the notes, then fieldkit ship release vX.Y.Z")
    return {"ok": all(r["state"] == "OK" for r in rows), "rows": rows, "gap": gap}


def package_version(repo):
    """The version the package declares: pyproject.toml [project] version, else <pkg>/__init__.py __version__."""
    p = Path(repo) / "pyproject.toml"
    if p.is_file():
        m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', p.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    for init in Path(repo).glob("*/__init__.py"):
        m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return None


def scaffold(repo, tag, base="main"):
    d = Path(repo) / "releases" / "notes" / tag
    d.mkdir(parents=True, exist_ok=True)
    prev = last_tag(repo, f"origin/{base}")
    rng = f"{prev}..origin/{base}" if prev else f"origin/{base}"
    subjects = git(repo, "log", "--no-merges", "--format=- %s", rng).stdout.strip()
    made = []
    for name, text in (("opening.md", "## What this is\n\n## Should you download it?\n\n## Why this matters\n"),
                       ("layman.md", f"# {tag}, in plain language\n"),
                       ("developer.md", f"# {tag}, for developers\n\n## Scope\n\n`{rng}`:\n\n{subjects}\n")):
        f = d / name
        if not f.exists():
            f.write_text(text, encoding="utf-8", newline="\n")
            made.append(str(f))
    return made


def release(tag, repo=".", base="main", scaffold_notes=False, wait=900, gh=_gh, sleep=time.sleep, py=sys.executable):
    """-> {"ok", "rows": [...], "next"}"""
    rows = []

    def row(c, ok, say):
        rows.append({"check": c, "state": "OK" if ok else "STOP", "say": say})
        return ok

    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        row("tag", False, f"{tag!r} is not vX.Y.Z")
        return {"ok": False, "rows": rows}
    git(repo, "fetch", "--quiet", "--tags", "origin")
    notes = Path(repo) / "releases" / "notes" / tag
    if scaffold_notes:
        made = scaffold(repo, tag, base)
        row("scaffold", False, (f"wrote {', '.join(made)}: write them in full words, then run without --scaffold"
                                if made else f"{notes} already has its files: run without --scaffold"))
        return {"ok": False, "rows": rows}
    branch = git(repo, "branch", "--show-current").stdout.strip()
    dirty = [l for l in git(repo, "status", "--porcelain").stdout.splitlines() if l and not l.startswith("??")]
    head, remote = _rev(repo, "HEAD"), _rev(repo, f"origin/{base}")
    if not row("checkout", branch == base and not dirty and head == remote,
               f"on {branch or '(detached)'}, {'dirty' if dirty else 'clean'}, "
               f"{'equal to' if head == remote else 'NOT equal to'} origin/{base} (the notes and the version bump reach "
               f"{base} through a pull request first)"):
        return {"ok": False, "rows": rows}
    slug = repo_slug(repo)
    # CI starts again on the merge commit: wait while checks run and none has failed (2026-10-16: v0.4.0 was refused
    # twice for 'in_progress' right after the release PR merged)
    deadline = time.time() + wait
    while True:
        out, err = gh(["api", f"repos/{slug}/commits/{head}/check-runs", "--paginate", "-q",
                       ".check_runs[] | [.name, .status, .conclusion] | @tsv"])
        runs = [(l.split("\t") + ["", ""])[:3] for l in (out or "").splitlines() if l.strip()]
        failed = any(r[1] == "completed" and r[2] not in ("success", "skipped", "neutral") for r in runs)
        if failed or (runs and all(r[1] == "completed" for r in runs)) or time.time() >= deadline:
            break
        sleep(30)
    green = bool(runs) and all(r[1] == "completed" and r[2] == "success" for r in runs)
    if not row("ci", green, f"{len(runs)} check run(s) on {head[:10]}: " + (", ".join(f"{r[0]} {r[2] or r[1]}" for r in runs)
               or err[:120] or "none")):
        return {"ok": False, "rows": rows}
    v = package_version(repo)
    if not row("version", v == tag[1:], f"package version {v}, tag {tag}"):
        return {"ok": False, "rows": rows}
    files = {n: notes / f"{n}.md" for n in ("opening", "layman", "developer")}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if not row("notes", not missing, "every note file present" if not missing else f"missing {missing}: --scaffold writes them"):
        return {"ok": False, "rows": rows}
    page = notes / "page.md"
    comp = subprocess.run([py, "-m", "fieldkit", "release-page", "compose", "--opening", str(files["opening"]),
                           "--layman", str(files["layman"]), "--developer", str(files["developer"]), "--out", str(page)]
                          + (["--extra", str(notes / "extra.md")] if (notes / "extra.md").is_file() else []),
                          capture_output=True, text=True, cwd=repo)
    chk = subprocess.run([py, "-m", "fieldkit", "release-page", "check", str(page), "--layman", str(files["layman"]),
                          "--developer", str(files["developer"])], capture_output=True, text=True, cwd=repo)
    committed = not git(repo, "status", "--porcelain", "--", str(page)).stdout.strip()
    if not row("page", comp.returncode == 0 and chk.returncode == 0 and committed,
               (chk.stdout or chk.stderr).strip()[-200:] + ("" if committed else "; the composed page differs from the "
                                                            "committed one: commit it through a pull request first")):
        return {"ok": False, "rows": rows}
    if not row("tag-free", not _rev(repo, f"refs/tags/{tag}"), f"{tag} {'already exists' if _rev(repo, f'refs/tags/{tag}') else 'is free'}"):
        return {"ok": False, "rows": rows}
    git(repo, "tag", "-a", tag, "-m", f"Release {tag}", head, check=True)
    r = git(repo, "push", "origin", f"refs/tags/{tag}")
    if not row("push-tag", r.returncode == 0, (r.stderr or r.stdout).strip()[-200:] or f"{tag} pushed"):
        return {"ok": False, "rows": rows}
    deadline, body = time.time() + wait, None
    while time.time() < deadline:
        out, _ = gh(["release", "view", tag, "-R", slug, "--json", "body", "-q", ".body"])
        if out:
            body = out
            break
        sleep(20)
    want = page.read_text(encoding="utf-8").strip()
    row("published", body is not None and body.strip() == want,
        f"https://github.com/{slug}/releases/tag/{tag}" if body is not None and body.strip() == want else
        ("the release did not appear within the wait: see the release workflow run" if body is None else
         "the release page differs from releases/notes/<tag>/page.md"))
    return {"ok": all(r["state"] == "OK" for r in rows), "rows": rows}


def lines(r):
    out = [f"  {x['state']:<5} {x['check']}: {x['say']}" + (f"\n        RUN: {x['run']}" if x.get("run") else "")
           for x in r["rows"]]
    return out
