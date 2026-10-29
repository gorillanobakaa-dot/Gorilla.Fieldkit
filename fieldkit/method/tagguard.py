# Version: 1.0.0 · updated 26-10-29
"""A tag is never moved, so a tag that cannot publish is refused BEFORE it is pushed (docs/LESSONS.yaml L-20261029-1).

    fieldkit ship release-ready vX.Y.Z    the checks below as a dry run: every row, in order, with the command that fixes it

`fieldkit ship push --tag vX.Y.Z` runs the same checks at the tagged commit and pushes nothing unless all pass:
  1. tag-free   X is not on origin (a dry run also wants it absent locally: nothing is tagged until the rest is ready)
  2. version    pyproject.toml / package version places / <pkg>/__init__.py all say X (version-sync, read at the commit)
  3. notes      releases/notes/X/{opening,layman,developer,page}.md exist at the commit
  4. page       `fieldkit release-page check` passes on that page with its layman and developer files
  5. ci         GitHub's CI for the commit is green, with a job for every system in the repository's mandate ci_os

Git, gh and the page check are injected, so the tests touch no network. Exit 0 ready, 3 not ready.
"""
import re
import subprocess
import sys
import tempfile
from pathlib import Path

NOTES = ("opening", "layman", "developer", "page")
VERSION_FILES = ("pyproject.toml", "Cargo.toml", "Cargo.lock", "package.json", "PKGBUILD", "VERSION", "CHANGELOG.md",
                 "packaging/changelog.in")


def _git(repo, *a, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=timeout)


def _show(repo, commit, path):
    r = _git(repo, "show", f"{commit}:{path}")
    return r.stdout if r.returncode == 0 else None


def ci_os_of(repo):
    """The `ci_os:` the mandate in force names for this repository (none -> [])."""
    try:
        from ..mandate import mandate as M
        from ..mandate import proofs
        name, _ = proofs.where(repo)
        for p in M.mandates():
            cfg = M.repo_cfg(M.load(p), name)
            if cfg is not None:
                return list(cfg.get("ci_os") or [])
    except Exception:  # noqa: BLE001 - a mandate that cannot be read adds no requirement; the mandate gate still runs
        pass
    return []


def page_check(repo, commit, tag, py=sys.executable):
    """-> (ok, say): release-page check on the page and files as committed at `commit`, read into a temp folder."""
    with tempfile.TemporaryDirectory() as td:
        files = {}
        for n in NOTES:
            text = _show(repo, commit, f"releases/notes/{tag}/{n}.md")
            if text is None:
                return False, f"releases/notes/{tag}/{n}.md is not at {commit[:10]}"
            files[n] = Path(td) / f"{n}.md"
            files[n].write_text(text, encoding="utf-8", newline="\n")
        r = subprocess.run([py, "-m", "fieldkit", "release-page", "check", str(files["page"]), "--layman",
                            str(files["layman"]), "--developer", str(files["developer"])],
                           capture_output=True, text=True, cwd=str(repo))
        say = (r.stdout or r.stderr).strip()[-200:]
        return r.returncode == 0, say or "release-page check passed"


def version_rows(repo, commit, tag):
    """-> list of problems: places at `commit` whose version is not the tag's."""
    want = tag[1:]
    from . import versionsync
    problems = []
    with tempfile.TemporaryDirectory() as td:
        names = _git(repo, "ls-tree", "-r", "--name-only", commit).stdout.splitlines()
        for name in names:
            top = "/" not in name
            if (name in VERSION_FILES) or (top and name.endswith(".iss")) or re.fullmatch(r"[^/]+/__init__\.py", name):
                text = _show(repo, commit, name)
                if text is None:
                    continue
                if name.endswith("__init__.py"):
                    m = re.search(r'(?m)^__version__\s*=\s*"([^"]+)"', text)
                    if m and m.group(1) != want:
                        problems.append(f"{name} says {m.group(1)}")
                    continue
                dest = Path(td) / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8", newline="\n")
        r = versionsync.check(td, want)
        if not r["places"] and not problems:
            problems.append("no version statement found at the commit")
        if not r["ok"]:
            problems += [*(f"disagree: {d}" for d in r["disagree"]), *(f"unreadable: {u}" for u in r["unreadable"])]
    return problems


