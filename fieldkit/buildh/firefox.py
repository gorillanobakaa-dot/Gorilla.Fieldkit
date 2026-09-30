"""firefox - bring the Gorilla Firefox patch set forward to the latest stable Firefox.

Steps (script = done by this code, model = one small job for the model, owner = waits for you):

  resolve      script  latest stable version and its release tag (upstream.py; never main/Nightly)
  vault        script  the untouched source in the vault (vault.py)
  workcopy     script  a fresh working copy made from the vault
  apply-<group>        script  every patch of the group, in order, with GNU patch; each hunk that
                               does not apply becomes a port-* model step (or an owner step)
  port-<group>-<n>     model   put ONE failed hunk's change into the file where that code now lives
  export-<group>       script  the group's new patch = git diff of what the group changed
  final-checks script  no .rej/.orig left, no conflict markers, no whitespace damage

The compile, package and publish come from the existing Gorilla Firefox harness and run
as separate owner-timed steps (hours at the thermal cap).

Settings (fieldkit.local.json): firefox.root = the Gorilla.firefox folder (patch_policy.json
and the patch set live there). The working copy is <vault root>/../work/firefox/<version>
unless the task names another.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

from ..core import settings
from . import upstream, vault

MAX_WINDOW = 160                                   # lines of the target file one packet may show
TRIVIAL = re.compile(r"^[\s{}()\[\];,]*$")        # braces and punctuation prove nothing


# -- patch files ---------------------------------------------------------------------------

def parse_patch(text):
    """-> [{"file": path, "hunks": [{"header": "@@ ..", "lines": [...]}]}] for a unified diff."""
    files, cur, hunk = [], None, None
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0].strip()
            path = path[2:] if path.startswith(("a/", "b/")) else path
            cur = {"file": path, "hunks": []}
            files.append(cur)
            hunk = None
        elif line.startswith("--- ") and (hunk is None or not hunk["lines"] or cur is None):
            continue
        elif line.startswith("@@") and cur is not None:
            hunk = {"header": line, "lines": []}
            cur["hunks"].append(hunk)
        elif hunk is not None and line[:1] in (" ", "+", "-", "\\"):
            hunk["lines"].append(line)
    return files


def hunk_sides(h):
    removed = [l[1:] for l in h["lines"] if l.startswith("-")]
    added = [l[1:] for l in h["lines"] if l.startswith("+")]
    context = [l[1:] for l in h["lines"] if l.startswith(" ")]
    return removed, added, context


_FAILED = re.compile(r"^Hunk #(\d+) FAILED at")
_PATCHING = re.compile(r"^(?:patching|checking) file '?(.+?)'?$")
_MISSING = re.compile(r"can't find file to patch|No file to patch")


def failures_from_output(out):
    """GNU patch output -> {"failed": [(file, hunk_no)], "missing": bool}."""
    failed, cur = [], None
    for line in out.splitlines():
        m = _PATCHING.match(line.strip())
        if m:
            cur = m.group(1)
            continue
        m = _FAILED.match(line.strip())
        if m and cur:
            failed.append((cur, int(m.group(1))))
    return {"failed": failed, "missing": bool(_MISSING.search(out))}


def _patch_exe():
    return shutil.which("patch") or r"C:\Program Files\Git\usr\bin\patch.exe"


# -- the steps -------------------------------------------------------------------------------

def _policy(root):
    pol = json.loads((Path(root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    return Path(root) / pol["patchset_root"], pol["groups"]


def step_resolve(t, **kw):
    info = t["meta"].get("pinned") or upstream.latest_firefox()
    t["meta"]["upstream"] = info
    return {"ok": True, "summary": f"Firefox {info['version']} ({info['tag']}, {info['commit'][:12]})"}


def step_vault(t, vault_base=None, **kw):
    info = t["meta"]["upstream"]
    out = vault.fetch_firefox(info, base=vault_base)
    check = vault.verify("firefox", info["version"], base=vault_base)
    return {"ok": check["intact"], "why": check["problems"], "summary": f"{out['status']}: {out['vault']}"}


def step_workcopy(t, vault_base=None, **kw):
    w = Path(t["workdir"])
    if (w / ".git").exists():
        return {"ok": True, "summary": f"working copy already at {w}"}
    out = vault.restore("firefox", w, t["meta"]["upstream"]["version"], base=vault_base)
    return {"ok": True, "summary": f"fresh copy of {out['version']} from the vault"}


def plan_groups(t, harness_root, **kw):
    """Adds apply/export steps for every enabled group, in the policy's order."""
    pset, groups = _policy(harness_root)
    steps = []
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        args = {"harness_root": str(harness_root), "group": g}
        steps.append({"id": f"apply-{g}", "kind": "script", "title": f"apply group {g}",
                      "run": "fieldkit.buildh.firefox:step_apply_group", "args": args})
        steps.append({"id": f"export-{g}", "kind": "script", "title": f"export the {g} patch",
                      "run": "fieldkit.buildh.firefox:step_export_group", "args": args})
    steps.append({"id": "final-checks", "kind": "script", "title": "no rejects, no conflict markers",
                  "run": "fieldkit.buildh.firefox:step_final_checks"})
    return {"ok": True, "add_steps": steps, "summary": f"{len(steps) // 2} enabled groups"}


