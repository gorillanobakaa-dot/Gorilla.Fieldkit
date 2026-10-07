"""The check after EVERY source change (owner, 2026-10-07: "do we have a deterministic tool ... that performs all the
checks after every source change"). `record` runs it on its own; it is also a command:

    fieldkit build-harness check-change <task>

Until then, export-hand and replay ran only when someone remembered, decisions on prefs could only be judged on an
installed build, and the release gate was the first place a missing patch or a broken lock would show. In order:
  1. the change is recorded: no uncommitted edit in the tree (an unrecorded edit is the one that gets lost)
  2. journal hash chain intact; no .rej/.orig; no pristine file missing without a patch that deletes it
  3. the UI rules and the technique locks (the build gate's fast rows, ~4 s)
  4. the decision register against the SOURCE tree (prefs, tree text, mozconfig); a VIOLATED decision fails, and
     what only an install can show (packaged files, prefs set under #if) is listed for post-install
  5. export-hand: the hand steps become public patches
  6. replay: pristine + public patch set = the tree, byte for byte (git tree hash)
  7. the public patch set is committed locally under the public placeholder identity (never pushed: that is the
     owner's call)
Fail closed: any row that fails makes the check FAIL. The 8-minute full step verify stays in the build gate.
"""
import subprocess
from pathlib import Path

from . import task


def _git(w, *a, env=None):
    return subprocess.run(["git", "-C", str(w), *a], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env)


def patchset_repo(owner_root):
    import json
    pol = json.loads((Path(owner_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    root = Path(owner_root) / pol["patchset_root"]
    top = _git(root, "rev-parse", "--show-toplevel").stdout.strip()
    return root, Path(top) if top else None


def commit_patches(owner_root, message, say=print):
    """-> (ok, evidence). Commits the patch set's `patches/` changes locally. The author must be the identity the
    published history already uses (the tip of the upstream branch), so the owner's own address never reaches the
    public repo (2026-10-07: the repo-local config carried it, and one commit picked it up before it was pushed)."""
    root, repo = patchset_repo(owner_root)
    if repo is None:
        return False, f"{root} is not inside a git repository"
    rel = root.relative_to(repo).as_posix()
    if not _git(repo, "status", "--porcelain", "--", rel).stdout.strip():
        return True, "nothing new to commit"
    pub = _git(repo, "log", "-1", "--format=%an%x00%ae", "@{upstream}").stdout.strip()
    if "\0" not in pub:
        return False, "the patch set has no upstream branch to take the public identity from"
    name, email = pub.split("\0")
    _git(repo, "add", "--", rel)
    r = _git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "-m", message)
    if r.returncode != 0:
        return False, f"commit refused: {(r.stdout + r.stderr).strip()[-200:]}"
    head = _git(repo, "log", "-1", "--format=%h %ae").stdout.strip()
    return True, f"committed {head} locally (not pushed)"


def run(task_id, say=print, note=None):
    """-> {"ok", "rows": [{check, ok, evidence}], "after_install": [...]}"""
    from . import buildrun, decisions, firefox, techniques, uicheck
    t = task.load(task_id)
    wd = Path(t["workdir"])
    owner = buildrun._owner_root(t)
    rows, after_install = [], []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})
        say(f"  [{'ok' if ok else 'FAIL'}] {name}: {evidence}")

    dirty = _git(wd, "status", "--porcelain").stdout.strip()
    row("every edit is recorded (no uncommitted change in the tree)", not dirty,
        "clean" if not dirty else f"unrecorded: {dirty.splitlines()[:3]} - record it: build-harness record {task_id} kind=... FILE")
    problems, count, _ = task.verify_journal(task_id)
    row("journal hash chain intact", not problems, f"{count} lines" if not problems else "; ".join(problems[:2]))
    stray = [p.name for p in firefox.leftovers(wd)]
    row("no .rej / .orig leftovers", not stray, "none" if not stray else f"{len(stray)}: {stray[:3]}")
    lost = firefox.unexplained_deletions(t)
    row("no pristine file missing without a patch that deletes it", not lost, "none" if not lost else f"{lost[:3]}")
    for name, ok, evidence in list(uicheck.gate_rows(wd)) + list(techniques.gate_rows(wd)):
        row(name, ok, evidence)
    if not owner:
        row("owner harness beside this task", False, "none: decisions and patch export cannot run")
        return {"ok": False, "rows": rows, "after_install": after_install}
    res = decisions.check(owner, workdir=str(wd), source_prefs=True)
    bad = [r for r in res["rows"] if r["verdict"] == "VIOLATED"]
    after_install = [r["id"] for r in res["rows"] if r["verdict"] == "UNCHECKABLE"]
    enforced = sum(r["verdict"] == "ENFORCED" for r in res["rows"])
    row("decision register holds on the source tree", not bad and not res["problems"],
        f"{enforced} enforced, {len(after_install)} need the install (post-install judges them)" if not bad and not res["problems"]
        else "; ".join([f"{r['id']}: {[e for e in r['evidence'] if e.startswith('FAIL')][:2]}" for r in bad[:3]] + res["problems"][:2]))
    if dirty:
        row("patch set reproduces the tree", False, "not judged: the tree has unrecorded edits")
        return {"ok": False, "rows": rows, "after_install": after_install}
    cap = buildrun.capture_patches(t, task_id, say=lambda m: None, note=note)
    row("export-hand + replay: pristine + public patch set = the tree", cap.get("replay") == "OK",
        "same git tree hash" if cap.get("replay") == "OK" else cap.get("why", "replay failed"))
    if "commit" in cap:
        row("public patch set committed locally under the public identity", *cap["commit"])
    ok = all(r["ok"] for r in rows)
    task.journal(t, "check-change", ok=ok, failed=[r["check"] for r in rows if not r["ok"]][:10],
                 after_install=after_install[:20])
    say(f"CHECK-CHANGE {'OK' if ok else 'FAIL'}: {sum(r['ok'] for r in rows)}/{len(rows)} rows"
        + (f"; after install: {', '.join(after_install)}" if after_install else ""))
    return {"ok": ok, "rows": rows, "after_install": after_install}