def check(repo, tag, dry_run=False, ci_os=None, ci=None, page=None, run=subprocess.run):
    """-> {"ok", "rows": [{"check", "ok", "say", "run"}]}. The tagged commit is the tag's (HEAD for a dry run with no tag)."""
    repo = str(repo)
    rows = []

    def row(name, ok, say, fix=""):
        rows.append({"check": name, "ok": bool(ok), "say": say, "run": "" if ok else fix})
        return ok

    if not re.fullmatch(r"v\d+\.\d+\.\d+([-+.][0-9A-Za-z.-]+)?", tag or ""):
        row("tag", False, f"{tag!r} is not a version tag like v1.2.3", "use a tag of the form vX.Y.Z")
        return {"ok": False, "rows": rows}
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    commit = local or _git(repo, "rev-parse", "HEAD").stdout.strip()
    remote = _git(repo, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", timeout=120)
    if remote.returncode != 0:
        row("tag-free", False, f"origin unreadable: {(remote.stderr or '').strip()[:160]}", "git -C . ls-remote origin")
    elif remote.stdout.strip():
        row("tag-free", False, f"{tag} already exists on origin and a tag is never moved: this version is burned",
            "take the next version (fieldkit version-sync . --expect vNEXT)")
    elif dry_run and local:
        row("tag-free", False, f"{tag} already exists locally; make it only when everything below is ready",
            f"git tag -d {tag}   (it is not on origin)")
    else:
        row("tag-free", True, f"{tag} is not on origin")
    probs = version_rows(repo, commit, tag)
    row("version", not probs, f"every version place at {commit[:10]} says {tag[1:]}" if not probs
        else "; ".join(probs), f"fieldkit version-sync . --expect {tag[1:]}   (then fix the places it names)")
    missing = [n for n in NOTES if _show(repo, commit, f"releases/notes/{tag}/{n}.md") is None]
    row("notes", not missing, f"releases/notes/{tag}/ has all of {', '.join(NOTES)}" if not missing
        else f"missing at {commit[:10]}: {', '.join(n + '.md' for n in missing)}",
        f"fieldkit ship release {tag} --scaffold   (write opening, layman, developer), then: fieldkit release-page compose "
        f"--opening releases/notes/{tag}/opening.md --layman releases/notes/{tag}/layman.md --developer "
        f"releases/notes/{tag}/developer.md --out releases/notes/{tag}/page.md   (commit them through a pull request)")
    if not missing:
        ok, say = (page or page_check)(repo, commit, tag)
        row("page", ok, say if ok else say,
            f"fieldkit release-page check releases/notes/{tag}/page.md --layman releases/notes/{tag}/layman.md "
            f"--developer releases/notes/{tag}/developer.md")
    else:
        row("page", False, "not checked: the note files are missing", "fix notes first")
    from ..mandate import proofs
    ok, say = (ci or proofs.ci_green)(repo, commit, {"ci_os": ci_os_of(repo) if ci_os is None else ci_os}, run=run)
    row("ci", ok, say, "wait for CI on that commit (fieldkit ship ci-wait PR) or fix what is red; the tag goes on a green commit")
    return {"ok": all(r["ok"] for r in rows), "commit": commit, "tag": tag, "rows": rows}


def lines(r):
    out = [f"  {'OK  ' if x['ok'] else 'FAIL'} {x['check']:<9} {x['say']}" + (f"\n         fix: {x['run']}" if x["run"] else "")
           for x in r["rows"]]
    if r["ok"]:
        out.append(f"RELEASE-READY: {r['tag']} at {r['commit'][:10]} can be tagged and pushed. Before that: "
                   "fieldkit privacy scan . --git")
    else:
        out.append(f"NOT READY: {r['tag']} would not publish; nothing was pushed. Run the fix lines above in order, "
                   f"then: fieldkit ship release-ready {r['tag']}")
    return out
