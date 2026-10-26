# Version: 1.0.0 · updated 26-10-16-22-50
"""fieldkit sync: the Windows and the Linux machine on the same harness, pulled and verified, never pushed at them.

Owner, 2026-10-16: "the claude from the windows and linux can get in sync, and the harness always stays updated".
Until now the two machines met only through hand-carried handover zips (kits/catchup).

Door statement (CLAUDE.md: every channel is a door). The channel is GitHub (Microsoft, US jurisdiction): whoever
controls or compels it can serve a commit to `git fetch`. What this tool lets through:
  - only on command (a session start or end), never on a timer;
  - only a FAST-FORWARD of the branch this checkout tracks: history that was rewritten or diverged is refused, so a
    replaced commit cannot slip in under an existing one;
  - only a commit whose CI ran green on BOTH windows-latest and ubuntu-latest (the check runs GitHub reports);
  - never with uncommitted work in the tree.
Residual: GitHub itself could forge both the commit and its check runs; signed commits checked against a key kept off
GitHub would close that, and are not set up. Private files (local/, memory) are not in git and still travel by
catchup.

    fieldkit sync [--apply] [--repo DIR ...]     report, or take the verified upstream commits (exit 3 when not in sync)
                                                 and install the agent skills of this checkout (an "agent skills" row)
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NEED_OS = ("windows", "ubuntu")


def _git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout.strip()


def _slug(repo):
    url = _git(repo, "remote", "get-url", "origin")
    url = url.split("@github.com")[-1] if "@github.com" in url else url.split("github.com")[-1]
    return url.lstrip(":/").removesuffix(".git")


def ci_green(slug, sha, run=subprocess.run):
    """-> (ok, evidence): every check run of `sha` completed with success, and NEED_OS each had one."""
    r = run(["gh", "api", f"repos/{slug}/commits/{sha}/check-runs", "--jq",
             "[.check_runs[] | {name, status, conclusion}]"], capture_output=True, text=True)
    if r.returncode:
        return False, f"CI unreadable (gh): {(r.stderr or '').strip()[:160]}"
    runs = json.loads(r.stdout or "[]")
    if not runs:
        return False, f"no CI run for {sha[:10]}"
    bad = [x["name"] for x in runs if x["status"] != "completed" or x["conclusion"] not in ("success", "skipped")]
    if bad:
        return False, f"CI not green for {sha[:10]}: {', '.join(bad[:4])}"
    missing = [o for o in NEED_OS if not any(o in x["name"].lower() for x in runs)]
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


def apply(row, ci=ci_green):
    """Fast-forward a BEHIND row when the tree is clean and CI proved the upstream tip on both systems."""
    if row.get("verdict") != "BEHIND":
        return row
    if row["dirty"]:
        row["why"] += "; refused: uncommitted changes in the tree"
        return row
    ok, ev = ci(_slug(row["repo"]), row["upstream_sha"])
    row["ci"] = ev
    if not ok:
        row["why"] += f"; refused: {ev}"
        return row
    _git(row["repo"], "merge", "--ff-only", "--quiet", row["upstream"])
    row.update(verdict="TAKEN", ok=True, why=f"fast-forwarded {row['behind']} commit(s); {ev}")
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
    stale = [r for r in installer.status() if r[2] in ("missing", "outdated")]
    row = {"repo": "agent skills", "ok": True, "verdict": "IN-SYNC", "why": "every installed copy matches"}
    if stale and do_apply:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            installer.main([])
        left = [r for r in installer.status() if r[2] in ("missing", "outdated")]
        row.update(verdict="INSTALLED" if not left else "ERROR", ok=not left,
                   why=f"{len(stale) - len(left)} copy(ies) installed" + (f", {len(left)} still stale" if left else ""))
    elif stale:
        row.update(verdict="STALE", ok=False, why=f"{len(stale)} copy(ies) missing or outdated: fieldkit sync --apply")
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
        if here is None or here.get("verdict") in ("IN-SYNC", "TAKEN", "AHEAD", "NO-UPSTREAM"):
            try:
                rows.append(skills_row(do_apply))
            except Exception as e:  # noqa: BLE001 - a broken installer is reported as a row, never a crash
                rows.append({"repo": "agent skills", "ok": False, "verdict": "ERROR", "why": str(e)})
    return rows


def lines(rows):
    out = []
    for r in rows:
        out.append(f"  [{'ok' if r['ok'] else 'NO'}] {r['verdict']:<11} {r['repo']}: {r['why']}")
    out.append("SYNC: " + ("OK" if all(r["ok"] for r in rows) else "NOT IN SYNC"))
    return out
