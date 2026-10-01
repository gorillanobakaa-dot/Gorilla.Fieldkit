"""Snapshot: the TRUTH of a Gorilla build, taken from the tree that was actually built and runs, not from the
curated patch files.

Measured 2026-10-01 on the owner's Gorilla.firefox/src (Firefox 155.0.1 + Gorilla, the tree behind the installed
"Gorilla Unleashed 155.0.1"): 820 modified files, 433 deleted, 772 new. The curated patch set (cut against a 154
nightly) names 404 files; 439 modified files and 689 new files have no patch at all, and 189 + 142 of its own
hunks do not match the built tree. The owner's own group 16 was made the same way this module works: "diff the
live build tree against the vanilla snapshot". Here that is a deterministic tool with a proof.

  capture   live tree vs the pristine vault copy of the SAME version -> a complete patch set in the owner's layout:
              <group>/<path_with_underscores>.patch   one unified diff per modified text file
              <group>/NEW_FILES/<rel>                  every new file, byte-exact
              <group>/REPLACE_FILES/<rel>              binary files that were modified
              <snapshot group>/DELETED_FILES.manifest.txt
              MANIFEST.json                            sha256 of every emitted file, counts, exclusions, source commit
            Files a curated group already names stay in that group; everything else goes to the snapshot group.
            Plus a derived harness root (config + a junction to src) the normal workflow can run from.
  prove     apply the captured set to a fresh pristine copy and compare with the live tree: must be identical
            (minus the files excluded as backups/junk, which are listed). No proof, no port.
  compare   the curated patch set against the live tree: which of its hunks are really in the build (alive) and
            which are not (dead: never landed, dropped, or drifted), for the owner.
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from . import firefox, task, vault, verify

JUNK = re.compile(r"(\.gorilla\d+|\.unofficial|\.presharp|\.orig|\.rej|\.bak|~)$|(^|/)(\.preflight_state\.json|package-lock\.json)$")
PROFILE_FILES = {"user.js"}          # root-level profile file the owner keeps in 10.OVERRIDES/NEW_FILES
SNAP = "20.SNAPSHOT.DELTA"


def _git(repo, *args, binary=False):
    r = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args], capture_output=True)
    if r.returncode not in (0, 1):
        raise task.Refused(f"git {' '.join(args[:3])} failed in {repo}: {r.stderr.decode('utf-8', 'replace')[:200]}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def stem(rel):
    return rel.replace("/", "_")


def live_tree(harness_root):
    return Path(harness_root) / "src"


def check_base(harness_root, version, vault_base=None):
    """The live tree's HEAD must be the pristine commit of `version` in the vault. -> (src, pristine path)."""
    src = live_tree(harness_root)
    if not (src / ".git").exists():
        raise task.Refused(f"{src} is not a git tree; the snapshot needs a tree whose HEAD is pristine Firefox")
    rows = [r for r in vault.listing(vault_base) if r["product"] == "firefox" and r["version"] == version]
    if not rows:
        raise task.Refused(f"no pristine {version} in the vault; run: fieldkit build-harness vault fetch firefox --pin {version}")
    head = _git(src, "rev-parse", "HEAD").strip()
    want = _git(rows[0]["path"], "rev-parse", "HEAD").strip()
    if head != want:
        raise task.Refused(f"the live tree is at {head[:12]} but pristine {version} is {want[:12]}: not the same base, "
                           "a diff would mix upstream changes with the owner's")
    return src, Path(rows[0]["path"])


def curated_map(harness_root):
    """file -> group, from the curated set's patches and NEW_FILES (enabled groups only)."""
    pset, groups = firefox._policy(harness_root)
    out = {}
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            continue
        ex = set(spec.get("exclude", []))
        for pf in sorted((pset / g).rglob("*.patch")):
            if pf.name in ex:
                continue
            for m in re.finditer(r"^\+\+\+ (?:b/)?(\S+)", pf.read_text(encoding="utf-8", errors="replace"), re.M):
                out.setdefault(m.group(1), g)
        for _, rel in firefox.new_files(pset, g):
            out.setdefault(rel, g)
            if "/" in rel:                                   # a new file next to a group's new files belongs with them
                out.setdefault(rel.rsplit("/", 1)[0] + "/", g)
    return out, groups


def group_for(group_of, rel, default):
    if rel in group_of:
        return group_of[rel]
    parts = rel.split("/")
    for i in range(len(parts) - 1, 0, -1):               # longest directory prefix first
        g = group_of.get("/".join(parts[:i]) + "/")
        if g:
            return g
    return default


