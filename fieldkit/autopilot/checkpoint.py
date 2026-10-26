# Version: 1.0.0 · updated 26-10-22-14-00
"""Task state of a job, kept apart from the model's session (DESIGN.md section 1).

  session     Claude Code's session id (from --output-format json): resumable with --resume while it is fresh
  checkpoint  <job>/CHECKPOINT.json + <job>/HANDOFF.md + a WIP commit: survives crashes, reboots, other machines

The WIP commit is made with git plumbing into refs/heads/wip/<job>: a temporary index, write-tree, commit-tree,
update-ref. The checkout's HEAD, index, branch and files are never touched (a shared checkout may hold other
people's uncommitted work; owner rule: never stash, reset or switch it), and a crash that left .git/index.lock behind
cannot stop it (the temporary index has its own lock).

Resume order: (1) the same session (--resume ID) when it ended less than FRESH_SECONDS ago and its last context is
under CONTEXT_LIMIT tokens (or unmeasured); (2) else a fresh session given HANDOFF.md. Never from scratch while a
checkpoint exists.
"""
import json
import os
import subprocess
import time
from pathlib import Path

FRESH_SECONDS = 55 * 60              # the prompt cache of a Claude Code session lives about an hour
CONTEXT_LIMIT = 400_000              # past this a resumed session costs more than a short handoff


def load(job_dir):
    p = Path(job_dir) / "CHECKPOINT.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "new", "attempts": [], "continuations": 0}


def save(job_dir, cp):
    d = Path(job_dir)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "CHECKPOINT.json.tmp"
    tmp.write_text(json.dumps(cp, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, d / "CHECKPOINT.json")


def _git(cwd, *a, env=None):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env)


