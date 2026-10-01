"""The verifier: reads the TREE and the PATCH SET, never the journal, and says what is really in place.

Built from the independent audit of 2026-10-01, which found 208 'done' steps in the overnight working copy that
the tree did not back, 44 files silently replaced by the owner's old tree, 4 edits no patch asked for, 124 new
files never copied and 560 upstream files deleted. None of that showed in the journal. So:

  score_hunk      each hunk of each in-scope patch -> APPLIED / PARTIAL / NOT-APPLIED / TARGET-GONE / NO-SIGNAL
  verify          the whole tree: hunk scores per group, false completions (task says done, tree says no),
                  copies of the old tree, stray edits, missing new files, unexplained deletions
  reopen          puts every false completion back to 'pending' (journal 'reopened'), so a lying record cannot
                  carry a step past the build gate

It runs before every job (sync) and inside the build gate. It changes nothing unless `reopen` is asked for, and
even then it only changes the task record, never a file in the tree.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import firefox, task

SHORT = 12          # a line shorter than this proves nothing on its own
SPECIFIC = 25       # a removed line this long, still present, proves the hunk is not in


def _judgeable(lines, floor):
    return [l.strip() for l in lines if len(l.strip()) >= floor and not firefox.TRIVIAL.match(l.strip())]


def _sequence_present(body, lines, gap=3):
    """Do `lines` (stripped) occur in `body` in order, each within `gap` lines of the previous?"""
    stripped = [l.strip() for l in body]
    starts = [i for i, l in enumerate(stripped) if l == lines[0]]
    for s in starts:
        pos, ok = s, True
        for want in lines[1:]:
            nxt = next((i for i in range(pos + 1, min(len(stripped), pos + 1 + gap)) if stripped[i] == want), None)
            if nxt is None:
                ok = False
                break
            pos = nxt
        if ok:
            return True
    return False


def score_hunk(body, hunk, file=""):
    """-> (verdict, detail). `body` is the target file's lines, or None when the file does not exist."""
    if file.endswith(".ftl") and body is not None:
        from . import fluent
        try:
            sem = fluent.semantics(hunk)
            if sem["reformat_only"]:
                return "APPLIED", "whitespace-only Fluent hunk: nothing to port"
            have = {e["id"]: e["parts"] for e in fluent.entries(body)}
            want = {**sem["changed"], **sem["added"]}
            ok = [i for i, p in want.items() if have.get(i) == p]
            gone = [i for i in list(want) + sem["removed"] if i not in have and i not in sem["added"]]
            ents = fluent.entries(body)
            still = [i for i in sem["removed"] if fluent.copy_to_remove(ents, i, hunk) is not None]
            if want and len(ok) == len(want) and not still:
                return "APPLIED", f"{len(ok)} message(s) read as the patch wants"
            if not want and sem["removed"] and not still:
                return "APPLIED", f"{len(sem['removed'])} message(s) removed where the patch removes them"
            if gone and not ok:
                return "TARGET-GONE", f"message(s) no longer exist: {gone[:3]}"
            if not ok:
                return "NOT-APPLIED", f"none of the {len(want)} message(s) read as the patch wants"
            return "PARTIAL", f"{len(ok)} of {len(want)} message(s) read as the patch wants"
        except fluent.Ambiguous:
            pass
    removed, added, _ = firefox.hunk_sides(hunk)
    add = _judgeable(added, SHORT)
    # a line the hunk removes AND adds back (re-indented, moved into an #ifdef) is not a removal to check:
    # live run 10 (h39) re-added `pref("media.contextmenu.video-overlay-detection", true);` indented, the
    # verifier called the harness's correct merge PARTIAL, reopened it, and the model was sent a done job
    rem = [l for l in _judgeable(removed, SPECIFIC) if l not in set(add)]
    if body is None:
        return "TARGET-GONE", "the file does not exist"
    have = {l.strip() for l in body}
    if not add and not rem:
        return "NO-SIGNAL", "only short, blank or punctuation lines: cannot be judged by text"
    add_in = [l for l in add if l in have]
    # a removal is judged inside the hunk's own span when it can be pinned: `color: inherit;` living elsewhere in
    # the file made two correct CSS merges 'not in the tree' and reopened them (live run 14, h15/h16)
    frame = firefox._span(body, {"lines": [l for l in hunk["lines"] if not l.startswith("-")]})   # context only
    if frame:
        lo, hi = frame
        if hi - lo <= 1:                                        # one anchor only: look as far as the hunk reaches
            hi = min(len(body), lo + len(hunk["lines"]) + 5)    # (may say NOT-APPLIED wrongly, never APPLIED wrongly)
        scope = body[lo:hi]
    else:
        scope = body
    # the removed block counts as still present only if its specific lines are still there TOGETHER, in order,
    # not scattered copies in other blocks (moz.build h2, tokens-brand.css h5 on 2026-10-01: `script=`,
    # `entry_point=` live in every GeneratedFile block; a frame pinned on the wrong copy must not matter either)
    rem_in = rem if rem and _sequence_present(scope, rem) else []
    if rem and not rem_in:
        pairs = firefox.renamed_near(body, hunk, rem, added)
        if pairs:                                       # live run 16: renamed by upstream, not gone
            return "NOT-APPLIED", f"{len(pairs)} removed line(s) still present under new names: " + \
                "; ".join(f"`{a[:40]}` is now `{b[:40]}`" for a, b in pairs[:2])
    if rem and not rem_in and not add:
        return "APPLIED", f"the {len(rem)} removed line(s) no longer stand together" + (" in the hunk's frame" if frame else " anywhere")
    if add and len(add_in) == len(add) and not rem_in:
        return "APPLIED", f"{len(add)} added line(s) present, {len(rem)} removed line(s) gone"
    if not add and rem and not rem_in:
        return "APPLIED", f"all {len(rem)} removed line(s) gone"
    if add and not add_in and rem and not rem_in:
        return "TARGET-GONE", "neither the old nor the new lines exist here: upstream removed or replaced this"
    if add and not add_in:
        return "NOT-APPLIED", f"none of the {len(add)} added line(s) present"
    if not add and rem_in:
        return "NOT-APPLIED", f"{len(rem_in)} of {len(rem)} removed line(s) still present"
    return "PARTIAL", f"{len(add_in)} of {len(add)} added line(s) present; {len(rem_in)} removed line(s) still present"


