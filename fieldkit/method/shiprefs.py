# Version: 1.0.0 · updated 26-11-25-12-00
"""Read-only classifier for remote branches, for `fieldkit ship audit` and `fieldkit ship prune` (2026-11-25).

A pull request proves a branch only when it is the SAME work: same repository, same base, same head commit. The
old audit matched by branch NAME, so a reused name (fix/repair-rollback: PRs #609, #642, #643 merged with other
head commits) made new, unmerged work look merged, and prune deleted it as stale.

    classify(repo, base, gh) -> {"ok", "refs": [{name, sha, cls, why, prs}], "errors": [...]}

Classes (first match wins):
  on-base          the ref's commit is an ancestor of origin/<base> (git proves it)
  open-pr          an open PR, same repo, same base, head commit == the ref's commit
  merged-pr        a merged PR, same repo, same base, head commit == the ref's commit
  content-on-base  every change the ref makes since it left the base is in origin/<base> (squash merge)
  unknown          GitHub could not be read for this ref, or an open PR on the name has another head (never empty)
  unproven         none of the above: work the base may lack (PRs on the same name with other heads are listed)
Nothing here writes to git or GitHub.
"""
import json

from .ship import git

PR_FIELDS = "number,state,headRefOid,baseRefName,headRefName,isCrossRepository,headRepository,headRepositoryOwner"
CLASSES = ("on-base", "open-pr", "merged-pr", "content-on-base", "unknown", "unproven")


def remote_refs(repo, base="main"):
    """-> [(name, sha)] for refs/remotes/origin/* except the base and HEAD (the caller fetched first)."""
    out = git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin").stdout
    refs = []
    for line in out.splitlines():
        ref, _, sha = line.strip().partition(" ")
        if not sha or ref == "origin":
            continue
        name = ref.split("/", 1)[-1]
        if name not in (base, "HEAD"):
            refs.append((name, sha))
    return refs


def same_repo(pr, slug):
    """True when the PR's head lives in slug itself (not a fork that reused the branch name)."""
    if pr.get("isCrossRepository"):
        return False
    hr, ho = pr.get("headRepository") or {}, pr.get("headRepositoryOwner") or {}
    name, owner = hr.get("name"), ho.get("login")
    if name and owner and slug:
        return f"{owner}/{name}".lower() == slug.lower()
    return True                              # gh omitted the fields: isCrossRepository false is the proof


def matching(prs, slug, base, sha):
    """PRs that prove this ref: same repo, same base, same head commit."""
    return [p for p in prs if same_repo(p, slug) and p.get("baseRefName") == base and p.get("headRefOid") == sha]


def pr_lookup(gh, slug, name):
    """-> (prs, error). Every PR (any state) whose head branch is called name; error is a string on failure."""
    out, err = gh(["pr", "list", "-R", slug, "--state", "all", "--head", name, "--limit", "200", "--json", PR_FIELDS])
    if out is None:
        return None, (err or "gh failed")[:200]
    try:
        return json.loads(out or "[]"), ""
    except ValueError as e:
        return None, f"unreadable gh output: {e}"


def classify_ref(repo, base, name, sha, slug, gh, content_on_base):
    ref = {"name": name, "sha": sha, "cls": "", "why": "", "prs": []}
    if git(repo, "merge-base", "--is-ancestor", sha, f"origin/{base}").returncode == 0:
        ref.update(cls="on-base", why=f"its commit is in origin/{base}")
        return ref
    prs, err = (pr_lookup(gh, slug, name) if slug else ([], ""))
    if prs is None:
        ref.update(cls="unknown", why=f"pull requests not readable: {err}")
        return ref
    mine = matching(prs, slug, base, sha)
    ref["prs"] = [p.get("number") for p in mine]
    if any(p.get("state") == "OPEN" for p in mine):
        ref.update(cls="open-pr", why="open PR #" + ", #".join(str(p["number"]) for p in mine if p.get("state") == "OPEN"))
        return ref
    if any(p.get("state") == "MERGED" for p in mine):
        ref.update(cls="merged-pr", why="merged PR #" + ", #".join(str(p["number"]) for p in mine if p.get("state") == "MERGED"))
        return ref
    if content_on_base(repo, f"origin/{name}", base):
        ref.update(cls="content-on-base", why=f"its changes are on {base}")
        return ref
    other = [p for p in prs if p not in mine]
    moved = [p for p in other if p.get("state") == "OPEN" and same_repo(p, slug) and p.get("baseRefName") == base]
    if moved:
        ref.update(cls="unknown", why=f"open PR #{moved[0]['number']} on this name has head "
                                      f"{str(moved[0].get('headRefOid'))[:10]}, the ref is {sha[:10]}: fetch again")
        return ref
    say = "work not proven on " + base
    if other:
        say += "; PRs on the same name with another head: " + ", ".join(f"#{p.get('number')} {p.get('state')}" for p in other[:6])
    ref.update(cls="unproven", why=say)
    return ref


def classify(repo=".", base="main", gh=None, slug=None, content_on_base=None):
    """-> {"ok", "refs", "errors"}; ok false when any ref is unknown. gh=None means offline: no PR is read."""
    from . import shiprelease as sr
    content_on_base = content_on_base or sr.content_on_base
    if gh is not None and slug is None:
        slug = sr.repo_slug(repo)
    refs, errors = [], []
    for name, sha in remote_refs(repo, base):
        r = classify_ref(repo, base, name, sha, slug if gh is not None else None, gh, content_on_base)
        refs.append(r)
        if r["cls"] == "unknown":
            errors.append(f"{name}: {r['why']}")
    return {"ok": not errors, "refs": refs, "errors": errors}


def by_class(result):
    out = {c: [] for c in CLASSES}
    for r in result["refs"]:
        out[r["cls"]].append(r)
    return out
