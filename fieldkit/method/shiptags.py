# Version: 1.0.0 · updated 26-11-09-16-00
"""Tags and every owner repo, for `fieldkit ship tags` and `fieldkit ship prune|audit --all` (owner, 2026-11-09:
"obviously we should not delete tags indiscriminately"; use git's own commands, not a new tool).

    fieldkit ship tags [--repo R | --all] [--fix-local]     read-only; --fix-local removes local-only stray tags

Every tag comes from `git ls-remote --tags origin` and `git tag --sort=-creatordate`, and is classed:
  release              v* with a GitHub release            version-no-release   v* without one
  archive              archive/* (what `ship prune --archive-unproven` saved: restorable work, never a candidate)
  other                anything else
Candidates (the only things flagged):
  unreachable   the tag's commit is on no remote branch (work only the tag holds; GitHub can still serve it)
  duplicate     another non-archive tag points at the same commit
  local-only    a local tag never pushed (the tool may remove it with --fix-local: `git tag -d`, logged)
  remote-only   a remote tag this clone lacks (information only)
A tag on GitHub is NEVER deleted or moved by an agent (hard line): the deletions the candidates suggest are written as
ONE owner command (PowerShell, full paths), run by the owner.

--all: every non-archived repo of the owner (`gh repo list`); the local clone if one is found under the usual roots,
else a scratch clone; one before/after table.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .ship import git
from . import shiprelease as sr

VERSION = re.compile(r"v\d+(\.\d+)*")


def _lines(r):
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


def remote_tags(repo):
    """-> {tag: commit} from `git ls-remote --tags origin` (the peeled commit of an annotated tag)."""
    obj, peeled = {}, {}
    for l in _lines(git(repo, "ls-remote", "--tags", "origin")):
        sha, _, ref = l.partition("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            obj[name] = sha
    return {n: peeled.get(n, s) for n, s in obj.items()}


def local_tags(repo):
    """-> [tag] newest first, from `git tag --sort=-creatordate`."""
    return _lines(git(repo, "tag", "--sort=-creatordate"))


def classify(tag, releases):
    if tag.startswith("archive/"):
        return "archive"
    if VERSION.fullmatch(tag):
        return "version-unchecked" if releases is None else "release" if tag in releases else "version-no-release"
    return "other"


def tags(repo=".", gh=sr._gh, fix_local=False):
    """-> {"ok", "rows", "counts", "candidates", "removed", "owner_command"}. Read-only unless fix_local, which only
    removes local tags that were never pushed. Never touches a remote."""
    slug = sr.repo_slug(repo)
    remote = remote_tags(repo)
    local = local_tags(repo)
    out, _ = gh(["release", "list", "-R", slug, "--limit", "1000", "--json", "tagName", "-q", ".[].tagName"]) if slug else (None, "")
    releases = set(out.split()) if out is not None else None        # None: GitHub unreadable, never guess a class
    classes = {t: classify(t, releases) for t in set(remote) | set(local)}
    counts = {}
    for c in classes.values():
        counts[c] = counts.get(c, 0) + 1
    cand = []                                                     # (tag, kind, say)
    by_commit = {}
    for t, c in remote.items():
        if classes[t] != "archive":
            by_commit.setdefault(c, []).append(t)
    for t, c in sorted(remote.items()):
        if classes[t] == "archive":
            continue
        have = git(repo, "cat-file", "-e", f"{c}^{{commit}}").returncode == 0
        if have and not _lines(git(repo, "for-each-ref", "refs/remotes", "--contains", c, "--count=1",
                                   "--format=%(refname)")):
            cand.append((t, "unreachable", f"{t}: its commit {c[:10]} is on no remote branch"))
        twins = [x for x in by_commit[c] if x != t]
        if twins:
            cand.append((t, "duplicate", f"{t}: same commit as {', '.join(sorted(twins)[:3])}"))
    for t in local:
        if t not in remote:
            cand.append((t, "local-only", f"{t}: local tag never pushed"))
    missing = sorted(t for t in remote if t not in local)
    removed = []
    if fix_local:
        for t, kind, _ in cand:
            if kind == "local-only" and git(repo, "tag", "-d", t).returncode == 0:
                removed.append(t)
    deletable = sorted({t for t, k, _ in cand if k in ("unreachable", "duplicate")})
    top = str(Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip() or repo))
    owner_cmd = ""
    if deletable:
        owner_cmd = "\n".join([f'cd "{top}"'] + [f'& "C:\\Program Files\\Git\\cmd\\git.exe" push origin --delete "refs/tags/{t}"'
                                                for t in deletable])
    rows = [{"check": "tags", "state": "OK", "say": f"{len(classes)} tag(s): " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))}]
    for kind, why in (("unreachable", "work only the tag holds"), ("duplicate", "two tags, one commit"),
                      ("local-only", "never pushed")):
        mine = [s for t, k, s in cand if k == kind and not (kind == "local-only" and t in removed)]
        rows.append({"check": kind, "state": "OK" if not mine else "DO", "say": f"no {kind} tag" if not mine else
                     f"{len(mine)} tag(s) ({why}): " + "; ".join(mine[:8]),
                     "run": "" if not mine else ("fieldkit ship tags --fix-local" if kind == "local-only" else
                                                 "the owner decides: the command below")})
    if removed:
        rows.append({"check": "removed", "state": "OK", "say": f"{len(removed)} local-only tag(s) removed (git tag -d): {', '.join(removed)}"})
    if missing:
        rows.append({"check": "remote-only", "state": "OK", "say": f"{len(missing)} remote tag(s) not in this clone: {', '.join(missing[:6])}"})
    if owner_cmd:
        rows.append({"check": "owner-command", "state": "DO", "say": "tag deletion on GitHub is the owner's (an agent never deletes or moves a tag)",
                     "run": owner_cmd})
    return {"ok": not any(r["state"] == "DO" for r in rows), "rows": rows, "counts": counts,
            "candidates": [{"tag": t, "kind": k} for t, k, _ in cand], "removed": removed, "owner_command": owner_cmd}


# ---- every owner repo -------------------------------------------------------------------------------------------

def owner_repos(gh=sr._gh):
    """-> [{"slug", "base"}] every non-archived repo of the authenticated owner."""
    out, err = gh(["repo", "list", "--no-archived", "--limit", "200", "--json", "nameWithOwner,defaultBranchRef"])
    if out is None:
        raise RuntimeError(f"gh repo list failed: {err[:200]}")
    return [{"slug": r["nameWithOwner"], "base": (r.get("defaultBranchRef") or {}).get("name") or "main"}
            for r in json.loads(out or "[]")]


def roots():
    home = Path.home()
    return [p for p in (home / "Documents" / "Scripts", home / "Documents", home / "source", home / "repos") if p.is_dir()]


def find_local(slug, scan=roots):
    """A clone of slug under the usual roots (depth 2), else None."""
    want = slug.lower()
    for root in scan():
        for d in [root, *[c for c in root.iterdir() if c.is_dir()]]:
            if (d / ".git").is_dir() and (sr.repo_slug(d) or "").lower() == want:
                return d
    return None


def scratch_clone(slug):
    base = "C:\\fk-scratch" if os.name == "nt" and Path("C:\\fk-scratch").is_dir() else None
    d = tempfile.mkdtemp(prefix="fk-all-", dir=base)
    r = subprocess.run(["gh", "repo", "clone", slug, str(Path(d) / "r")], capture_output=True, text=True, timeout=600)
    return (Path(d) / "r", d) if r.returncode == 0 else (None, d)


def snapshot(repo, base, gh):
    """Before/after numbers: remote branches, tags, open PRs, extra worktrees."""
    slug = sr.repo_slug(repo)
    brs = [b for b in _lines(git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"))
           if b not in ("origin", "origin/HEAD", f"origin/{base}")]
    out, _ = gh(["pr", "list", "-R", slug, "--state", "open", "--json", "number", "-q", "length"]) if slug else (None, "")
    return {"branches": len(brs), "tags": len(remote_tags(repo)), "prs": int((out or "0").strip() or 0),
            "worktrees": len(sr._worktrees(repo)) - 1}


def run_all(action, archive_unproven=False, gh=sr._gh, repos=owner_repos, find=find_local, clone=scratch_clone):
    """action: prune | audit | tags -> {"ok", "table": [{repo, before, after, ...}], "rows"}; one repo failing never stops the rest."""
    table, scratch = [], []
    try:
        listed = repos(gh)
    except RuntimeError as e:                                        # gh signed out or hidden: say so, no traceback
        return {"ok": False, "table": [], "scratch": [], "error": str(e)}
    for r in listed:
        slug, base = r["slug"], r["base"]
        entry = {"repo": slug, "base": base, "where": "local"}
        path = find(slug)
        if path is None:
            path, d = clone(slug)
            scratch.append(d)
            entry["where"] = "scratch clone"
        if path is None:
            entry.update(ok=False, say="no local clone and the scratch clone failed")
            table.append(entry)
            continue
        try:
            git(path, "fetch", "--quiet", "--prune", "origin")
            entry["before"] = snapshot(path, base, gh)
            if action == "prune":
                res = sr.prune(path, base, archive_unproven=archive_unproven, gh=gh)
            elif action == "audit":
                res = sr.audit(path, base, gh=gh)
            else:
                res = tags(path, gh=gh)
            entry["after"] = snapshot(path, base, gh)
            entry["ok"] = res["ok"]
            entry["rows"] = [x for x in res["rows"] if x["state"] != "OK"]
            if action == "tags":
                entry["counts"] = res["counts"]
        except Exception as e:                                       # noqa: BLE001 - one repo never stops the rest
            entry.update(ok=False, say=f"{type(e).__name__}: {str(e)[:160]}")
        table.append(entry)
    return {"ok": all(e.get("ok") for e in table), "table": table, "scratch": scratch}


def all_lines(d):
    out = [f"  DO  {d['error']}"] if d.get("error") else []
    for e in d["table"]:
        b, a = e.get("before"), e.get("after") or {}             # a repo that failed mid-way has no after
        nums = "" if not b else "  ".join(f"{k} {b[k]}->{a.get(k, '?')}" for k in ("branches", "tags", "prs", "worktrees"))
        out.append(f"  {'OK ' if e.get('ok') else 'DO '} {e['repo']} [{e['where']}] {nums} {e.get('say', '')}".rstrip())
        for x in e.get("rows", []):
            out.append(f"        {x['check']}: {x['say']}")
    return out


def cleanup(d):
    """Remove only the scratch clones this run made (they are under the scratch root; nothing runs from them)."""
    for s in d.get("scratch", []):
        shutil.rmtree(s, ignore_errors=True)