def _git(wd, *args):
    return subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True, errors="replace").stdout


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def hunks_in_scope(harness_root):
    """-> [(group, patch rel path, file, hunk index 1-based, hunk)] for enabled groups, minus excluded patches."""
    pset, groups = firefox._policy(harness_root)
    out = []
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        excluded = set(spec.get("exclude", []))
        for pf in sorted((pset / g).rglob("*.patch")):
            if pf.name in excluded:
                continue
            rel = pf.relative_to(pset).as_posix()
            for f in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace")):
                for n, h in enumerate(f["hunks"], 1):
                    out.append((g, rel, f["file"], n, h))
    return out


def older_pristine(version):
    """The vault's newest Firefox copy older than `version`, or None."""
    from . import vault
    try:
        rows = [r for r in vault.listing() if r["product"] == "firefox" and r["version"] != version]
    except Exception:  # noqa: BLE001 - no vault configured: the caller reports UNDETERMINED
        return None

    def key(v):
        return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.]", v))
    older = [r for r in rows if version and key(r["version"]) < key(version)]
    return Path(max(older, key=lambda r: key(r["version"]))["path"]) if older else None


def add_missing_new_file_steps(task_id):
    """A task planned before the new-files step existed gets one per group that carries NEW_FILES. -> ids added."""
    t = task.load(task_id)
    hr = t["meta"].get("harness_root")
    if not hr:
        return []
    pset, groups = firefox._policy(hr)
    have = {s["id"] for s in t["steps"]}
    added = []
    for g, spec in groups.items():
        sid = f"new-files-{g}"
        if spec.get("status") != "enabled" or sid in have or not (pset / g / "NEW_FILES").is_dir():
            continue
        at = next((i for i, s in enumerate(t["steps"]) if s["id"] == f"export-{g}"), len(t["steps"]))
        t["steps"].insert(at, {"id": sid, "kind": "script", "title": f"copy the new files of {g}", "status": "pending",
                               "attempts": 0, "max_attempts": 3, "run": "fieldkit.buildh.firefox:step_new_files",
                               "args": {"harness_root": str(hr), "group": g}})
        added.append(sid)
    if added:
        task.save(t)
        task.journal(t, "plan-amended", steps=added, why="new-files steps were missing from the plan")
    return added


def relocate_missing(task_id):
    """Owner 'file no longer exists' steps whose file has a measurable new home become port/append steps
    (relocate.heal). -> [(owner id, reason, new ids)]."""
    from . import relocate
    t = task.load(task_id)
    hr = t["meta"].get("harness_root")
    if not hr:
        return []
    out = relocate.heal(t, hr)
    if out:
        task.save(t)
        for sid, why, new in out:
            task.journal(t, "relocated", step=sid, why=[why], steps=new)
    return out