def step_apply_group(t, harness_root, group, **kw):
    pset, groups = _policy(harness_root)
    excluded = set(groups[group].get("exclude", []))
    w = Path(t["workdir"])
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    t["meta"].setdefault("group_base", {})[group] = base
    new_steps, applied, skipped, upstreamed = [], 0, [], []
    for pf in sorted((pset / group).rglob("*.patch")):
        if pf.name in excluded:
            skipped.append(pf.name)
            continue
        with open(pf, "rb") as fh:
            r = subprocess.run([_patch_exe(), "-p1", "--forward", "--no-backup-if-mismatch", "--fuzz=3",
                                "-d", str(w)], stdin=fh, capture_output=True, timeout=600)
        out = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        res = failures_from_output(out)
        rel = pf.relative_to(pset).as_posix()
        if "previously applied" in out:              # GNU patch: this Firefox already has the change
            upstreamed.append(f"{rel} (the whole patch is already in this Firefox)")
            continue
        if r.returncode == 0:
            applied += 1
        parsed = {f["file"]: f["hunks"] for f in parse_patch(pf.read_text(encoding="utf-8", errors="replace"))}
        for fname, n in res["failed"]:
            hunks = parsed.get(fname) or []
            if n - 1 >= len(hunks):
                continue
            tf = w / fname
            if tf.is_file() and already_upstream(tf.read_text(encoding="utf-8", errors="replace").splitlines(),
                                                 hunks[n - 1]):
                upstreamed.append(f"{rel} {fname} #{n}")
                continue
            new_steps.append({"id": f"port-{group}-{pf.stem}-{Path(fname).name}-h{n}", "kind": "model",
                              "title": f"port hunk #{n} of {rel} into {fname}",
                              "packet": "fieldkit.buildh.firefox:packet_port",
                              "check": "fieldkit.buildh.firefox:check_port", "allowed": [fname],
                              "args": {"patch": rel, "file": fname, "hunk": hunks[n - 1]}})
        if res["missing"]:
            new_steps.append({"id": f"owner-{group}-{pf.stem}", "kind": "owner",
                              "title": f"{rel} patches a file that no longer exists in this Firefox; "
                                       f"decide: drop the patch, or point it at the file's new home"})
    for rej in list(w.rglob("*.rej")) + list(w.rglob("*.orig")):
        rej.unlink()
    # the export step for this group comes after its port steps
    return {"ok": True, "add_steps": new_steps,
            "upstreamed": upstreamed,
            "summary": f"{group}: {applied} patch(es) applied clean, {len(new_steps)} hunk job(s), "
                       f"{len(upstreamed)} hunk(s) already upstream, {len(skipped)} excluded"}


def step_export_group(t, harness_root, group, out_dir=None, **kw):
    w = Path(t["workdir"])
    base = t["meta"]["group_base"][group]
    diff = subprocess.run(["git", "-C", str(w), "diff", "--binary", base, "HEAD"], capture_output=True).stdout
    out = Path(out_dir or (Path(t["workdir"]).parent / f"patchset-{t['meta']['upstream']['version']}")) / f"{group}.patch"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(diff)
    return {"ok": True, "summary": f"{out.name}: {len(diff):,} bytes"}


