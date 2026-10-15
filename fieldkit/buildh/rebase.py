# Version: 1.0.0 · updated 26-10-15-23-32
"""rebase-set: bring the patch set onto a new Firefox release, so pristine upstream + the set = the tree that was built.

Born 2026-10-15 (Linux 158). A port applies the set group by group; a hunk that no longer applies is re-made by
hand in the new tree (S3, the "port-" checkpoints). The set itself was never updated, so replay on pristine 158 gave
77 failing patches and 40 differing paths: the next machine (Windows) would have met every one of them again and
redone the same day of work. The owner: "when I start the 158 build on Windows, Claude should only download the
harness and the source, apply our patches and start the build."

    fieldkit build-harness rebase-set <task>          (dry: says what it would write)
    fieldkit build-harness rebase-set <task> write=1  (writes the group and the policy, then proves it with replay)

How, deterministic and repeatable:
  1. replay as the policy stands, without this release's rebase group
  2. every patch that fails is marked superseded for this release; replay again (a patch that depended on it can
     fail now); repeat until nothing fails
  3. the difference between that replay and the built tree (HEAD) becomes group 23.REBASE.<major>: one patch per
     file, a deleted file in DELETED_FILES.manifest.txt; binary files as git binary patches
  4. the policy gains the group (last) and, on each group, the superseded patch names under "exclude" with
     "exclude_reason_<major>". The old patches stay on disk: they are the history and what the intents point at
  5. replay again: it must be OK (same git tree hash), or nothing is reported as done
Run it again after any later change: it rewrites the group from scratch (stale numbered patches removed).
"""
import json
import re
import subprocess
from pathlib import Path

from . import firefox, replay as rp


def _git(w, *a):
    r = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(w), *a], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a[:3])}: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return r.stdout


def group_name(major):
    return f"23.REBASE.{major}"


def failed_patches(failures):
    """replay failure lines ('08.Look/x.patch: 1 out of 2 hunks FAILED') -> {group: {patch file name}}."""
    out = {}
    for line in failures:
        rel = line.split(": ", 1)[0]
        parts = rel.split("/")
        if len(parts) >= 2 and rel.endswith(".patch"):
            out.setdefault(parts[0], set()).add(parts[-1])
    return out


def slug(path):
    return re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")[:120]


#: (group, patch) -> the replay round it was superseded in: 1 = it does not apply to pristine <major> itself (the
#: release drifted under it); 2+ = it applied only on top of a patch superseded in an earlier round (a cascade)
ROUND = {}