def _truth_root(hr, workdir=None):
    """The owner's built tree a snapshot harness was taken from (patch_policy._snapshot.live_tree, else the snapshot
    harness's own src link), or None. Never the task's own tree: the owner's `src` became a junction to the ported
    157 tree after the snapshot, and the truth path followed it, so the Fluent reconcile compared the tree with
    itself and saw nothing (live run 16, second pass)."""
    import json
    cands = []
    try:
        snap = json.loads((Path(hr) / "config" / "patch_policy.json").read_text(encoding="utf-8")).get("_snapshot") or {}
        if snap.get("live_tree"):
            cands.append(Path(snap["live_tree"]))
    except (OSError, ValueError):
        pass
    cands.append(Path(hr) / "src")
    own = Path(workdir).resolve() if workdir else None
    for c in cands:
        try:
            if c.is_dir() and (own is None or c.resolve() != own) and (c / "browser").is_dir():
                return c
        except OSError:
            continue
    return None


def add_dedupe_steps(task_id, rep):
    """A Fluent file with a message doubled beyond the owner's truth gets a dedupe step (fluent.step_dedupe), placed
    before the final checks. -> ids added."""
    t = task.load(task_id)
    truth = _truth_root(t["meta"].get("harness_root") or "", t["workdir"])
    if not truth or not rep.get("ftl_duplicates"):
        return []
    have = {s["id"] for s in t["steps"]}
    added = []
    for row in rep["ftl_duplicates"]:
        rel = row.split(":")[0]
        sid = "dedupe-" + re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")[:60]
        if sid in have:
            continue
        at = next((i for i, s in enumerate(t["steps"]) if s["id"].startswith("final")), len(t["steps"]))
        t["steps"].insert(at, {"id": sid, "kind": "script", "title": f"{rel}: messages defined more or fewer times than in the "
                                                                      f"owner's tree; reconcile against it", "status": "pending",
                               "attempts": 0, "max_attempts": 3, "run": "fieldkit.buildh.fluent:step_dedupe",
                               "check": "fieldkit.buildh.fluent:check_dedupe", "allowed": [rel],
                               "args": {"file": rel, "truth_root": str(truth)}})
        added.append(sid)
    if added:
        task.save(t)
        task.journal(t, "plan-amended", steps=added, why="Fluent messages defined more often than in the owner's tree")
    return added


