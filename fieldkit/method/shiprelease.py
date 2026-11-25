# Version: 1.1.0 · updated 26-11-25-12-00
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
  worktrees   extra local worktrees, each with whether its work is on the base (2026-10-23)
  local-branches  local branches with commits on no remote, and merged ones still lying here (2026-10-23)
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
from fieldkit import proc as fkproc
import sys
import time
from pathlib import Path

from .ship import git, _rev
from ..core import ghhidden
from . import shiprefs

MAX_COMMITS = 20
MAX_DAYS = 7


def _gh(args, timeout=60):
    try:
        r = fkproc.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    return (r.stdout if r.returncode == 0 else None), (r.stderr or "").strip()


def _ident(repo):
    """git -c arguments for an annotated tag when no identity is configured (a CI runner has none: the archive tag
    failed there on 2026-10-16, the same class as the silent-commit fix of 2026-10-15). A configured one is kept."""
    if git(repo, "config", "user.email").stdout.strip():
        return []
    return ["-c", "user.name=fieldkit", "-c", "user.email=fieldkit@localhost"]


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
        fkproc.run(["git", "-C", str(repo), "read-tree", f"origin/{base}"], env=env, capture_output=True)
        r = fkproc.run(["git", "-C", str(repo), "apply", "--cached", "--check", "-R", "-"], input=diff, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")   # cp1252 crashed on emoji (2026-11-09)
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
    slug = None if offline else repo_slug(repo)
    res = shiprefs.classify(repo, base, gh=None if offline else gh, slug=slug)
    cls = shiprefs.by_class(res)
    on_base = cls["on-base"]
    row("merged-branches", not on_base,
        "no merged branch left on the remote" if not on_base else
        f"{len(on_base)} branch(es) on the remote are fully merged into {base}: " + ", ".join(r["name"] for r in on_base[:8]),
        "" if not on_base else "GitHub deletes a head branch when its PR merges; an agent never deletes a remote branch: "
                               "fieldkit ship prune lists the rest for the owner")
    if not offline:
        unproven, unknown = cls["unproven"], cls["unknown"]
        row("branches", not unproven,
            "every remote branch's work is on " + base if not unproven else
            f"{len(unproven)} remote branch(es) carry work {base} lacks, with no merged pull request for that commit: "
            + "; ".join(f"{r['name']} ({r['why']})" for r in unproven[:6]),
            "" if not unproven else "open or finish a pull request for each (fieldkit ship push --auto), or say why it stays out")
        if unknown:
            row("branches-unknown", False, f"{len(unknown)} remote branch(es) could not be classed: "
                + "; ".join(f"{r['name']} ({r['why']})" for r in unknown[:6]), "gh auth status, then fieldkit ship audit")
        stale = cls["merged-pr"] + cls["content-on-base"]
        if stale:
            row("stale-branches", False, "merged (same head commit) or squash-merged and still on the remote: "
                + ", ".join(f"{r['name']} ({r['why']})" for r in stale[:8]),
                "fieldkit ship prune (report only: the owner deletes remote branches)")
    if not offline:
        out, err = gh(["pr", "list", "-R", slug, "--state", "open", "--json", "number,title,headRefName,statusCheckRollup"]) \
            if slug else (None, "no GitHub remote")
        if out is None:
            row("pulls", False, f"open pull requests not readable: {ghhidden.explain(err[:120])}",
                "" if ghhidden.gh_hidden() else "gh auth status")
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
                "" if not prs else "green ones: on their branch, fieldkit ship push --auto (GitHub merges on green CI); "
                                   "red ones: fix on the branch, then fieldkit ship ci-wait")
    _local_leftovers(repo, base, row)
    gap = release_gap(repo, base)
    due = gap["commits"] > max_commits or (gap["days"] is not None and gap["days"] > max_days and gap["commits"] > 0)
    row("release", not due,
        (f"origin/{base} is {gap['commits']} commit(s) and {gap['days']} day(s) past {gap['tag']}" if gap["tag"] else
         f"no v* release tag yet; {gap['commits']} commit(s) on {base}") + (": a release is DUE" if due else ""),
        "" if not due else "fieldkit ship release vX.Y.Z --scaffold, write the notes, then fieldkit ship release vX.Y.Z")
    return {"ok": all(r["state"] == "OK" for r in rows), "rows": rows, "gap": gap}


