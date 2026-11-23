# Version: 1.0.0 · updated 26-11-15
"""PERMANENCE: no session's work is lost when the session ends (owner, 2026-11-15: "All these fixes, I bloody hope they
become a permanent part of Fieldkit and don't get lost as soon as the session ends. We do have a deterministic tool to
enforce that, don't we? If not it should be built.").

    fieldkit permanence check [--session SID|TRANSCRIPT] [--repo PATH ...] [--stale-hours H] [--json]

Work is permanent only when it is on main through a merged pull request. The check walks every git worktree of the
repositories `fieldkit sync` knows (Fieldkit, the hub, gorilla-firefox, Gorilla.Opencode, fieldkit.local.json sync.repos,
and --repo) and prints one line per finding:

    UNCOMMITTED  tracked changes, or untracked files not ignored by .gitignore (nothing under state/)
    UNPUSHED     commits not on the branch's upstream, or on no remote at all
    UNMERGED     a pushed branch not in main whose PR is open with no live ci-wait, closed without merge, or never opened
    WAITING      listed, no failure: an open PR with a live (or handed) ci-wait
    PARKED       listed, no failure: three OWN red rounds on the strongest allowed model parked the branch (fieldkit/method/strikes.py)

Every failure line ends with NEXT: the command that makes the work permanent. Exit 3 when there is a failure.
With --session only the worktrees that session touched count (the transcript's Edit / Write / NotebookEdit paths, its
first working folder when that is a linked worktree, and the folder of any Bash call that ran git commit/add/push or
`ship push`); in the shared main checkout only the files it edited itself count (another session's edits are not its own).

Hooks (all fail open: a broken check never traps a session):
    Stop hook (tokens/rotate.py stop_hook)   UNCOMMITTED or UNPUSHED blocks the turn's end once (stop_hook_active never
                                             loops); UNMERGED never blocks (ENDCHECK covers the open PR, ci-wait the rest)
    tool router (autopilot/toolrouter.py)    archive_session of a session, self included, is refused while its check
                                             has UNCOMMITTED or UNPUSHED findings
    keeper tick (lifecycle/keeper.py)        UNPUSHED or UNMERGED work older than 2 h is said in the room for Zero
                                             ("not mine" is no outcome), never a headless model

Reads git and, for a pushed branch that is not in main, `gh pr list`. Writes nothing. Windows and Linux.
"""
import json
import os
import re
import subprocess
from fieldkit import proc as fkproc
import sys
import time
from pathlib import Path

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
GIT_WORDS = re.compile(r"\bgit\s+(commit|add|push)\b|\bship\s+push\b")
BLOCKING = ("UNCOMMITTED", "UNPUSHED")
STALE_HOURS = 2.0
MAIN_NAMES = ("main", "master")


def _git(root, *args, timeout=30):
    """-> stdout of a git command in root, or None when it failed."""
    try:
        r = fkproc.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _gh_prs(root, branch):
    """-> [{number, state, url, headRefOid}] of the PRs whose head is the branch, or None when gh cannot say."""
    try:
        r = fkproc.run(["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "10", "--json",
                            "number,state,url,headRefOid,mergedAt"], cwd=str(root), capture_output=True, text=True,
                           timeout=40, encoding="utf-8", errors="replace")
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


# ------------------------------------------------------------------ what a session touched

def _toplevel(path):
    """The worktree root holding a path (walks up to a .git file or folder; no subprocess), or None."""
    p = Path(path)
    if not p.is_absolute():                               # "C:/work" on Linux is relative: never the current folder
        return None
    for d in [p, *p.parents]:
        if (d / ".git").exists():
            return d
    return None


def _linked(root):
    """True for a linked worktree (its .git is a file): a worktree made for one job, whose changes are all its own."""
    return (Path(root) / ".git").is_file()