def verify(task_id):
    """-> report dict. Read-only."""
    t = task.load(task_id)
    hr = t["meta"].get("harness_root")
    w = Path(t["workdir"])
    rep = {"task": task_id, "groups": {}, "false_completions": [], "old_tree_copies": [], "stray_edits": [],
           "missing_new_files": [], "unexplained_deletions": [], "target_gone": [], "problems": []}
    if not hr or not (Path(hr) / "config" / "patch_policy.json").is_file():
        rep["problems"].append("no harness_root / patch policy: nothing can be verified")
        return rep
    cache = {}

    def body(file):
        if file not in cache:
            p = w / file
            cache[file] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else None
        return cache[file]

    # 1. every hunk, scored from the tree
    scores = {}
    for g, rel, file, n, h in hunks_in_scope(hr):
        v, d = score_hunk(body(file), h, file)
        scores[(rel, file, n)] = (v, d)
        rep["groups"].setdefault(g, {"APPLIED": 0, "PARTIAL": 0, "NOT-APPLIED": 0, "TARGET-GONE": 0, "NO-SIGNAL": 0})[v] += 1
        if v == "TARGET-GONE":
            rep["target_gone"].append(f"{rel} {file} #{n}")
    # 2. the task record against the tree
    by_key = {}
    for s in t["steps"]:
        a = s.get("args") or {}
        if s["kind"] == "model" and "hunk" in a:
            m = re.search(r"-h(\d+)$", s["id"])
            by_key[(a["patch"], a["file"], int(m.group(1)) if m else -1)] = s
    for key, s in by_key.items():
        if s["status"] not in ("done", "obsolete") or s.get("skipped_by_owner") or s.get("dropped_by_owner"):
            continue
        v, d = scores.get(key, (None, None))
        if s.get("done_by") == "hand" or s.get("hand_port"):
            a = s.get("args") or {}
            b = body(a["file"]) if a.get("file") else None
            why = firefox.hand_port_holds(b, a["hunk"]) if b is not None else ["the file does not exist"]
            v, d = ("APPLIED", "hand port holds") if not why else ("NOT-APPLIED", "hand port: " + "; ".join(why)[:160])
        if v is None:                                   # a relocated step: its file is not the patch's (relocate.py)
            v, d = score_hunk(body(a["file"]), a["hunk"], a["file"]) if (a := s.get("args") or {}).get("hunk") else (None, None)
        # an OBSOLETE step whose lines the tree still holds (renamed or not) was closed wrongly: reopened like a
        # false completion (live run 16: Tabbrowser.sys.mjs h4, closed as obsolete with the code still there)
        if v in ("NOT-APPLIED", "PARTIAL"):
            rep["false_completions"].append({"step": s["id"], "verdict": v, "detail": d, "done_by": s.get("done_by", "model")})
    # 3. files that are byte-identical to the owner's OLD tree. Harmless when upstream did not touch the file
    #    between the two versions (then patching gives the same bytes); lossy when it did. The older pristine
    #    copy in the vault decides; without one the verdict is UNDETERMINED, never 'fine'.
    root = _git(w, "rev-list", "--max-parents=0", "HEAD").split()
    key_root = Path(hr) / "src"
    changed = [l for l in _git(w, "diff", "--name-only", root[0], "HEAD").splitlines() if l] if root else []
    old_pristine = older_pristine(t["meta"].get("upstream", {}).get("version"))
    rep["old_tree_copies_harmless"], rep["old_tree_copies_undetermined"] = [], []
    pset0, groups0 = firefox._policy(hr)
    shipped = set()                                               # files the patch set itself ships whole: expected identical
    for g, spec in groups0.items():
        if spec.get("status") != "enabled":
            continue
        shipped.update(rel for _, rel in firefox.new_files(pset0, g))
        rf = pset0 / g / "REPLACE_FILES"
        if rf.is_dir():
            shipped.update(p.relative_to(rf).as_posix() for p in rf.rglob("*") if p.is_file())
    for rel in changed:
        if rel in shipped:
            continue
        p, k = w / rel, key_root / rel
        if not (p.is_file() and k.is_file() and p.stat().st_size == k.stat().st_size and _sha(p) == _sha(k)):
            continue
        if old_pristine is None or not (old_pristine / rel).is_file():
            rep["old_tree_copies_undetermined"].append(rel)
            continue
        new_pristine = subprocess.run(["git", "-C", str(w), "show", f"{root[0]}:{rel}"], capture_output=True).stdout
        if new_pristine == (old_pristine / rel).read_bytes():
            rep["old_tree_copies_harmless"].append(rel)       # upstream did not change it: same result either way
        else:
            rep["old_tree_copies"].append(rel)
    # 4. stray edits: changed files that no in-scope patch or NEW_FILES names
    named = {file for _, _, file, _, _ in hunks_in_scope(hr)}
    pset, groups = firefox._policy(hr)
    for g, spec in groups.items():
        if spec.get("status") == "enabled":
            named.update(rel for _, rel in firefox.new_files(pset, g))
    named |= firefox.manifest_deletions(hr)                       # a file the fork deletes on purpose is not a stray edit
    for s in t["steps"]:                                          # a relocated target (relocate.py) is named by its step
        a = s.get("args") or {}
        named.update([a["file"]] if a.get("file") else [])
        named.update(s.get("allowed") or [])
    for g, spec in groups.items():
        rf = pset / g / "REPLACE_FILES"
        if spec.get("status") == "enabled" and rf.is_dir():
            named.update(p.relative_to(rf).as_posix() for p in rf.rglob("*") if p.is_file())
    rep["stray_edits"] = [rel for rel in changed if rel not in named]
    # 5. new files not in the tree
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        for src, rel in firefox.new_files(pset, g):
            d = w / rel
            if not d.is_file() or d.read_bytes() != src.read_bytes():
                rep["missing_new_files"].append(f"{g}/NEW_FILES/{rel}")
    # 6. upstream files gone without a patch deleting them
    rep["unexplained_deletions"] = firefox.unexplained_deletions(t)
    # 7. a Fluent file with an id twice where the pristine file had it once (a moved message whose old copy was not
    #    removed, or a port that added a copy): Firefox's parser keeps the last and the build's l10n lint fails
    #    Measured against the owner's TRUTH when the harness is a snapshot (live run 16: nine of ten doubled ids are
    #    doubled in the owner's running 155 tree as well, so they are the port, not damage), else the pristine tree.
    rep["ftl_duplicates"] = []
    if root:
        import collections
        from . import fluent
        truth = _truth_root(hr, w)
        for rel in changed:
            if not rel.endswith(".ftl"):
                continue
            now = body(rel)
            if now is None:
                continue
            ref = (truth / rel).read_text(encoding="utf-8", errors="replace").splitlines() if truth and (truth / rel).is_file()                 else _git(w, "show", f"{root[0]}:{rel}").splitlines()
            was = collections.Counter(e["id"] for e in fluent.entries(ref))
            cnt = collections.Counter(e["id"] for e in fluent.entries(now))
            dup = [i for i, n in cnt.items() if n > 1 and n > was[i]]
            lost = []
            if truth and (truth / rel).is_file():
                up = collections.Counter(e["id"] for e in fluent.entries(_git(w, "show", f"{root[0]}:{rel}").splitlines()))
                lost = [i for i, n in was.items() if cnt[i] < n and up[i]]
            if dup or lost:
                rep["ftl_duplicates"].append(f"{rel}: " + (f"doubled {dup[:3]}" if dup else "") + (" " if dup and lost else "")
                                             + (f"lost {lost[:3]}" if lost else ""))
    return rep