def _worktrees(repo):
    """-> [{worktree, branch, HEAD, ...}] from `git worktree list --porcelain`; the first is the main checkout."""
    out, cur = [], {}
    for ln in git(repo, "worktree", "list", "--porcelain").stdout.splitlines() + [""]:
        if not ln.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = ln.partition(" ")
        cur[k] = v or True
    return out


def _on_base(repo, ref, base):
    return (git(repo, "merge-base", "--is-ancestor", ref, f"origin/{base}").returncode == 0
            or content_on_base(repo, ref, base))


def _local_leftovers(repo, base, row):
    """Local worktrees and branches left after the work is done (owner, 2026-10-23: "when the release cycle is finished
    nothing is left forgotten unmerged on some stray branch"). Git only, so offline too; nothing is removed here."""
    wts = _worktrees(repo)
    extra = [w for w in wts[1:] if "prunable" not in w]
    said = []
    for w in extra:
        ref = w.get("branch") or w.get("HEAD")
        name = str(w.get("branch") or "").removeprefix("refs/heads/") or "detached"
        said.append(f"{w['worktree']} ({name}: {'its work is on ' + base if _on_base(repo, ref, base) else 'work NOT on ' + base})")
    row("worktrees", not extra, "no extra worktree" if not extra else f"{len(extra)} extra worktree(s): " + "; ".join(said[:6]),
        "" if not extra else "work on the base: git worktree remove PATH (it refuses uncommitted changes); "
                             "work not on it: push the branch and open a pull request")
    checked_out = {str(w.get("branch") or "").removeprefix("refs/heads/") for w in wts}
    unpushed, merged = [], []
    for b in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split():
        if b == base:
            continue
        n = git(repo, "rev-list", "--count", b, "--not", "--remotes").stdout.strip()
        if n and int(n) > 0:
            unpushed.append(f"{b} ({n})")
        elif b not in checked_out and _on_base(repo, b, base):
            merged.append(b)
    row("local-branches", not unpushed and not merged,
        "no local branch left over" if not (unpushed or merged) else
        "; ".join(([f"commits on no remote: {', '.join(unpushed[:6])}"] if unpushed else []) +
                  ([f"merged into {base}, still here: {', '.join(merged[:6])}"] if merged else [])),
        "" if not (unpushed or merged) else
        "unpushed: push and open a pull request (fieldkit ship push); merged: git branch -d NAME (refuses unmerged)")


def prune(repo=".", base="main", archive_unproven=False, gh=_gh):
    """One source of truth, without deleting on GitHub (hard line, 2026-11-25: an agent never deletes a remote branch
    or pushes a tag outside fieldkit ship push). Remote branches are classed by shiprefs (same repo, same base, same
    head commit) and reported; the deletions the proof allows are written as ONE owner command. archive_unproven adds
    archive/<branch> tags for unproven branches to that command (tag first, then delete). Local branches merged into
    the base are removed with git branch -d (git refuses an unmerged one). Never touches the base.
    -> {"ok", "rows", "deleted": [] (always), "archived": [] (always), "kept", "local", "refused", "deletable",
        "owner_command"}"""
    git(repo, "fetch", "--quiet", "--prune", "origin")
    slug = repo_slug(repo)
    res = shiprefs.classify(repo, base, gh=gh if slug else None, slug=slug)
    if slug and res["errors"]:      # without GitHub an open PR's head looks unproven: propose nothing (2026-11-09)
        why = ghhidden.explain("; ".join(res["errors"])[:160])
        return {"ok": False, "deleted": [], "archived": [], "kept": [], "local": [], "refused": [], "deletable": [],
                "owner_command": "",
                "rows": [{"check": "github", "state": "DO", "say": f"pull requests not readable ({why}): nothing was proposed",
                          "run": "run it where gh is signed in (not inside an autopilot run)"}]}
    cls = shiprefs.by_class(res)
    deletable = sorted(r["name"] for c in ("on-base", "merged-pr", "content-on-base") for r in cls[c])
    kept = sorted(r["name"] for r in cls["unproven"])
    current = git(repo, "branch", "--show-current").stdout.strip()
    local = []
    for b in git(repo, "branch", "--merged", f"origin/{base}", "--format=%(refname:short)").stdout.split():
        if b not in (base, current) and git(repo, "branch", "-d", b).returncode == 0:
            local.append(b)
    top = str(Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo))
    gitexe = '& "C:\\Program Files\\Git\\cmd\\git.exe"'
    cmd = []
    for b in deletable:
        cmd.append(f'{gitexe} push origin --delete "refs/heads/{b}"')
    if archive_unproven:
        for b in kept:
            cmd.append(f'{gitexe} tag -a "archive/{b}" -m "archived: work not proven on {base}" "origin/{b}"')
            cmd.append(f'{gitexe} push origin "refs/tags/archive/{b}"')
            cmd.append(f'{gitexe} push origin --delete "refs/heads/{b}"')
    owner_cmd = "\n".join([f'cd "{top}"'] + cmd) if cmd else ""
    rows = [{"check": "deletable", "state": "OK" if not deletable else "DO",
             "say": "no remote branch proven merged" if not deletable else
             f"{len(deletable)} remote branch(es) proven merged (same head commit or on {base}): {', '.join(deletable[:12])}",
             "run": "" if not deletable else "the owner decides: the command below (an agent never deletes on GitHub)"},
            {"check": "kept", "state": "OK" if not kept else "DO", "say": "nothing unproven" if not kept else
             f"{len(kept)} branch(es) with work not proven on {base}, kept: "
             + "; ".join(f"{r['name']} ({r['why']})" for r in cls["unproven"][:8]),
             "run": "" if not kept else "look at each: open a pull request (fieldkit ship push --auto), "
                                        "or the owner archives it (fieldkit ship prune --archive-unproven writes the command)"},
            {"check": "local", "state": "OK", "say": f"{len(local)} merged local branch(es) removed"}]
    if owner_cmd:
        rows.append({"check": "owner-command", "state": "DO", "say": "remote branch and tag changes on GitHub are the owner's",
                     "run": owner_cmd})
    return {"ok": not kept and not deletable, "rows": rows, "deleted": [], "archived": [], "kept": kept,
            "local": local, "refused": [], "deletable": deletable, "owner_command": owner_cmd}


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