def step_final_checks(t, **kw):
    w = Path(t["workdir"])
    why = [f"leftover {p.relative_to(w)}" for p in list(w.rglob("*.rej"))[:5] + list(w.rglob("*.orig"))[:5]]
    first = t["checkpoints"][0]["commit"] if t["checkpoints"] else "HEAD"
    r = subprocess.run(["git", "-C", str(w), "diff", "--check", first, "HEAD"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    why += [l for l in r.stdout.splitlines() if "conflict marker" in l][:5]
    # regression: every hunk a model ported must still be in place at the end
    from . import task as taskmod
    for s in t["steps"]:
        if s["kind"] != "model" or s["status"] != "done" or s.get("skipped_by_owner"):
            continue
        a = s["args"]
        target = w / a["file"]
        now = target.read_text(encoding="utf-8", errors="replace").splitlines() if target.is_file() else []
        base_commit = next((c["commit"] for c in t["checkpoints"] if c["label"] == s["id"]), None)
        before = subprocess.run(["git", "-C", str(w), "show", f"{base_commit}~1:{a['file']}"], capture_output=True
                                ).stdout.decode("utf-8", "replace").splitlines() if base_commit else []
        lost = hunk_problems(before, now, a["hunk"]) if before else []
        if lost:
            why.append(f"regression: {s['id']} no longer holds: {lost[0]}")
            taskmod.journal(t, "regression", step=s["id"], why=lost[0])
    return {"ok": not why, "why": why, "summary": "clean" if not why else f"{len(why)} problem(s)"}


# -- the model's job: port one hunk ----------------------------------------------------------

def _anchor(lines, context_and_removed, min_run=3):
    """Where the hunk's old code lives now: the best run of matching lines, by content."""
    want = [l.strip() for l in context_and_removed if l.strip() and not TRIVIAL.match(l)]
    if not want:
        return None
    stripped = [l.strip() for l in lines]
    window = len(want) + 5
    where = {}
    for k in set(want):
        for i, l in enumerate(stripped):
            if l == k:
                where.setdefault(k, []).append(i)
    starts = sorted({i for hits in where.values() for i in hits})
    best, best_i = 0, None
    for s in starts:                                    # the place where most of the hunk's lines sit together
        score = sum(1 for k in set(want) if any(s <= i < s + window for i in where.get(k, [])))
        if score > best:
            best, best_i = score, s
    if best_i is not None and best >= min(min_run, len(set(want))):
        return best_i
    # upstream edited the lines themselves: take the file line most like one of them
    import difflib
    near_r, near_i = 0.75, None
    for w in want:
        for i, l in enumerate(stripped):
            if l and abs(len(l) - len(w)) <= max(len(w), 20):
                r = difflib.SequenceMatcher(None, w, l).ratio()
                if r > near_r:
                    near_r, near_i = r, i
    return near_i if near_i is not None else best_i


def packet_port(t, s, budget_chars, patch, file, hunk, **kw):
    removed, added, context = hunk_sides(hunk)
    target = Path(t["workdir"]) / file
    parts = [f"PATCH: {patch}", f"FILE:  {file}", "",
             "The change this hunk makes (lines starting '-' are removed, '+' are added, ' ' are context):",
             hunk["header"], *hunk["lines"], ""]
    if not target.is_file():
        parts.append(f"{file} does not exist in this Firefox. You cannot port this hunk: submit without "
                     "changing anything and the step goes to the owner.")
        return "\n".join(parts)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    at = _anchor(lines, [l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
    # The budget is a ceiling, not a target (2026-09-30: the first live packet filled it and
    # showed lines 1-1425, ~20k tokens, for a change near line 260). Show the hunk's own length
    # plus 30 lines either side, never more than MAX_WINDOW lines.
    fits = max(20, (budget_chars - len("\n".join(parts)) - 800) // 90)          # ~90 chars per numbered line
    room = min(fits, len(hunk["lines"]) + 60, MAX_WINDOW)
    if at is None:
        parts.append(f"The old code was not found in {file} (upstream rewrote it). The file has {len(lines)} "
                     f"lines. Use your find tool on a distinctive line of the hunk to locate the new code; "
                     f"the first {room} lines are shown only for orientation.")
        lo, hi = 0, min(len(lines), room)
    else:
        lo, hi = max(0, at - 30), min(len(lines), max(0, at - 30) + room)
        parts.append(f"In this Firefox the same code starts near line {at + 1}. Lines {lo + 1}-{hi}:")
    parts += [f"{i + 1:6}| {lines[i]}" for i in range(lo, hi)]
    parts += ["", "DO: edit " + file + " so that the '+' lines are present and the '-' lines are gone, in the "
                                     "matching place. Change nothing else. Keep the file's own style."]
    return "\n".join(parts)


def hunk_problems(before, after, hunk):
    """What is still wrong in `after` (lines) for the hunk's change, counted against `before`.

    For every meaningful line the hunk adds, the file must hold as many copies as before plus
    those added minus those removed; for every line it removes, the count must drop the same way.
    Braces and punctuation-only lines prove nothing and are ignored."""
    removed, added, _ = hunk_sides(hunk)
    b = [l.strip() for l in before]
    a = [l.strip() for l in after]
    why = []
    for k in dict.fromkeys(x.strip() for x in added + removed):
        if not k or TRIVIAL.match(k):
            continue
        net = sum(1 for x in added if x.strip() == k) - sum(1 for x in removed if x.strip() == k)
        want = max(0, b.count(k) + net)
        if net > 0 and a.count(k) < want:
            why.append(f"missing added line: {k[:100]}")
        elif net < 0 and a.count(k) > want:
            why.append(f"line should be gone: {k[:100]}")
    return why


def check_port(t, s, patch, file, hunk, **kw):
    target = Path(t["workdir"]) / file
    if not target.is_file():
        return {"ok": False, "why": [f"{file} does not exist; this hunk needs the owner"]}
    before = subprocess.run(["git", "-C", t["workdir"], "show", f"HEAD:{file}"], capture_output=True).stdout.decode(
        "utf-8", "replace").splitlines()
    after = target.read_text(encoding="utf-8", errors="replace").splitlines()
    why = hunk_problems(before, after, hunk)
    _, added, _ = hunk_sides(hunk)
    if len(after) > len(before) + 3 * max(1, len(added)) + 20:
        why.append(f"{len(after) - len(before)} lines added for a {len(added)}-line hunk: change only what the hunk changes")
    return {"ok": not why, "why": why[:8]}


def already_upstream(file_lines, hunk):
    """True when the file already has the hunk's result: the added lines are there and the removed ones gone."""
    removed, added, _ = hunk_sides(hunk)
    a = [l.strip() for l in file_lines]
    meaningful_added = [x.strip() for x in added if x.strip() and not TRIVIAL.match(x.strip())]
    meaningful_removed = [x.strip() for x in removed if x.strip() and not TRIVIAL.match(x.strip())
                          and x.strip() not in meaningful_added]
    return bool(meaningful_added or meaningful_removed) and all(k in a for k in meaningful_added) and         not any(k in a for k in meaningful_removed)


# -- building the plan -------------------------------------------------------------------------

def plan(harness_root, vault_base=None, pinned=None):
    """The steps as the owner approves them. Group steps are added once the source is in place."""
    common = {"vault_base": str(vault_base) if vault_base else None}
    return [
        {"id": "resolve", "kind": "script", "title": "find the latest stable Firefox (release tag, never main)",
         "run": "fieldkit.buildh.firefox:step_resolve"},
        {"id": "vault", "kind": "script", "title": "put the untouched source in the vault and verify it",
         "run": "fieldkit.buildh.firefox:step_vault", "args": common},
        {"id": "workcopy", "kind": "script", "title": "make a fresh working copy from the vault",
         "run": "fieldkit.buildh.firefox:step_workcopy", "args": common},
        {"id": "plan-groups", "kind": "script", "title": "one apply + export step per enabled patch group",
         "run": "fieldkit.buildh.firefox:plan_groups", "args": {"harness_root": str(harness_root)}},
    ]