def reopen(task_id, rep=None):
    """Every false completion goes back to pending. -> the step ids reopened."""
    rep = rep or verify(task_id)
    t = task.load(task_id)
    ids = {f["step"] for f in rep["false_completions"]}
    done = []
    for s in t["steps"]:
        if s["id"] in ids:
            s["status"], s["attempts"], s["auto_tried"], s["last_why"] = "pending", 0, False, None
            s.pop("done_by", None)
            done.append(s["id"])
    if done:
        task.save(t)
        task.journal(t, "reopened", steps=done, why="the tree does not contain what the record said was done")
    return done


def problems(rep):
    """-> list of (name, ok, evidence) rows for the build gate and preflight."""
    fc, oc, se, mn, ud = (rep["false_completions"], rep["old_tree_copies"], rep["stray_edits"],
                          rep["missing_new_files"], rep["unexplained_deletions"])
    return [("tree: every step the record calls done is really in the tree", not fc,
             "all backed" if not fc else f"{len(fc)} false completion(s), e.g. {fc[0]['step']}: {fc[0]['detail']}"),
            ("tree: no file is a lossy copy of the owner's old tree", not oc and not rep.get("old_tree_copies_undetermined"),
             (f"none lossy; {len(rep.get('old_tree_copies_harmless', []))} identical because upstream did not touch them"
              if not oc and not rep.get("old_tree_copies_undetermined") else
              f"{len(oc)} lossy: {oc[:3]}" if oc else
              f"{len(rep['old_tree_copies_undetermined'])} UNDETERMINED (no older pristine copy in the vault): "
              f"{rep['old_tree_copies_undetermined'][:3]}")),
            ("tree: no edit that no patch asked for", not se, "none" if not se else f"{len(se)}: {se[:3]}"),
            ("tree: every new file of the patch set is in place", not mn, "all present" if not mn else f"{len(mn)} missing, e.g. {mn[0]}"),
            ("tree: no upstream file gone without a patch", not ud, "none" if not ud else f"{len(ud)}, e.g. {ud[0]}"),
            ("tree: Fluent messages as often as in the owner's tree", not rep.get("ftl_duplicates"),
             "none" if not rep.get("ftl_duplicates") else f"{len(rep['ftl_duplicates'])} file(s), e.g. {rep['ftl_duplicates'][0]}")] + \
           [("verifier could run", not rep["problems"], "; ".join(rep["problems"]) or "ok")]


def lines(rep):
    out = [f"VERIFY {rep['task']} (read from the tree, not the journal)"]
    out += ["  group                          APPLIED PARTIAL NOT-APPLIED TARGET-GONE NO-SIGNAL"]
    for g, c in sorted(rep["groups"].items()):
        out.append(f"  {g:30} {c['APPLIED']:7} {c['PARTIAL']:7} {c['NOT-APPLIED']:11} {c['TARGET-GONE']:11} {c['NO-SIGNAL']:9}")
    for name, ok, ev in problems(rep):
        out.append(f"  {'PASS' if ok else 'FAIL'}  {name}: {ev}")
    if rep["false_completions"]:
        out.append("  false completions (the record says done, the tree says no):")
        out += [f"    {f['step']}  [{f['verdict']}] {f['detail']}" for f in rep["false_completions"][:20]]
    bad = sum(1 for _, ok, _ in problems(rep) if not ok)
    out.append(f"VERIFY: {'CLEAN' if not bad else f'{bad} problem(s)'}")
    return out