def transcript_for(session):
    """-> the transcript path for a session id (or a path), or None."""
    if not session:
        return None
    if Path(str(session)).is_file():
        return Path(session)
    base = Path.home() / ".claude" / "projects"
    hits = sorted(base.glob(f"*/{session}.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def session_scope(transcript):
    """-> (roots, edited): the worktree roots a transcript touched and the absolute paths it edited. Never raises."""
    roots, edited, first = set(), set(), True
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"tool_use"' not in line and not (first and '"cwd"' in line):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                cwd = d.get("cwd")
                if first and cwd:
                    first = False
                    top = _toplevel(cwd)
                    if top and _linked(top):
                        roots.add(top.resolve())
                c = (d.get("message") or {}).get("content")
                for b in c if isinstance(c, list) else []:
                    if b.get("type") != "tool_use":
                        continue
                    ti = b.get("input") or {}
                    if b.get("name") in EDIT_TOOLS:
                        fp = ti.get("file_path") or ti.get("notebook_path")
                        if fp:
                            top = _toplevel(Path(fp).parent)
                            if top:
                                roots.add(top.resolve())
                                edited.add(str(Path(fp).resolve()))
                    elif b.get("name") in ("Bash", "PowerShell") and cwd and GIT_WORDS.search(str(ti.get("command") or "")):
                        top = _toplevel(cwd)
                        if top:
                            roots.add(top.resolve())
    except OSError:
        pass
    return roots, edited


# ------------------------------------------------------------------ the findings

def worktrees(repo):
    """-> the worktree roots of one repository (the main checkout and every linked one)."""
    out = _git(repo, "worktree", "list", "--porcelain")
    roots = [Path(x[9:]) for x in (out or "").splitlines() if x.startswith("worktree ")]
    return [r for r in roots if r.is_dir()] or [Path(repo)]


def _default_ref(root):
    for ref in ("origin/main", "origin/master"):
        if _git(root, "rev-parse", "--verify", "--quiet", ref) is not None:
            return ref
    return None


def _after(merged_at, commit_ts):
    """True when the PR was merged at or after the commit was made: a merged PR's headRefOid may be the merge commit."""
    try:
        import datetime
        t = datetime.datetime.fromisoformat(str(merged_at).replace("Z", "+00:00")).timestamp()
        return t >= int(commit_ts)
    except (ValueError, TypeError):
        return False


def _age_h(root, now):
    t = (_git(root, "log", "-1", "--format=%ct") or "").strip()
    return round((now - int(t)) / 3600, 1) if t.isdigit() else 0.0


def _changes(root, edited, only_edited):
    """-> the changed paths (relative) of a worktree: tracked changes and untracked, not ignored, nothing under state/."""
    out = _git(root, "status", "--porcelain=v1", "-uall", "--no-renames")
    files = []
    for ln in (out or "").splitlines():
        rel = ln[3:].strip().strip('"')
        if not rel or rel.replace("\\", "/").startswith("state/"):
            continue
        if only_edited and str((Path(root) / rel).resolve()) not in edited:
            continue
        files.append(rel)
    return files


def _alive(num, alive):
    from ..method import ci_wake
    return ci_wake.running(num, None, alive or ci_wake.pid_alive) or (ci_wake.waits_dir() / "handed" / f"pr-{num}.json").is_file()


def scan_worktree(root, edited=None, only_edited=False, now=None, gh=None, alive=None, parked=None):
    """-> the findings of one worktree: [{kind, root, branch, detail, next, age_h, blocking}]."""
    now = time.time() if now is None else now
    gh = gh or _gh_prs
    root = Path(root)
    branch = (_git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    branch = "" if branch == "HEAD" else branch
    label = branch or "(detached)"
    out = []

    def add(kind, detail, nxt, age=0.0):
        out.append({"kind": kind, "root": str(root), "branch": branch, "detail": detail, "next": nxt, "age_h": age,
                    "blocking": kind in BLOCKING})
    files = _changes(root, edited or set(), only_edited)
    if files:
        add("UNCOMMITTED", f"{len(files)} file(s): {', '.join(files[:4])}{' ...' if len(files) > 4 else ''}",
            f'cd "{root}" && git add -A && git commit -m "WHY" && fieldkit ship push --auto')
    up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up is not None:
        n = (_git(root, "rev-list", "--count", "@{u}..HEAD") or "0").strip()
        why = f"{n} commit(s) ahead of {up.strip()}"
    else:
        n = (_git(root, "rev-list", "--count", "HEAD", "--not", "--remotes") or "0").strip()
        why = f"{n} commit(s) on no remote (no upstream)"
    default = _default_ref(root)
    if n.isdigit() and int(n) > 0:
        add("UNPUSHED", why, f'cd "{root}" && fieldkit ship push --auto', _age_h(root, now))
    elif branch and branch not in MAIN_NAMES and default and _git(root, "merge-base", "--is-ancestor", "HEAD", default) is None \
            and _git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}") is not None:
        head = (_git(root, "rev-parse", "HEAD") or "").strip()
        prs = gh(root, branch)
        if prs is None:
            return out                                    # gh cannot say: no finding (fail open), the keeper tries again
        ct = (_git(root, "log", "-1", "--format=%ct") or "0").strip()
        merged = [p for p in prs if p.get("state") == "MERGED" and (p.get("headRefOid") == head or _after(p.get("mergedAt"), ct))]
        if merged:
            return out
        age = _age_h(root, now)
        op = [p for p in prs if p.get("state") == "OPEN"]
        if parked and parked(branch):
            add("PARKED", f"{label}: parked: its own CI failures beat every model allowed now, left for the owner or a stronger model", "fieldkit ship ci-wait --help", age)
        elif op:
            num = op[0]["number"]
            if _alive(num, alive):
                add("WAITING", f"{label}: {op[0]['url']} has a ci-wait", f"fieldkit ship ci-wait --merge {num}", age)
            else:
                add("UNMERGED", f"{label}: {op[0]['url']} is open and no ci-wait is running", f"fieldkit ship ci-wait --merge {num}", age)
        elif prs:
            add("UNMERGED", f"{label}: PR {prs[0].get('url') or prs[0]['number']} {prs[0].get('state', '').lower()} without its commits in main",
                f'cd "{root}" && fieldkit ship push --auto', age)
        else:
            add("UNMERGED", f"{label}: pushed, never opened as a PR", f'cd "{root}" && fieldkit ship push --auto', age)
    return out


def scan(repos=None, session=None, now=None, gh=None, alive=None, parked=None):
    """-> every finding. session = a transcript path (scope to what it touched), repos = extra repository roots."""
    from ..core import sync
    if parked is None:
        try:
            from ..method import strikes
            parked = strikes.parked_for
        except Exception:  # noqa: BLE001
            parked = None
    roots_scope, edited = None, set()
    if session:
        roots_scope, edited = session_scope(session)
    found, seen = [], set()
    for repo in sync.repos(extra=repos or ()):
        for wt in worktrees(repo):
            key = str(wt.resolve())
            if key in seen:
                continue
            seen.add(key)
            if roots_scope is not None and wt.resolve() not in roots_scope:
                continue
            only = roots_scope is not None and not _linked(wt)
            try:
                found += scan_worktree(wt, edited, only, now, gh, alive, parked)
            except Exception:  # noqa: BLE001 - one broken worktree never hides the others
                continue
    return found


def line(f):
    return f"{f['kind']:<11} {f['root']} [{f['branch'] or '(detached)'}]: {f['detail']} NEXT: {f['next']}"


def blocking(found):
    return [f for f in found if f["blocking"]]


def stop_reasons(transcript, scan_fn=None):
    """-> the end-of-turn reasons for the Stop hook ([] = the turn may end). Never raises."""
    try:
        fs = blocking((scan_fn or scan)(session=transcript))
        return [f"PERMANENCE: {line(f)}" for f in fs]
    except Exception:  # noqa: BLE001
        return []


def archive_refusal(call, scan_fn=None, records=None):
    """-> the refusal for an archive_session call whose session has UNCOMMITTED or UNPUSHED work, else None."""
    try:
        if not str(call.get("tool_name") or "").endswith("archive_session"):
            return None
        target = str((call.get("tool_input") or {}).get("session_id") or "self")
        tp = call.get("transcript_path")
        if target != "self":
            from . import nested
            recs = nested.load_records() if records is None else records
            cli = (recs.get(target) or {}).get("cliSessionId")
            tp = transcript_for(cli) if cli else None
        if not tp:
            return None
        fs = blocking((scan_fn or scan)(session=tp))
        if not fs:
            return None
        return ("ARCHIVE REFUSED (fieldkit permanence): work that would be lost with this session: "
                + " | ".join(line(f) for f in fs[:4]) + ". Make it permanent, then archive")
    except Exception:  # noqa: BLE001
        return None


def stale(found, hours=STALE_HOURS):
    """-> the UNPUSHED and UNMERGED findings older than `hours` (the keeper says them in the room)."""
    return [f for f in found if f["kind"] in ("UNPUSHED", "UNMERGED") and f["age_h"] >= hours]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit permanence", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["check"])
    ap.add_argument("--session", help="a session id or a transcript path: only the worktrees it touched")
    ap.add_argument("--repo", action="append", default=[], help="one more repository (repeatable)")
    ap.add_argument("--stale-hours", type=float, default=None, help="list only UNPUSHED/UNMERGED work older than this")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    tp = None
    if a.session:
        tp = transcript_for(a.session)
        if tp is None:
            print(f"permanence: no transcript for session {a.session}\nNEXT: fieldkit permanence check", file=sys.stderr)
            return 2
    found = scan(a.repo, tp)
    if a.stale_hours is not None:
        found = stale(found, a.stale_hours)
    bad = [f for f in found if f["kind"] in ("UNCOMMITTED", "UNPUSHED", "UNMERGED")]
    if a.json:
        print(json.dumps({"findings": found, "ok": not bad}, indent=1, ensure_ascii=False))
    else:
        print("\n".join(line(f) for f in found) or "permanence: nothing lost, nothing waiting")
        print("PERMANENCE: " + (f"{len(bad)} finding(s)" if bad else "OK") + "\nNEXT: " +
              (bad[0]["next"] if bad else "nothing"))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