def capture(harness_root, version, out_root, vault_base=None):
    """-> MANIFEST dict. Writes out_root/patchset and out_root/harness. Never writes into harness_root."""
    src, pristine = check_base(harness_root, version, vault_base)
    out_root = Path(out_root)
    pset_out = out_root / "patchset"
    if pset_out.exists():
        shutil.rmtree(pset_out)
    pset_out.mkdir(parents=True)
    group_of, groups = curated_map(harness_root)
    snap = f"{SNAP}.{version}"
    counts = {"patches": 0, "new_files": 0, "replaced": 0, "deleted": 0, "excluded": []}
    emitted = {}

    def put(rel_out, data):
        p = pset_out / rel_out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        emitted[rel_out] = _sha(data)

    numstat = {}
    for line in _git(src, "diff", "--numstat", "HEAD").splitlines():
        a, d, f = line.split("\t", 2)
        numstat[f] = (a, d)
    status = _git(src, "status", "--porcelain", "-z").split("\0")
    deleted = sorted(s[3:] for s in status if s.startswith(" D") or s.startswith("D "))
    modified = sorted(f for f in numstat if f not in deleted)
    for rel in modified:
        g = group_for(group_of, rel, snap)
        if numstat[rel][0] == "-":                                       # binary: replace byte-exact
            put(f"{g}/REPLACE_FILES/{rel}", (src / rel).read_bytes())
            counts["replaced"] += 1
            continue
        diff = _git(src, "diff", "--no-color", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "HEAD", "--", rel, binary=True)
        put(f"{g}/{stem(rel)}.patch", diff)
        counts["patches"] += 1
    put(f"{snap}/DELETED_FILES.manifest.txt", ("\n".join(deleted) + "\n").encode("utf-8") if deleted else b"")
    counts["deleted"] = len(deleted)
    for rel in sorted(_git(src, "ls-files", "--others", "--exclude-standard", "-z").split("\0")):
        if not rel:
            continue
        if JUNK.search(rel):
            counts["excluded"].append(rel)
            continue
        if "/" not in rel:
            if rel in PROFILE_FILES:
                g = "10.OVERRIDES"
            else:
                counts["excluded"].append(rel + "  (root-level file, not source)")
                continue
        else:
            g = group_for(group_of, rel, snap)
        put(f"{g}/NEW_FILES/{rel}", (src / rel).read_bytes())
        counts["new_files"] += 1
    # the derived harness root: the owner's policy, pointed at this set, plus the snapshot group
    h = out_root / "harness"
    (h / "config").mkdir(parents=True, exist_ok=True)
    pol = json.loads((Path(harness_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    pol["patchset_root"] = "../patchset"
    pol["groups"][snap] = {"status": "enabled", "reason": f"everything the live {version} build changed that no curated group names; "
                                                           "deletions manifest; taken by fieldkit build-harness snapshot"}
    pol["_snapshot"] = {"version": version, "live_tree": str(src), "pristine": str(pristine)}
    (h / "config" / "patch_policy.json").write_text(json.dumps(pol, indent=2), encoding="utf-8")
    link = h / "src"
    if not link.exists():
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(src)], capture_output=True)
        if not link.is_dir():
            raise task.Refused(f"could not junction {link} -> {src}: {r.stderr.decode('utf-8', 'replace')[:120]}")
    manifest = {"version": version, "live_tree": str(src), "live_head": _git(src, "rev-parse", "HEAD").strip(),
                "pristine": str(pristine), "patchset": str(pset_out), "harness_root": str(h), "counts": counts,
                "groups_used": sorted({k.split("/")[0] for k in emitted}), "files": emitted}
    (out_root / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


# -- the proof -----------------------------------------------------------------------------------------

def apply_set(pset, groups_enabled, w):
    """Apply a captured set to working copy `w` (a pristine git clone), the way the harness does. -> problems."""
    problems = []
    pset = Path(pset)
    for g in groups_enabled:
        gd = pset / g
        if not gd.is_dir():
            continue
        for pf in sorted(gd.rglob("*.patch")):
            with open(pf, "rb") as fh:
                r = subprocess.run([firefox._patch_exe(), "-p1", "--forward", "--no-backup-if-mismatch", "-d", str(w)],
                                   stdin=fh, capture_output=True, timeout=600)
            if r.returncode != 0:
                problems.append(f"{g}/{pf.name}: patch exit {r.returncode}: {r.stdout.decode('utf-8', 'replace')[-200:]}")
        for sub in ("NEW_FILES", "REPLACE_FILES"):
            root = gd / sub
            for p in sorted(root.rglob("*")) if root.is_dir() else []:
                if p.is_file():
                    rel = p.relative_to(root).as_posix()
                    if sub == "NEW_FILES" and "/" not in rel and p.name in firefox.NOT_SOURCE:
                        continue
                    d = w / rel
                    d.parent.mkdir(parents=True, exist_ok=True)
                    d.write_bytes(p.read_bytes())
        man = gd / "DELETED_FILES.manifest.txt"
        if man.is_file():
            for rel in man.read_text(encoding="utf-8").split("\n"):
                if rel.strip() and (w / rel).is_file():
                    (w / rel).unlink()
    for p in firefox.leftovers(w):
        p.unlink()
    return problems


def prove(manifest, work, vault_base=None):
    """Apply the set to a fresh pristine copy at `work` and compare with the live tree. -> report."""
    src = Path(manifest["live_tree"])
    w = Path(work)
    if w.exists():
        raise task.Refused(f"{w} exists; the proof needs a fresh folder")
    vault.restore("firefox", w, manifest["version"], base=vault_base)
    pol = json.loads((Path(manifest["harness_root"]) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    enabled = [g for g, s in pol["groups"].items() if s.get("status") == "enabled"]
    problems = apply_set(manifest["patchset"], enabled, w)
    # compare: every file in the live tree (tracked + untracked, minus junk) must be byte-identical in w, and w must
    # have nothing the live tree lacks
    tracked = {f for f in _git(src, "ls-files", "-z").split("\0") if f}
    untracked = {f for f in _git(src, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f}
    # untracked root-level files are tools or profile files, never source (capture excludes them the same way)
    live = {f for f in tracked | untracked if not JUNK.search(f) and not (f in untracked and "/" not in f)}
    live -= {f for f in live if not (src / f).is_file()}           # deleted-in-live are not in the live set
    got = set(_git(w, "ls-files", "-z").split("\0")) | set(_git(w, "ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    got = {f for f in got if f and (w / f).is_file()}
    missing = sorted(live - got)
    extra = sorted(got - live)
    # Both trees start from the same pristine commit, so a file neither tree modified is identical by git's own
    # guarantee; only files modified or added in either tree need their bytes compared (Firefox has ~350k files).
    touched = set()
    for repo in (src, w):
        touched |= {f for f in _git(repo, "diff", "--name-only", "-z", "HEAD").split("\0") if f}
        touched |= {f for f in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if f}
    differ = []
    for f in sorted((live & got) & touched):
        a, b = (src / f), (w / f)
        if a.stat().st_size != b.stat().st_size or _sha(a.read_bytes()) != _sha(b.read_bytes()):
            differ.append(f)
    ok = not problems and not missing and not extra and not differ
    rep = {"ok": ok, "patch_problems": problems, "missing_in_rebuilt": missing, "extra_in_rebuilt": extra, "differ": differ,
           "files_compared": len((live & got) & touched), "identical_by_construction": len((live & got) - touched), "work": str(w)}
    Path(manifest["patchset"]).parent.joinpath("PROOF.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return rep


# -- the curated set, judged by the live tree ------------------------------------------------------------

def compare_curated(harness_root):
    """-> {group: {verdict: n}}, dead hunks list. 'dead' = the curated patch says it, the built tree does not have it."""
    src = live_tree(harness_root)
    cache = {}

    def body(f):
        if f not in cache:
            p = src / f
            cache[f] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else None
        return cache[f]
    counts, dead = {}, []
    for g, rel, file, n, h in verify.hunks_in_scope(harness_root):
        v, d = verify.score_hunk(body(file), h)
        counts.setdefault(g, {"APPLIED": 0, "PARTIAL": 0, "NOT-APPLIED": 0, "TARGET-GONE": 0, "NO-SIGNAL": 0})[v] += 1
        if v in ("NOT-APPLIED", "PARTIAL", "TARGET-GONE"):
            dead.append(f"{rel} {file} #{n} [{v}] {d}")
    return counts, dead


def lines_capture(m):
    c = m["counts"]
    out = [f"SNAPSHOT of the live {m['version']} build ({m['live_head'][:12]}) -> {m['patchset']}",
           f"  {c['patches']} patch(es), {c['new_files']} new file(s), {c['replaced']} binary replacement(s), {c['deleted']} deletion(s)",
           f"  groups: {', '.join(m['groups_used'])}",
           f"  excluded as backups/junk/not source ({len(c['excluded'])}): {c['excluded'][:6]}{' ...' if len(c['excluded']) > 6 else ''}",
           f"  derived harness root: {m['harness_root']}"]
    return out


def lines_proof(r):
    out = [f"PROOF: rebuilt from pristine + the snapshot; {r['files_compared']} touched files compared byte by byte with the live tree, "
           f"{r.get('identical_by_construction', 0)} untouched files identical by construction (same pristine commit)",
           f"  patch problems: {len(r['patch_problems'])}", f"  missing in the rebuilt tree: {len(r['missing_in_rebuilt'])}",
           f"  extra in the rebuilt tree: {len(r['extra_in_rebuilt'])}", f"  differ: {len(r['differ'])}"]
    for k in ("patch_problems", "missing_in_rebuilt", "extra_in_rebuilt", "differ"):
        out += [f"    {x}" for x in r[k][:5]]
    out.append("PROOF: " + ("IDENTICAL - the snapshot reproduces the build" if r["ok"] else "FAILED - do not port from this snapshot"))
    return out