def is_repo(cwd):
    return _git(cwd, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"


def head(cwd):
    r = _git(cwd, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def wip_ref(job_id):
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in job_id)
    return f"refs/heads/wip/{safe}"


def wip_commit(cwd, job_dir, job_id, why):
    """Snapshot the working tree (tracked + untracked, .gitignore respected) as a commit on wip/<job>.
    -> {ok, ref, sha, files, why}."""
    if not is_repo(cwd):
        return {"ok": False, "why": f"{cwd} is not a git repository: the checkpoint is HANDOFF.md alone"}
    env = dict(os.environ)
    idx = Path(job_dir) / "wip.index"
    idx.unlink(missing_ok=True)
    env["GIT_INDEX_FILE"] = str(idx)
    parent = head(cwd)
    if parent:
        _git(cwd, "read-tree", parent, env=env)
    add = _git(cwd, "add", "-A", env=env)
    if add.returncode != 0:
        return {"ok": False, "why": f"git add (temporary index) failed: {add.stderr.strip()[:300]}"}
    tree = _git(cwd, "write-tree", env=env).stdout.strip()
    ref = wip_ref(job_id)
    prev = _git(cwd, "rev-parse", "--verify", "-q", ref).stdout.strip()
    if prev and _git(cwd, "rev-parse", prev + "^{tree}").stdout.strip() == tree:
        idx.unlink(missing_ok=True)
        return {"ok": True, "ref": ref, "sha": prev, "unchanged": True, "why": "nothing new since the last snapshot"}
    args = ["commit-tree", tree, "-m", f"WIP({job_id}): checkpoint - {why}"[:200]]
    for p in [x for x in (parent, prev) if x]:
        args += ["-p", p]
    env_c = dict(env, GIT_AUTHOR_NAME=env.get("GIT_AUTHOR_NAME") or "fieldkit-autopilot",
                 GIT_AUTHOR_EMAIL=env.get("GIT_AUTHOR_EMAIL") or "autopilot@localhost",
                 GIT_COMMITTER_NAME=env.get("GIT_COMMITTER_NAME") or "fieldkit-autopilot",
                 GIT_COMMITTER_EMAIL=env.get("GIT_COMMITTER_EMAIL") or "autopilot@localhost")
    # the repository's own identity wins when it has one
    for k, key in (("GIT_AUTHOR_NAME", "user.name"), ("GIT_AUTHOR_EMAIL", "user.email")):
        v = _git(cwd, "config", key).stdout.strip()
        if v:
            env_c[k] = env_c[k.replace("AUTHOR", "COMMITTER")] = v
    c = _git(cwd, *args, env=env_c)
    sha = c.stdout.strip()
    if c.returncode != 0 or not sha:
        return {"ok": False, "why": f"git commit-tree failed: {c.stderr.strip()[:300]}"}
    _git(cwd, "update-ref", ref, sha)
    idx.unlink(missing_ok=True)
    files = _git(cwd, "diff", "--stat", parent or "4b825dc642cb6eb9a060e54bf8d69288fbee4904", sha).stdout.strip()
    return {"ok": True, "ref": ref, "sha": sha, "files": files.splitlines()[-1] if files else "no change"}


def handoff(job_dir, cwd, job_id, reason, result_text="", since=None, wip=None, start_head=None):
    """HANDOFF.md: the model's own (written during the attempt) is kept and the harness's facts appended; without
    one the harness writes it from what it measured."""
    p = Path(job_dir) / "HANDOFF.md"
    own = p.is_file() and since is not None and p.stat().st_mtime >= since
    facts = [f"## Harness checkpoint {time.strftime('%Y-%m-%d %H:%M:%S')}", "", f"- job: {job_id}",
             f"- why it stopped: {reason}"]
    if wip and wip.get("ok"):
        facts.append(f"- work in progress snapshotted: {wip['ref']} {wip['sha'][:12]} ({wip.get('files', '')})")
    elif wip:
        facts.append(f"- no WIP commit: {wip.get('why')}")
    if is_repo(cwd):
        st = _git(cwd, "status", "--short").stdout.strip().splitlines()
        facts.append(f"- working tree of {cwd}: {len(st)} changed path(s)")
        facts += [f"    {x}" for x in st[:40]]
        if start_head and head(cwd) and start_head != head(cwd):
            log = _git(cwd, "log", "--oneline", f"{start_head}..HEAD").stdout.strip().splitlines()
            facts.append(f"- commits made in this job: {len(log)}")
            facts += [f"    {x}" for x in log[:20]]
        if os.path.exists(os.path.join(_git(cwd, "rev-parse", "--absolute-git-dir").stdout.strip() or ".", "index.lock")):
            facts.append("- .git/index.lock exists (a crash mid-commit?): remove it only once no git process runs")
    if result_text:
        facts += ["- the run's last words:", "", "```", result_text.strip()[-1500:], "```"]
    body = "\n".join(facts) + "\n"
    if own:
        text = p.read_text(encoding="utf-8", errors="replace").rstrip() + "\n\n" + body
    else:
        text = (f"# HANDOFF: {job_id}\n\nWritten by the harness: the session did not leave its own handoff.\n"
                "Next: carry on with the brief from where the working tree and the commits stand; do not start over.\n\n"
                + body)
    p.write_text(text, encoding="utf-8")
    return p


def session_context(session_id, cwd=None):
    """Tokens of the session's last call (its context size) from its transcript, through session-cost (fieldkit/tokens);
    None when unmeasured."""
    if not session_id:
        return None
    from . import budget
    proj = Path.home() / ".claude" / "projects"
    for t in proj.glob(f"*/{session_id}.jsonl") if proj.is_dir() else []:
        s = budget.transcript_summary(t)
        return s["context_last"] if s else None
    return None


def resume_mode(cp, now=None, context=None):
    """-> ("session", id) | ("checkpoint", None) | ("start", None)."""
    now = now or time.time()
    if not cp.get("attempts"):
        return "start", None
    sid, ended = cp.get("session_id"), cp.get("ended_at") or 0
    if sid and now - ended < FRESH_SECONDS and (context is None or context < CONTEXT_LIMIT):
        return "session", sid
    return "checkpoint", None