def release(tag, repo=".", base="main", scaffold_notes=False, wait=900, gh=_gh, sleep=time.sleep, py=sys.executable, hotfix="", gate=None):
    """-> {"ok", "rows": [...], "next"}"""
    from . import releasecandidate

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
    blocker = releasecandidate.legacy_blocker(repo, slug, tag, base)
    if blocker:
        row("candidate", False, blocker)
        return {"ok": False, "rows": rows}
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
    from . import tagguard
    parity = tagguard.parity_row(repo, head)
    if parity is not None and not row("parity", *parity):
        return {"ok": False, "rows": rows, "next": tagguard.PARITY_FIX}
    files = {n: notes / f"{n}.md" for n in ("opening", "layman", "developer")}
    missing = [str(f) for f in files.values() if not f.is_file()]
    if not row("notes", not missing, "every note file present" if not missing else f"missing {missing}: --scaffold writes them"):
        return {"ok": False, "rows": rows}
    page = notes / "page.md"
    comp = fkproc.run([py, "-m", "fieldkit", "release-page", "compose", "--opening", str(files["opening"]),
                           "--layman", str(files["layman"]), "--developer", str(files["developer"]), "--out", str(page)]
                          + (["--extra", str(notes / "extra.md")] if (notes / "extra.md").is_file() else []),
                          capture_output=True, text=True, cwd=repo)
    chk = fkproc.run([py, "-m", "fieldkit", "release-page", "check", str(page), "--layman", str(files["layman"]),
                          "--developer", str(files["developer"])], capture_output=True, text=True, cwd=repo)
    committed = not git(repo, "status", "--porcelain", "--", str(page)).stdout.strip()
    if not row("page", comp.returncode == 0 and chk.returncode == 0 and committed,
               (chk.stdout or chk.stderr).strip()[-200:] + ("" if committed else "; the composed page differs from the "
                                                            "committed one: commit it through a pull request first")):
        return {"ok": False, "rows": rows}
    from . import releasegate                                  # one release per batch (owner order 2026-11-15)
    g = (gate or releasegate.guard)(repo_slug(repo), tag, hotfix)
    if not row("batch-window", g["ok"], g.get("error") or g["state"]):
        return {"ok": False, "rows": rows, "next": g.get("next", "")}
    if not row("tag-free", not _rev(repo, f"refs/tags/{tag}"), f"{tag} {'already exists' if _rev(repo, f'refs/tags/{tag}') else 'is free'}"):
        return {"ok": False, "rows": rows}
    git(repo, *_ident(repo), "tag", "-a", tag, "-m", f"Release {tag}", head, check=True)
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