def marked(owner_root, major):
    """-> {group: {patch}} the policy records as superseded for `major` (key superseded_<major>)."""
    raw = json.loads((Path(owner_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    return {g: set(s.get(f"superseded_{major}", [])) for g, s in raw["groups"].items() if s.get(f"superseded_{major}")}


def superseded(owner_root, workdir, major, say=print, rounds=12, replay=rp.replay):
    """Steps 1-2. -> (excluded {group: set}, last replay result)."""
    excl = {}
    ROUND.clear()
    back = marked(owner_root, major)          # what an earlier run superseded is judged again, from scratch
    for i in range(rounds):
        r = replay(owner_root, workdir, say=lambda m: None, extra_exclude=excl, include=back,
                   skip=(group_name(major), f"{group_name(major)}.linux", f"{group_name(major)}.windows"))
        new = failed_patches(r["failures"])
        added = sum(len(v - excl.get(g, set())) for g, v in new.items())
        say(f"  rebase: round {i + 1}: {len(r['failures'])} failing patch(es), {added} newly superseded")
        if not r["failures"]:
            return excl, r
        for g, v in new.items():
            for name in v - excl.get(g, set()):
                ROUND[(g, name)] = i + 1
            excl.setdefault(g, set()).update(v)
    raise RuntimeError(f"rebase: patches still failing after {rounds} rounds: {r['failures'][:5]}")


def restricted_paths(owner_root):
    """-> {path: platform} for every file a platform-limited part of the policy touches (a group with "platforms",
    a patch under "exclude_on"). The rebase patch of such a file is valid only where that part applies, so it goes
    in the platform's own rebase group; every other file's rebase patch is shared by all platforms."""
    raw = json.loads((Path(owner_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    pset = Path(owner_root) / raw["patchset_root"]
    out = {}
    for g, spec in raw["groups"].items():
        if g.startswith("23.REBASE."):
            continue
        plats = spec.get("platforms") or []
        files = sorted((pset / g).rglob("*.patch")) if (pset / g).is_dir() else []
        for pf in files:
            limited = list(plats) or [p for p, names in (spec.get("exclude_on") or {}).items() if pf.name in names]
            if not limited:
                continue
            # exclude_on names where it is LEFT OUT: the file then belongs to the other platform(s)
            where = plats[0] if plats else ("linux" if "windows" in limited else "windows")
            for fp in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace")):
                out.setdefault(fp["file"], where)
        if plats:
            out.update({rel: plats[0] for _, rel in firefox.new_files(pset, g)})
    return out


def write_group(owner_root, workdir, major, replayed, excl, say=print, dry=False, platform=None):
    """Steps 3-4. -> {"patches": n, "deleted": n, "superseded": n, "platform_patches": n}"""
    platform = platform or firefox.this_platform()
    pset, _ = firefox._policy(owner_root)
    w = Path(workdir)
    built = _git(w, "rev-parse", "HEAD^{tree}").decode().strip()
    status = [l.split("\t", 1) for l in _git(w, "diff-tree", "-r", "--no-renames", "--name-status", replayed, built)
              .decode("utf-8", "surrogateescape").splitlines() if "\t" in l]
    limited = restricted_paths(owner_root)
    shared_g, own_g = group_name(major), f"{group_name(major)}.{platform}"
    parts = {shared_g: ([], []), own_g: ([], [])}
    for i, (st, path) in enumerate(sorted(status, key=lambda x: x[1]), 1):
        g = own_g if path in limited else shared_g
        written, deleted = parts[g]
        if st.startswith("D"):
            deleted.append(path)
            continue
        diff = _git(w, "diff-tree", "-p", "--binary", "--no-renames", "--full-index", replayed, built, "--", path)
        head = (f"# Gorilla {major} rebase: {path} as built on Firefox {major}"
                + (f" ({platform} only: a {limited[path]}-only patch touches this file)" if g == own_g else "") + "\n"
                f"# The earlier patches of this file do not apply to pristine {major} (superseded in the policy with\n"
                f"# exclude_reason_{major}); this patch carries the file from the remaining set to the built tree.\n\n")
        written.append((f"{i:04d}-{slug(path)}.patch", head.encode() + diff))
    n_sup = sum(len(v) for v in excl.values())
    for g, (written, deleted) in parts.items():
        say(f"  rebase: {g}: {len(written)} patch(es), {len(deleted)} deleted file(s)")
    say(f"  rebase: {n_sup} superseded patch(es) in {len(excl)} group(s)")
    res = {"patches": len(parts[shared_g][0]), "platform_patches": len(parts[own_g][0]),
           "deleted": len(parts[shared_g][1]) + len(parts[own_g][1]), "superseded": n_sup}
    if dry:
        return res
    for gname, (written, deleted) in parts.items():
        g = pset / gname
        if not written and not deleted:
            if g.is_dir():
                for old in g.glob("[0-9][0-9][0-9][0-9]-*.patch"):
                    old.unlink()
            continue
        g.mkdir(parents=True, exist_ok=True)
        names = {n for n, _ in written}
        for old in g.glob("[0-9][0-9][0-9][0-9]-*.patch"):
            if old.name not in names:
                old.unlink()                 # this group is generated: a file it no longer writes is stale
        for n, data in written:
            (g / n).write_bytes(data)
        man = g / "DELETED_FILES.manifest.txt"
        if deleted:
            man.write_text("".join(p + "\n" for p in sorted(deleted)), encoding="utf-8", newline="\n")
        elif man.is_file():
            man.unlink()
        (g / "README.md").write_text(
            f"# {g.name}\n\nGenerated by `fieldkit build-harness rebase-set` on {platform}: pristine Firefox {major} + every "
            f"enabled group before this one (minus the patches superseded for {major}) + this group = the tree Gorilla "
            f"built for {major}. One patch per file. "
            + (f"Only on {platform}: these files are touched by a {platform}-only part of the set; the other platform "
               f"writes its own {group_name(major)}.<platform> when it runs rebase-set. " if gname == own_g else
               "Shared by every platform. ")
            + "Do not edit by hand: re-run rebase-set.\n", encoding="utf-8", newline="\n")
    pol_path = Path(owner_root) / "config" / "patch_policy.json"
    pol = json.loads(pol_path.read_text(encoding="utf-8"))
    groups = pol["groups"]
    for gname, spec in groups.items():              # the previous run's marks go; this run's are written below
        old = set(spec.pop(f"superseded_{major}", []))
        if old:
            spec["exclude"] = sorted(set(spec.get("exclude", [])) - old)
            if not spec["exclude"]:
                spec.pop("exclude")
            spec.pop(f"exclude_reason_{major}", None)
    for gname, names_ in excl.items():
        spec = groups[gname]
        spec["exclude"] = sorted(set(spec.get("exclude", [])) | names_)
        spec[f"superseded_{major}"] = sorted(names_)
        spec[f"exclude_reason_{major}"] = (f"Superseded for Firefox {major}: does not apply to pristine {major}; the file's "
                                           f"{major} form is in {group_name(major)} (rebase-set). Kept on disk as history.")
    for gname, (written, deleted) in parts.items():
        groups.pop(gname, None)
    groups[shared_g] = {"status": "enabled", "reason": (
        f"Generated by build-harness rebase-set: the step from the rest of the set to the tree built for Firefox "
        f"{major}, for every platform. Applied after every hand-made group. Proven by replay (same git tree hash).")}
    for other in [k for k in groups if k.startswith(group_name(major) + ".")]:
        groups[other] = groups.pop(other)       # another platform's rebase group stays after the shared one
    if parts[own_g][0] or parts[own_g][1]:
        groups[own_g] = {"status": "enabled", "platforms": [platform], "reason": (
            f"Generated by build-harness rebase-set on {platform}: the files a {platform}-only part of the set touches. "
            f"Applied last, on {platform} only.")}
    pol_path.write_text(json.dumps(pol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return res


def _blob(w, rev, path):
    r = subprocess.run(["git", "-C", str(w), "show", f"{rev}:{path}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def _meaningful(line):
    s = line.strip()
    return len(s) > 3 and s not in ("{", "}", "};", "});", ")", "*/", "/*", "<!--", "-->")


def carried(owner_root, workdir, excl):
    """Is the LOGIC of every superseded patch in the built tree? For each patch: the lines it adds must be in the
    built file and the lines it removes must be gone from it (meaningful lines, compared stripped).
    -> [{group, patch, round, cause, verdict, added, found, removed, gone, missing: [...]}]
       verdict: CARRIED (all of it is in the tree), ALREADY-UPSTREAM (pristine had it already), PARTLY, MISSING,
                FILE-GONE (the built tree has no such file: check it was removed on purpose)
    The owner, 2026-10-15: "if it does not apply directly, can the LOGIC and IDEA of the patch be transplanted to
    158?" This is the check that it was, patch by patch, instead of a belief."""
    pset, _ = firefox._policy(owner_root)
    w = Path(workdir)
    pristine = _git(w, "rev-list", "--max-parents=0", "HEAD").decode().split()[0]
    rows = []
    for g in sorted(excl):
        for name in sorted(excl[g]):
            pf = next((p for p in (pset / g).rglob(name)), None)
            if pf is None:
                continue
            add = rem = found = gone = upstream = 0
            missing = []
            file_gone = False
            for fp in firefox.parse_patch(pf.read_text(encoding="utf-8", errors="replace")):
                built = _blob(w, "HEAD", fp["file"])
                orig = _blob(w, pristine, fp["file"]) or ""
                if built is None:
                    file_gone = not fp.get("deleted")
                    continue
                have = {l.strip() for l in built.splitlines()}
                had = {l.strip() for l in orig.splitlines()}
                for h in fp["hunks"]:
                    plus = {l[1:].strip() for l in h["lines"] if l.startswith("+") and _meaningful(l[1:])}
                    minus = {l[1:].strip() for l in h["lines"] if l.startswith("-") and _meaningful(l[1:])} - plus
                    for s in plus:
                        add += 1
                        if s in have:
                            found += 1
                            upstream += s in had
                        else:
                            missing.append(f"{fp['file']}: + {s[:110]}")
                    for s in minus:
                        rem += 1
                        if s not in have:
                            gone += 1
                        else:
                            missing.append(f"{fp['file']}: - {s[:110]} (still there)")
            total, ok = add + rem, found + gone
            if file_gone and not total:
                verdict = "FILE-GONE"
            elif total and ok == total:
                verdict = "ALREADY-UPSTREAM" if add and upstream == add and not rem else "CARRIED"
            elif ok == 0:
                verdict = "FILE-GONE" if file_gone else "MISSING"
            else:
                verdict = "PARTLY"
            rnd = ROUND.get((g, name), 1)
            rows.append({"group": g, "patch": name, "round": rnd,
                         "cause": "drift: does not apply to pristine" if rnd == 1 else f"cascade: built on a patch superseded in round {rnd - 1}",
                         "verdict": verdict, "added": add, "found": found, "removed": rem, "gone": gone,
                         "missing": missing[:12]})
    return rows


def report(rows, major):
    """The SUPERSEDED.md written beside 23.REBASE.<major>: every superseded patch, why, and whether its logic is in."""
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    out = [f"# Patches superseded for Firefox {major}", "",
           f"Generated by `fieldkit build-harness rebase-set`. {len(rows)} patches no longer apply to pristine {major}; "
           f"their {major} form is in {group_name(major)}. Whether each one's logic is in the {major} tree "
           "(added lines present, removed lines gone, compared line by line):", "",
           ", ".join(f"{k} {v}" for k, v in sorted(c.items())), "",
           "CARRIED: all of it is in the tree. ALREADY-UPSTREAM: Mozilla made the same change. PARTLY / MISSING: read "
           "the lines below: either ported in a different form (fine, say so in the patch's intent) or LOST "
           "(port it, `build-harness record`, then rebase-set again). FILE-GONE: the file is not in the tree.", ""]
    for r in sorted(rows, key=lambda r: (r["verdict"] not in ("MISSING", "PARTLY", "FILE-GONE"), r["group"], r["patch"])):
        out.append(f"- **{r['verdict']}** `{r['group']}/{r['patch']}` ({r['cause']}; added {r['found']}/{r['added']} "
                   f"in, removed {r['gone']}/{r['removed']} gone)")
        out += [f"    - {m}" for m in r["missing"][:6]] if r["verdict"] in ("PARTLY", "MISSING") else []
    return "\n".join(out) + "\n"


def run(owner_root, workdir, major, write=False, say=print):
    """-> {"ok", "patches", "deleted", "superseded", "replay"}"""
    excl, r = superseded(owner_root, workdir, major, say=say)
    out = write_group(owner_root, workdir, major, r["replayed"], excl, say=say, dry=not write)
    rows = carried(owner_root, workdir, excl)
    from collections import Counter
    out["carried"] = dict(Counter(x["verdict"] for x in rows))
    say("  rebase: logic of the superseded patches in the built tree: "
        + ", ".join(f"{k} {v}" for k, v in sorted(out["carried"].items())))
    for x in rows:
        if x["verdict"] in ("MISSING", "PARTLY"):
            say(f"    {x['verdict']}: {x['group']}/{x['patch']} ({x['found']}/{x['added']} added lines in, "
                f"{x['gone']}/{x['removed']} removed lines gone)")
    if write:
        pset, _ = firefox._policy(owner_root)
        (pset / group_name(major) / "SUPERSEDED.md").write_text(report(rows, major), encoding="utf-8", newline="\n")
    if not write:
        return {"ok": True, **out, "replay": "not run (dry)"}
    final = rp.replay(owner_root, workdir, say=lambda m: None)
    ok = final["ok"]
    say(f"REBASE {'OK' if ok else 'FAILED'}: replay {'gives the built tree' if ok else 'differs'} "
        f"({final['patches']} patches, {len(final['failures'])} failure(s), {len(final['differ'])} differing path(s))")
    return {"ok": ok, **out, "replay": "OK" if ok else (final["failures"][:5] + final["differ"][:5])}
