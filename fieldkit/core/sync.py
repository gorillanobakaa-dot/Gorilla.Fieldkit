# Version: 1.1.0 · updated 26-10-28-20-45
"""fieldkit sync: the Windows and the Linux machine on the same harness, pulled and verified, never pushed at them.

Owner, 2026-10-16: "the claude from the windows and linux can get in sync, and the harness always stays updated".
Until now the two machines met only through hand-carried handover zips (kits/catchup).

Door statement (CLAUDE.md: every channel is a door). The channel is GitHub (Microsoft, US jurisdiction): whoever
controls or compels it can serve a commit to `git fetch`. What this tool lets through:
  - only on command (a session start or end), never on a timer;
  - only a FAST-FORWARD of the branch this checkout tracks: history that was rewritten or diverged is refused, so a
    replaced commit cannot slip in under an existing one;
  - only a commit whose CI ran green on BOTH windows-latest and ubuntu-latest (the check runs GitHub reports): the
    NEWEST such commit on the upstream's first-parent line, never one past it (2026-10-28, plan item 30: PRs merge
    every few minutes, main's tip is always mid-CI, and a tip-only rule left the L15 30 commits behind);
  - never with uncommitted work in the tree.
Residual: GitHub itself could forge both the commit and its check runs; signed commits checked against a key kept off
GitHub would close that, and are not set up. Private files (local/, memory) are not in git and still travel by
catchup.

    fieldkit sync [--apply] [--repo DIR ...]     report, or take the verified upstream commits (exit 3 when not in sync)
                                                 and install the agent skills of this checkout (an "agent skills" row)

A waiter reads the exit code (0 = every row in sync, 3 = not) or `--json` ({"in_sync", "exit", "repos"}), never the
words: 2026-10-28 a wait matched "IN SYNC" inside "NOT IN SYNC". The summary line is "SYNC: OK" or "SYNC: DIFFERS",
and no text this tool prints for a row not in sync contains "IN SYNC" or "IN-SYNC".
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEED_OS = ("windows", "ubuntu")
# A repository whose job names do not say the system: which job proves which one, as data (plan P14, idea I-11)
OS_JOBS_FILE = Path(__file__).resolve().parents[1] / "method" / "ci_os_jobs.yaml"


def os_jobs(slug, path=None):
    """-> {system: (job names)} for a repository whose CI job names do not say the system (ci_os_jobs.yaml)."""
    import yaml
    try:
        data = yaml.safe_load(Path(path or OS_JOBS_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return {o: tuple(names or ()) for o, names in (data.get(slug) or {}).items()}


def missing_os(runs, need, slug, path=None):
    """-> the systems in `need` with no check run: a job named in ci_os_jobs.yaml for `slug`, else one whose name
    contains the system's word."""
    named = os_jobs(slug, path) if slug else {}
    return [o for o in need if not any(x["name"] in named.get(o, ()) if o in named else o in x["name"].lower()
                                       for x in runs)]


def _git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout.strip()


def _slug(repo):
    url = _git(repo, "remote", "get-url", "origin")
    url = url.split("@github.com")[-1] if "@github.com" in url else url.split("github.com")[-1]
    return url.lstrip(":/").removesuffix(".git")


