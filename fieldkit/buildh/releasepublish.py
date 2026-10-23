# Version: 1.0.0 · updated 26-10-19
"""One-step release: everything proven first by the harness, then ONE owner action that sends it, visibly.

    fieldkit build-harness release-publish TASK prepare repo=DIR tag=TAG title="..." notes=FILE asset=FILE ...
                                                        [supersede=OLDTAG supersede_title="..." supersede_notes=FILE]
    fieldkit build-harness release-publish TASK send    (the owner, at their own terminal: shows the plan, asks yes once)

Owner, 2026-10-19, after a night of pasted commands: "I should NOT have to do this"; "It was supposed to be automatic".
What went wrong that night, and what this does instead:
  - the slow proof (the pre-push hook's set-proof, ~4 min) ran AFTER the yes with its output hidden: prepare runs it
    first, and send streams the push's own output line by line;
  - the release title and "build 1 superseded" were a second pasted block that ran although the first had not
    finished: send does them itself, in order, only after the push, the release and the digest check succeeded;
  - the approval flag (GORILLA_OWNER_APPROVED) was pasted by hand: send sets it for its own push only, and send
    refuses outside the owner's terminal (task.owner_terminal: no agent shell, no agent ancestor);
  - nobody watched the window: every step is logged, the result is spoken by the herald, and the first failure stops
    the run with the step's own output.

prepare (anyone, the agent too; sends nothing): the assets' SHA-256 against SHA256SUMS among them and against
build-verify's record when the task has one, the notes file, the privacy push-check of the range, the set-proof the
pre-push hook will run, the tag must not exist yet on GitHub (read-only), then a plan file
state/publish/<tag>.plan.json with all of it. send refuses a plan whose assets changed since prepare.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import task

PLAN_DIR = task.STATE.parent / "publish"


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _git(repo, *a, runner=subprocess.run):
    return runner(["git", "-C", str(repo), *a], capture_output=True, text=True, errors="replace")


def plan_path(tag):
    return PLAN_DIR / f"{tag}.plan.json"


def prepare(task_id, repo, tag, title, notes, assets, supersede=None, supersede_title=None, supersede_notes=None,
            gh_repo=None, pushes=None, ci=None, checks=None, runner=subprocess.run, say=print):
    """-> {"ok", "rows", "plan"}; writes the plan file when every row passes."""
    rows = []

    def row(check, ok, evidence):
        rows.append({"check": check, "ok": bool(ok), "evidence": evidence})
        say(f"  [{'ok' if ok else 'FAIL'}] {check}: {evidence}")
    repo = Path(repo)
    assets = [Path(a) for a in assets]
    missing = [str(a) for a in assets if not a.is_file()]
    row("every asset exists", not missing, f"{len(assets)} asset(s); missing {missing}")
    if missing:
        return {"ok": False, "rows": rows, "plan": None}
    hashes = {a.name: _sha(a) for a in assets}
    from ..autopilot import inbox
    blockers = inbox.sums_blockers([str(a) for a in assets])
    row("SHA256SUMS lists exactly the assets, with their hashes", not blockers, "; ".join(blockers) or "all match")
    br = task.STATE / task_id / "build-result.json"
    if br.is_file():
        res = json.loads(br.read_text(encoding="utf-8"))
        bad = [n for n, a in res.get("artifacts", {}).items()
               if Path(a["file"]).name in hashes and hashes[Path(a["file"]).name] != a.get("sha256")]
        row("the assets are the files build-verify proved", not bad, f"differ: {bad}" if bad else "every built file matches")
    row("the release notes exist", Path(notes).is_file(), str(notes))
    if supersede_notes:
        row("the superseded page text exists", Path(supersede_notes).is_file(), str(supersede_notes))
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", runner=runner).stdout.strip()
    head = _git(repo, "rev-parse", "HEAD", runner=runner).stdout.strip()
    ahead = _git(repo, "rev-list", "--count", "@{u}..HEAD", runner=runner).stdout.strip() or "0"
    row("the branch and what will be pushed", bool(branch and head), f"{branch} at {head[:10]}, {ahead} commit(s) ahead")
    chk = runner([sys.executable, "-m", "fieldkit", "privacy", "push-check", str(repo), "--range", "@{u}..HEAD"],
                 capture_output=True, text=True, errors="replace")
    row("privacy push-check of the range", chk.returncode == 0, (chk.stdout or chk.stderr).strip().splitlines()[-1:] or "")
    if (repo / "config" / "patch_policy.json").is_file() and (repo / "patches").is_dir():
        sp = runner([sys.executable, "-m", "fieldkit", "build-harness", "set-proof", "patchset=.", "platform=all"],
                    cwd=str(repo), capture_output=True, text=True, errors="replace")
        last = [l for l in (sp.stdout or "").splitlines() if l.startswith("SET-PROOF")]
        row("set-proof on every platform (what the pre-push hook will repeat)", sp.returncode == 0, "; ".join(last) or sp.stderr[-200:])
    if gh_repo:
        ex = runner(["gh", "release", "view", tag, "-R", gh_repo, "--json", "tagName"], capture_output=True, text=True,
                    errors="replace")
        row("the tag is new on GitHub", ex.returncode != 0, "not there yet" if ex.returncode != 0 else f"{tag} already exists")
    ok = all(r["ok"] for r in rows)
    # what is pushed, in order: the branch itself by default; Gorilla OpenCode pushes windows-port and then
    # windows-port:main (GitHub CI runs on main only)
    pushes = list(pushes or [branch])
    plan = {"task": task_id, "repo": str(repo), "gh_repo": gh_repo, "branch": branch, "head": head, "ahead": ahead,
            "pushes": pushes, "ci": list(ci or []),
            # commands that must exit 0 again right before the push (a project without a leak gate binds its release
            # to these: Gorilla OpenCode's public-check, telemetry source and artifact scans; worker, 2026-10-19)
            "checks": list(checks or []),
            "tag": tag, "title": title, "notes": str(notes), "assets": {a.name: {"path": str(a), "sha256": hashes[a.name],
                                                                                    "size": a.stat().st_size} for a in assets},
            "supersede": supersede, "supersede_title": supersede_title, "supersede_notes": str(supersede_notes) if supersede_notes else None,
            "prepared": time.strftime("%Y-%m-%dT%H:%M:%S"), "rows": rows}
    if ok:
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        plan_path(tag).write_text(json.dumps(plan, indent=1), encoding="utf-8")
        say(f"PLAN WRITTEN: {plan_path(tag)}")
    return {"ok": ok, "rows": rows, "plan": plan}


def _stream(cmd, say, env=None, cwd=None, popen=subprocess.Popen):
    """Run with every output line shown as it comes (the 2026-10-19 push sat silent for 4 minutes). -> exit code."""
    p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=env, cwd=cwd)
    for line in p.stdout:
        say("    " + line.rstrip())
    return p.wait()


def send(tag, runner=subprocess.run, popen=subprocess.Popen, say=print, confirm=None, owner=None, speak=None):
    """The owner's one action. -> exit code (0 = live and verified)."""
    owner = task.owner_terminal if owner is None else owner
    if not owner():
        say("REFUSED: sending a release is the owner's, at their own terminal: " + task.agent_shell())
        return 3
    pp = plan_path(tag)
    if not pp.is_file():
        say(f"REFUSED: no prepared plan for {tag} ({pp}): run prepare first")
        return 3
    plan = json.loads(pp.read_text(encoding="utf-8"))
    changed = [n for n, a in plan["assets"].items() if not Path(a["path"]).is_file() or _sha(a["path"]) != a["sha256"]]
    if changed:
        say(f"REFUSED: asset(s) changed since prepare: {changed}")
        return 3
    say(f"RELEASE {plan['tag']}: {plan['title']}")
    say(f"  push {', '.join(plan.get('pushes') or [plan['branch']])} of {plan['repo']} ({plan['ahead']} commit(s), head {plan['head'][:10]}) to {plan['gh_repo']}")
    for n, a in plan["assets"].items():
        say(f"  asset {n}  {a['size']:,} bytes  sha256 {a['sha256']}")
    if plan.get("supersede"):
        say(f"  then mark {plan['supersede']} as: {plan['supersede_title']}")
    if confirm is None:
        confirm = input("Send all of this to GitHub? Type yes: ").strip().lower() == "yes"
    if not confirm:
        say("not sent")
        return 1
    steps = []

    def step(name, ok, detail=""):
        steps.append((name, ok))
        say(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
        return ok
    for c in plan.get("checks") or []:                           # proven again now, not only at prepare time
        cr = runner(c if isinstance(c, list) else ["powershell", "-NoProfile", "-Command", c], capture_output=True,
                    text=True, errors="replace")
        if not step(f"check: {c if isinstance(c, str) else ' '.join(c)}", cr.returncode == 0,
                    (cr.stdout or cr.stderr).strip().splitlines()[-1:] and (cr.stdout or cr.stderr).strip().splitlines()[-1] or ""):
            return _end(steps, say, speak)
    env = dict(os.environ, GORILLA_OWNER_APPROVED="1")          # for this push only: the owner just typed yes
    say("pushing (the repository's own checks run now; their output follows):")
    for ref in plan.get("pushes") or [plan["branch"]]:
        if not step(f"push {ref}", _stream(["git", "-C", plan["repo"], "push", "origin", ref], say, env=env, popen=popen) == 0):
            return _end(steps, say, speak)
    r = runner(["gh", "release", "create", plan["tag"], "-R", plan["gh_repo"], "--target", plan["head"], "--title",
                plan["title"], "--notes-file", plan["notes"], *[a["path"] for a in plan["assets"].values()]],
               capture_output=True, text=True, errors="replace")
    if not step("release created with its title and files", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:]):
        return _end(steps, say, speak)
    v = runner(["gh", "release", "view", plan["tag"], "-R", plan["gh_repo"], "--json", "assets,isDraft"],
               capture_output=True, text=True, errors="replace")
    try:
        got = json.loads(v.stdout or "{}")
        online = {a["name"]: (a.get("digest") or "").replace("sha256:", "") for a in got.get("assets", [])}
    except ValueError:
        got, online = {}, {}
    # 2026-10-23 (v158.0-2): GitHub gave both assets a null digest right after the upload, and "no digest" was read
    # as "differ"; the files were byte-identical. With no digest, the file is downloaded and hashed here instead.
    missing = [n for n in plan["assets"] if n in online and not online[n]]
    if missing:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="gpublish_") as d:
            for n in missing:
                dl = runner(["gh", "release", "download", plan["tag"], "-R", plan["gh_repo"], "-p", n, "-D", d],
                            capture_output=True, text=True, errors="replace")
                f = Path(d) / n
                if dl.returncode == 0 and f.is_file():
                    online[n] = _sha(f)
    bad = [n for n, a in plan["assets"].items() if online.get(n) != a["sha256"]]
    if not step("GitHub's files are byte-for-byte the prepared ones", not bad and not got.get("isDraft"),
                f"differ or missing: {bad}" if bad else "every digest matches"):
        return _end(steps, say, speak)
    if plan.get("supersede"):
        args = ["gh", "release", "edit", plan["supersede"], "-R", plan["gh_repo"], "--title", plan["supersede_title"]]
        if plan.get("supersede_notes"):
            args += ["--notes-file", plan["supersede_notes"]]
        s = runner(args, capture_output=True, text=True, errors="replace")
        step(f"{plan['supersede']} marked superseded", s.returncode == 0, (s.stdout + s.stderr).strip()[-200:])
    for wf in plan.get("ci") or []:                              # CI the release starts: where to read its verdict
        say(f"  CI started by the release: gh run list -R {plan['gh_repo']} --workflow {wf} --limit 1")
    return _end(steps, say, speak)


def _end(steps, say, speak=None):
    ok = all(o for _n, o in steps)
    say("RELEASE LIVE AND VERIFIED" if ok else f"RELEASE STOPPED at: {next(n for n, o in steps if not o)} (nothing after it ran)")
    try:                                                         # spoken, so nobody has to watch the window
        from .herald import say as _herald
        _speak = speak or _herald
        _speak("Release live and verified" if ok else "Release stopped. Look at the window.")
    except Exception:
        pass
    return 0 if ok else 3