def _public_check_runs(slug, sha, timeout=15):
    """-> the check runs of a PUBLIC repository's commit from api.github.com, with no credential (the headless job on
    the other computer has none, 2026-10-31). Who learns what: GitHub sees this address ask about this commit, as a
    `git fetch` already tells it. Raises on any failure (the caller fails closed)."""
    import urllib.request
    req = urllib.request.Request(f"https://api.github.com/repos/{slug}/commits/{sha}/check-runs?per_page=100",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "fieldkit-sync"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        data = json.loads(resp.read().decode("utf-8"))
    return [{"name": x.get("name"), "status": x.get("status"), "conclusion": x.get("conclusion")}
            for x in data.get("check_runs") or []]


def ci_green(slug, sha, run=subprocess.run, public=_public_check_runs):
    """-> (ok, evidence): every check run of `sha` completed with success, and NEED_OS each had one. Read with gh;
    when gh cannot (no login, not installed), from the public API without a credential; neither -> not green."""
    try:
        r = run(["gh", "api", f"repos/{slug}/commits/{sha}/check-runs", "--jq",
                 "[.check_runs[] | {name, status, conclusion}]"], capture_output=True, text=True)
        gh_err = (r.stderr or "").strip()[:160] if r.returncode else ""
    except OSError as e:
        r, gh_err = None, str(e)[:160]
    if gh_err:
        try:
            runs = public(slug, sha)
        except Exception as e:  # noqa: BLE001 - fail closed, say both
            return False, f"CI unreadable (gh: {gh_err}; public API: {str(e)[:120]})"
    else:
        runs = json.loads(r.stdout or "[]")
    if not runs:
        return False, f"no CI run for {sha[:10]}"
    bad = [x["name"] for x in runs if x["status"] != "completed" or x["conclusion"] not in ("success", "skipped")]
    if bad:
        return False, f"CI not green for {sha[:10]}: {', '.join(bad[:4])}"
    missing = missing_os(runs, NEED_OS, slug)
    if missing:
        return False, f"CI for {sha[:10]} has no {', '.join(missing)} job"
    return True, f"CI green on {', '.join(NEED_OS)} for {sha[:10]} ({len(runs)} check runs)"


def state(repo, fetch=True):
    repo = Path(repo)
    row = {"repo": str(repo), "ok": False}
    if fetch:
        _git(repo, "fetch", "--quiet", "origin")
    row["branch"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    up = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False)
    if not up:
        row["verdict"], row["why"] = "NO-UPSTREAM", f"{row['branch']} tracks no remote branch"
        return row
    row["upstream"] = up
    ahead, behind = (int(x) for x in _git(repo, "rev-list", "--left-right", "--count", f"HEAD...{up}").split())
    row.update(ahead=ahead, behind=behind, dirty=bool(_git(repo, "status", "--porcelain", "--untracked-files=no")),
               upstream_sha=_git(repo, "rev-parse", up))
    if ahead and behind:
        row["verdict"], row["why"] = "DIVERGED", f"{ahead} local and {behind} upstream commits: a person merges this"
    elif behind:
        row["verdict"], row["why"] = "BEHIND", f"{behind} upstream commit(s) to take"
    elif ahead:
        row["verdict"], row["why"] = "AHEAD", f"{ahead} local commit(s) not pushed (a push needs the owner's yes)"
        row["ok"] = True
    else:
        row["verdict"], row["why"], row["ok"] = "IN-SYNC", f"{row['branch']} = {up}", True
    # the other machine may push to the default branch while this one works on a feature branch: its tools would
    # never arrive here. Not merged by this tool (a person merges); reported, and the row is not in sync.
    default = _git(repo, "rev-parse", "--abbrev-ref", "origin/HEAD", check=False)
    if default and default != up:
        missing = int(_git(repo, "rev-list", "--count", f"HEAD..{default}"))
        row["behind_default"] = missing
        if missing:
            row["ok"] = False
            row["why"] += f"; {default} has {missing} commit(s) this branch lacks: merge {default} in (a person)"
    return row


MAX_WALK = 40   # first-parent commits checked below the tip before giving up (one gh call each)


def apply(row, ci=ci_green):
    """Fast-forward a BEHIND row when the tree is clean, to the newest upstream first-parent commit CI proved on both
    systems. The walk goes from the tip down and stops at the first green one: every commit above it (red, pending
    or cancelled) stays out. A row that stayed below the tip is TAKEN-PART and not ok (the next run takes the rest)."""
    if row.get("verdict") != "BEHIND":
        return row
    if row["dirty"]:
        row["why"] += "; refused: uncommitted changes in the tree"
        return row
    slug = _slug(row["repo"])
    line = _git(row["repo"], "rev-list", "--first-parent", f"--max-count={MAX_WALK}", f"HEAD..{row['upstream']}").split()
    skipped, target, ev = [], None, "no upstream commit to check"
    for sha in line:
        ok, ev = ci(slug, sha)
        if ok:
            target = sha
            break
        skipped.append(ev)
        if ev.startswith("CI unreadable"):
            break
    if target is None:
        row["ci"] = skipped[0] if skipped else ev
        row["why"] += f"; refused: {row['ci']}" + (f" (and {len(skipped) - 1} commit(s) below it)" if len(skipped) > 1 else "")
        return row
    row["ci"] = ev
    _git(row["repo"], "merge", "--ff-only", "--quiet", target)
    left = int(_git(row["repo"], "rev-list", "--count", f"HEAD..{row['upstream']}"))
    took = row["behind"] - left
    if not left:
        row.update(verdict="TAKEN", ok=True, behind=0, why=f"fast-forwarded {took} commit(s); {ev}")
    else:
        row.update(verdict="TAKEN-PART", ok=False, behind=left, taken_sha=target,
                   why=f"fast-forwarded {took} commit(s) to {target[:10]}, the newest green; stayed {left} commit(s) "
                       f"behind the tip: {skipped[0]}; {ev}")
    return row


def repos(extra=()):
    out = [ROOT]
    try:
        from .settings import local_settings
        out += [Path(p) for p in (local_settings().get("sync") or {}).get("repos", [])]
    except Exception:  # noqa: BLE001 - no local settings: Fieldkit alone
        pass
    out += [Path(p) for p in extra]
    seen, uniq = set(), []
    for p in out:
        k = str(p.resolve())
        if k not in seen and (p / ".git").exists():
            seen.add(k)
            uniq.append(p)
    return uniq


def skills_row(do_apply=False, installer=None):
    """The agent skills on this machine against this checkout (2026-10-26, owner: "align our standard with theirs for
    maximum compatibility and interoperability"): every agent folder that reads the Agent Skills standard
    (~/.claude/skills, ~/.agents/skills, ~/.gemini/skills) holds the same set on every machine. --apply installs
    from the checkout just verified (install_skills.py: only Fieldkit's own names and receipted third-party ones)."""
    if installer is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("install_skills", ROOT / "install_skills.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
    stale = [r for r in installer.status() if r[2] in ("missing", "outdated", "off-os")]
    row = {"repo": "agent skills", "ok": True, "verdict": "IN-SYNC", "why": "every installed copy matches"}
    if stale and do_apply:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            installer.main([])
        left = [r for r in installer.status() if r[2] in ("missing", "outdated", "off-os")]
        row.update(verdict="INSTALLED" if not left else "ERROR", ok=not left,
                   why=f"{len(stale) - len(left)} copy(ies) installed" + (f", {len(left)} still stale" if left else ""))
        # an off-os copy leaves only through the recycle bin; when that fails the installer says why: show it
        # (2026-10-31, VAIO: 12 stale for days because gorilla-size was not built, and sync hid the reason)
        kept = sorted({ln.split("(recycle failed: ", 1)[1].rstrip(")") for ln in buf.getvalue().splitlines()
                       if ln.startswith("kept") and "(recycle failed: " in ln})
        if left and kept:
            row["why"] += "; not recycled: " + "; ".join(kept)
    elif stale:
        row.update(verdict="STALE", ok=False, why=f"{len(stale)} copy(ies) missing, outdated or off this OS: fieldkit sync --apply")
    foreign = [f"{t.parent.name}/{n}" for t, n, s in installer.status() if s == "foreign"]
    if foreign:
        row["why"] += "; name taken by someone else's folder, left alone: " + ", ".join(foreign)
    return row


def run(do_apply=False, extra=(), fetch=True):
    rows = []
    for r in repos(extra):
        try:
            row = state(r, fetch=fetch)
            rows.append(apply(row) if do_apply else row)
        except RuntimeError as e:
            rows.append({"repo": str(r), "ok": False, "verdict": "ERROR", "why": str(e)})
    if (ROOT / "install_skills.py").is_file():
        here = next((x for x in rows if Path(x["repo"]).resolve() == ROOT.resolve()), None)
        if here is None or here.get("verdict") in ("IN-SYNC", "TAKEN", "TAKEN-PART", "AHEAD", "NO-UPSTREAM"):
            try:
                rows.append(skills_row(do_apply))
            except Exception as e:  # noqa: BLE001 - a broken installer is reported as a row, never a crash
                rows.append({"repo": "agent skills", "ok": False, "verdict": "ERROR", "why": str(e)})
    return rows


def in_sync(rows):
    return bool(rows) and all(r["ok"] for r in rows)


def exit_code(rows):
    return 0 if in_sync(rows) else 3


def as_json(rows):
    return {"in_sync": in_sync(rows), "exit": exit_code(rows), "repos": rows}


def lines(rows):
    out = []
    for r in rows:
        # a row that is not ok never prints its IN-SYNC verdict (a branch level with upstream but behind main)
        verdict = r["verdict"] if r["ok"] or r["verdict"] != "IN-SYNC" else "LEVEL"
        out.append(f"  [{'ok' if r['ok'] else 'NO'}] {verdict:<11} {r['repo']}: {r['why']}")
    bad = sum(1 for r in rows if not r["ok"])
    out.append("SYNC: OK" if in_sync(rows) else f"SYNC: DIFFERS ({bad or 'no'} of {len(rows)} rows; exit 3)")
    return out
