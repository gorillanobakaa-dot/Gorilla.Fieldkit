# Version: 1.0.0 · updated 26-10-26-20-00
"""Is a merge of main ONLY a merge? Proven from git, so it never costs a second full suite (the merge train, part 1).

    fieldkit ship update [--base main]        merge origin/main into this branch the train's way: generated files
                                              regenerated, cards merged entry by entry, the proof below, then the
                                              commit through the pre-commit hook (never --no-verify)
    fieldkit ship proof OURS THEIRS [RESULT]  what each path of a merge is (below); exit 0 only a merge, 3 not
    fieldkit ship ci-record SHA               remember that GitHub's CI is green for SHA (gh); the hook reads it offline
    python -m fieldkit.method.mergeproof hook --key K   the pre-commit hook's question (exit 0: run only these tests)

2026-10-26: PR #124 went CONFLICTING twice when main moved (#123, then #126); each local merge of main ran the full
pre-commit suite again (30-40 min each, ~75 min lost), although the merge only brought files main's CI had already
tested plus a generated index. testcost already skips a tree that passed; a merge commit is a new tree.

Every path a merge touches gets ONE kind, recomputed from the three trees (merge base, ours, theirs) and the result:
    ours          the result keeps our version, and main did not change it
    from-main     the result is main's version, and we did not change it
    generated     a file in generated.yaml whose content equals its generator's output on the result (fieldkit drift)
    entry-merged  a card file both sides changed, equal to the merge driver's entry-by-entry merge (mergedriver.py)
    line-merged   both sides changed it, and git's own line merge gives exactly the result, cleanly
    hand-edit     anything else: a resolution somebody typed, a file dropped or added by hand
    generated-drift / generated-unchecked   a generated file that does not equal its generator, or was not checked
"only a merge" (merge_clean) = no hand-edit and no generated problem. The pre-commit hook skips the full suite only
when, in addition, the tree we merged INTO already passed (the full suite or this proof, or CI recorded green), main's
commit passed CI (recorded by ci-record, or its tree passed the suite here), the commit carries exactly the merged
tree (nothing untracked) and no path is line-merged: then only the tests that cover the changed paths run (testcost
plan). Fail closed: anything else runs the full suite. CI stays the final gate. Exit 0 / 3, 2 bad input.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import emit

GEN_FILE = "fieldkit/method/generated.yaml"
DRIVER = "fieldkit/method/mergedriver.py"
DRIVER_FILES = ("fieldkit/desk/platform_index.json", "fieldkit/desk/tools.yaml", "docs/groups.yaml")
SKIP_KINDS = {"ours", "from-main", "generated", "entry-merged"}
CLEAN_KINDS = SKIP_KINDS | {"line-merged"}


def _git(repo, *args, check=True, env=None, text=True):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, env=env,
                       **({"encoding": "utf-8", "errors": "replace"} if text else {}))
    if check and r.returncode:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)}: {err.strip()[-300:]}")
    return r.stdout if check else r


def rev(repo, ref):
    r = _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    return r.stdout.strip() or None


def common_dir(repo):
    return Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())


def state_dir(repo):
    """Shared by every worktree of the clone, never committed: <git common dir>/fieldkit."""
    d = common_dir(repo) / "fieldkit"
    d.mkdir(parents=True, exist_ok=True)
    return d


# -- generated files ---------------------------------------------------------------------------------------------------
def generators(tree_dir=None):
    """{path: argv} from the merged tree's generated.yaml (else Fieldkit's own copy); {python} = this interpreter."""
    import yaml
    p = Path(tree_dir) / GEN_FILE if tree_dir else None
    if p is None or not p.is_file():
        p = Path(__file__).with_name("generated.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {k: [str(a).replace("{python}", sys.executable) for a in v] for k, v in (data.get("generated") or {}).items()}


def _gen_env():
    return dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def gen_matches(tree_dir, path, argv):
    """-> (matches, evidence) through fieldkit drift (JSON compared as data)."""
    from .drift import drift
    if not (Path(tree_dir) / path).is_file():
        return False, f"{path} is missing in the merged tree"
    r = drift(argv, path, cwd=str(tree_dir), env=_gen_env())
    if not r.get("ok"):
        return False, r.get("error", "the generator did not run")
    return (not r["drift"]), ("equals its generator" if not r["drift"] else
                              f"{r['differences']} difference(s) from its generator: {'; '.join(r['first'][:3])}")


def regenerate(tree_dir, path, argv):
    """Write the generator's output to `path` -> True when the file changed. Raises when the generator fails."""
    r = subprocess.run(argv, cwd=str(tree_dir), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=_gen_env(), timeout=600)
    if r.returncode:
        raise RuntimeError(f"the generator of {path} failed (exit {r.returncode}): {r.stderr.strip()[-300:]}")
    f = Path(tree_dir) / path
    old = f.read_text(encoding="utf-8") if f.is_file() else None
    if old == r.stdout:
        return False
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(r.stdout, encoding="utf-8", newline="\n")
    return True


# -- the proof ---------------------------------------------------------------------------------------------------------
def entries(repo, treeish):
    """{path: "mode sha"} of every file in a commit or tree."""
    out = {}
    for line in _git(repo, "ls-tree", "-r", "-z", "--full-tree", treeish).split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            mode, _, sha = meta.split()
            out[path] = f"{mode} {sha}"
    return out


def expected3(b, o, t):
    """The result a three-way merge must give for one path, or None when both sides changed it differently."""
    if o == t:
        return o
    if o == b:
        return t
    if t == b:
        return o
    return None


def _blob(repo, entry):
    return b"" if entry is None else _git(repo, "cat-file", "blob", entry.split()[1], text=False)


def _three_files(repo, b, o, t, td):
    names = []
    for n, e in (("base", b), ("ours", o), ("theirs", t)):
        p = Path(td) / n
        p.write_bytes(_blob(repo, e))
        names.append(str(p))
    return names


def line_merge(repo, b, o, t):
    """git's own line merge of the three blobs -> bytes when clean, else None."""
    with tempfile.TemporaryDirectory() as td:
        fb, fo, ft = _three_files(repo, b, o, t, td)
        r = subprocess.run(["git", "merge-file", "-p", fo, fb, ft], capture_output=True)
        return r.stdout if r.returncode == 0 else None


def driver_merge(repo, result, path, b, o, t):
    """The card merge driver (PR #129) on the three blobs -> bytes when it merges, else None. The driver is read from
    the merged tree itself, so its rule is the one the result was made with; no driver in the tree -> None."""
    if path not in DRIVER_FILES or not rev(repo, f"{result}:{DRIVER}"):
        return None
    with tempfile.TemporaryDirectory() as td:
        fb, fo, ft = _three_files(repo, b, o, t, td)
        drv = Path(td) / "mergedriver.py"
        drv.write_bytes(_git(repo, "show", f"{result}:{DRIVER}", text=False))
        r = subprocess.run([sys.executable, str(drv), "merge", fb, fo, ft, path], capture_output=True, cwd=td)
        return Path(fo).read_bytes() if r.returncode == 0 else None


def prove(repo, ours, theirs, result, tree_dir=None, gens=None):
    """-> {"merge_clean", "skip_ok", "rows": [{path, kind, why}], "changed": [paths the result changes vs ours]}.
    `tree_dir` is a checkout whose files ARE the result (the generators run there); None = generated unchecked."""
    bases = _git(repo, "merge-base", "--all", ours, theirs, check=False).stdout.split()
    if len(bases) != 1:
        return {"merge_clean": False, "skip_ok": False, "changed": [],
                "rows": [{"path": "-", "kind": "hand-edit", "why": f"{len(bases)} merge bases: not a plain merge"}]}
    gens = generators(tree_dir) if gens is None else gens
    B, O, T, R = (entries(repo, x) for x in (bases[0], ours, theirs, result))
    rows = []
    for path in sorted(set(B) | set(O) | set(T) | set(R)):
        b, o, t, r = B.get(path), O.get(path), T.get(path), R.get(path)
        if b == o == t == r:
            continue
        if path in gens:
            if r is None:
                kind, why = "hand-edit", "a generated file was removed"
            elif tree_dir is None:
                kind, why = "generated-unchecked", "no checkout of the result to run the generator in"
            else:
                ok, why = gen_matches(tree_dir, path, gens[path])
                kind = "generated" if ok else "generated-drift"
            rows.append({"path": path, "kind": kind, "why": why})
            continue
        want = expected3(b, o, t)
        if want is not None:
            if r == want:
                kind = "ours" if r == o else "from-main"
                rows.append({"path": path, "kind": kind, "why": ""})
            else:
                rows.append({"path": path, "kind": "hand-edit", "why": "differs from the three-way result"})
            continue
        got = None if r is None or o is None or t is None else _blob(repo, r)
        if got is not None and o.split()[0] == t.split()[0] == r.split()[0]:
            if driver_merge(repo, result, path, b, o, t) == got:
                rows.append({"path": path, "kind": "entry-merged", "why": "equals the merge driver's result"})
                continue
            if line_merge(repo, b, o, t) == got:
                rows.append({"path": path, "kind": "line-merged", "why": "git's line merge, clean"})
                continue
        rows.append({"path": path, "kind": "hand-edit", "why": "both sides changed it; the result is no tool's merge"})
    kinds = {x["kind"] for x in rows}
    return {"merge_clean": kinds <= CLEAN_KINDS, "skip_ok": kinds <= SKIP_KINDS, "rows": rows,
            "changed": sorted(p for p in set(O) | set(R) if O.get(p) != R.get(p)), "base": bases[0]}


def summary(proof):
    n = {}
    for x in proof["rows"]:
        n[x["kind"]] = n.get(x["kind"], 0) + 1
    return ", ".join(f"{v} {k}" for k, v in sorted(n.items())) or "nothing changed"


# -- CI results, remembered for the offline hook -----------------------------------------------------------------------
def _ci_file(repo):
    return state_dir(repo) / "ci-green.jsonl"


def ci_known(repo, sha):
    p = _ci_file(repo)
    if not sha or not p.is_file():
        return None
    for line in reversed(p.read_text(encoding="utf-8", errors="replace").splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("sha") == sha:
            return row
    return None


def _gh_ci_green(repo, sha):
    from ..core import sync
    return sync.ci_green(sync._slug(repo), sha)


def ci_record(repo, sha, check=None):
    """Ask GitHub (gh) whether CI is green for `sha` on every system; only a green answer is written -> (ok, why)."""
    if ci_known(repo, sha):
        return True, f"CI green for {sha[:10]} (recorded)"
    ok, why = (check or _gh_ci_green)(repo, sha)
    if ok:
        with open(_ci_file(repo), "a", encoding="utf-8") as f:
            f.write(json.dumps({"sha": sha, "t": time.strftime("%Y-%m-%d %H:%M:%S"), "evidence": why}) + "\n")
    return ok, why


# -- the pre-commit hook's question ------------------------------------------------------------------------------------
def tested(repo, sha, seen=None):
    """Did the tree of commit `sha` pass here (full suite or this proof), or is CI recorded green for it? -> why or None."""
    from .. import testcost
    seen = seen or testcost.seen
    tree = rev(repo, f"{sha}^{{tree}}")
    for check in ("full-suite", "merge-proof"):
        hit = seen(check, tree)
        if hit:
            return f"{check} passed on its tree at {hit.get('t', '?')}"
    row = ci_known(repo, sha)
    return f"CI green ({row['t']})" if row else None


def hook_decide(repo=".", key=None, base="main", seen=None, gens=None):
    """-> (skip, why, tests). skip=True: the full suite need not run; run `tests` (maybe none) instead."""
    from .. import testcost
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    heads = (git_dir / "MERGE_HEAD").read_text().split() if (git_dir / "MERGE_HEAD").is_file() else []
    if len(heads) != 1:
        return False, "not a merge of one branch", []
    theirs, ours = heads[0], rev(repo, "HEAD")
    tree = _git(repo, "write-tree").strip()          # the tree this commit will carry (objects only, index untouched)
    if key and key != tree:
        return False, "the working tree holds files the commit does not carry (untracked or unstaged)", []
    on_base = rev(repo, f"refs/remotes/origin/{base}")
    if not on_base or _git(repo, "merge-base", "--is-ancestor", theirs, on_base, check=False).returncode:
        return False, f"the merged commit {theirs[:10]} is not on origin/{base}", []
    t_ours = tested(repo, ours, seen)
    if not t_ours:
        return False, f"our side {ours[:10]} never passed here (no full suite, no proof, no CI record)", []
    t_theirs = tested(repo, theirs, seen)
    if not t_theirs:
        return False, f"origin/{base} {theirs[:10]} has no CI record: fieldkit ship ci-record {theirs[:10]}", []
    proof = prove(repo, ours, theirs, tree, tree_dir=repo, gens=gens)
    if not proof["skip_ok"]:
        bad = [f"{x['path']} ({x['kind']})" for x in proof["rows"] if x["kind"] not in SKIP_KINDS]
        return False, "not only a merge: " + ", ".join(bad[:5]), []
    tests = testcost.plan(repo, paths=proof["changed"])["tests"]
    return True, (f"merge of origin/{base} {theirs[:10]} ({t_theirs}) into {ours[:10]} ({t_ours}): "
                  f"{summary(proof)}; {len(tests)} test file(s) cover the changed paths"), tests


# -- fieldkit ship update ----------------------------------------------------------------------------------------------
def _resolve_with_driver(repo, path):
    """A conflicted card file: run the merge driver from the working tree on the index stages -> True when merged."""
    drv = Path(repo) / DRIVER
    if path not in DRIVER_FILES or not drv.is_file():
        return False
    with tempfile.TemporaryDirectory() as td:
        files = []
        for stage, name in ((1, "base"), (2, "ours"), (3, "theirs")):
            p = Path(td) / name
            r = _git(repo, "show", f":{stage}:{path}", check=False, text=False)
            p.write_bytes(r.stdout if r.returncode == 0 else b"")
            files.append(str(p))
        r = subprocess.run([sys.executable, str(drv), "merge", *files, path], capture_output=True, cwd=td)
        if r.returncode != 0:
            return False
        (Path(repo) / path).write_bytes(Path(files[1]).read_bytes())
    _git(repo, "add", "--", path)
    return True


def update(repo=".", base="main", gens=None, ci=None, commit=True, fetch=True):
    """Merge origin/<base> into the checked-out branch, the train's way. Never forces; aborts rather than guess."""
    branch = _git(repo, "branch", "--show-current").strip()
    if not branch or branch == base:
        return {"ok": False, "state": "no-branch", "next": f"check out the branch to bring up to date with {base}"}
    if _git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        return {"ok": False, "state": "dirty", "branch": branch, "next": "commit or set aside the changes first"}
    if fetch:
        _git(repo, "fetch", "--quiet", "origin", base, check=False)
    theirs, ours = rev(repo, f"refs/remotes/origin/{base}"), rev(repo, "HEAD")
    rep = {"branch": branch, "base": base, "ours": ours, "theirs": theirs}
    if not theirs:
        return dict(rep, ok=False, state="no-base", next=f"origin/{base} is unknown: git fetch origin")
    if _git(repo, "merge-base", "--is-ancestor", theirs, ours, check=False).returncode == 0:
        return dict(rep, ok=True, state="up-to-date", next="nothing to merge")
    if ci is not False:                                # remember CI for both sides while online (the hook is offline)
        for sha in (theirs, ours):
            try:
                ci_record(repo, sha, ci)
            except Exception:  # noqa: BLE001 - no record only means the hook runs the full suite
                pass
    m = _git(repo, "merge", "--no-ff", "--no-commit", f"refs/remotes/origin/{base}", check=False)
    gens = generators(repo) if gens is None else gens
    conflicted = _git(repo, "diff", "--name-only", "--diff-filter=U").split()
    unresolved = []
    for path in conflicted:
        if path in gens:
            _git(repo, "checkout", "--theirs", "--", path, check=False)   # replaced by the generator below
        elif not _resolve_with_driver(repo, path):
            unresolved.append(path)
    if m.returncode and not conflicted:
        _git(repo, "merge", "--abort", check=False)
        return dict(rep, ok=False, state="merge-failed", error=(m.stderr or m.stdout).strip()[-300:],
                    next="read the error; nothing was committed")
    if unresolved:
        _git(repo, "merge", "--abort", check=False)
        return dict(rep, ok=False, state="conflict", paths=unresolved,
                    next="a real conflict: the session that owns the branch resolves it (git merge origin/"
                         f"{base}); generated files and cards are not the problem")
    regenerated = []
    try:
        for path, argv in gens.items():
            if (Path(repo) / path).is_file() or path in conflicted:
                if regenerate(repo, path, argv) or path in conflicted:
                    regenerated.append(path)
                _git(repo, "add", "--", path)
    except RuntimeError as e:
        _git(repo, "merge", "--abort", check=False)
        return dict(rep, ok=False, state="generator-failed", error=str(e), next="fix the generator; nothing committed")
    tree = _git(repo, "write-tree").strip()
    proof = prove(repo, ours, theirs, tree, tree_dir=repo, gens=gens)
    if not proof["merge_clean"]:
        _git(repo, "merge", "--abort", check=False)
        bad = [f"{x['path']} ({x['kind']}: {x['why']})" for x in proof["rows"] if x["kind"] not in CLEAN_KINDS]
        return dict(rep, ok=False, state="not-only-a-merge", error="; ".join(bad[:5]),
                    next="nothing committed: look at these paths by hand")
    rep.update(proof=summary(proof), regenerated=regenerated, skip_ok=proof["skip_ok"])
    if not commit:
        return dict(rep, ok=True, state="merged-uncommitted", next="git commit --no-edit (through the hook)")
    msg = f"Merge origin/{base} into {branch} (fieldkit ship update: {summary(proof)})"
    trailers = _git(repo, "config", "--get-all", "fieldkit.trailer", check=False).stdout.strip()
    if trailers:
        msg += "\n\n" + trailers
    c = _git(repo, "commit", "-m", msg, check=False)
    if c.returncode:
        return dict(rep, ok=False, state="commit-refused", error=(c.stdout + c.stderr).strip()[-1500:],
                    next="the pre-commit hook refused: the merge is still in progress; read why, fix, commit")
    hook = [ln for ln in (c.stdout + c.stderr).splitlines() if ln.startswith("pre-commit:")]
    return dict(rep, ok=True, state="merged", head=rev(repo, "HEAD"), hook=hook[-5:],
                next="push it: fieldkit ship push")


def lines(d):
    out = [f"{d.get('branch', '?')}: {d.get('state')}" + (f" ({d['proof']})" if d.get("proof") else "")]
    if d.get("regenerated"):
        out.append("regenerated: " + ", ".join(d["regenerated"]))
    for p in d.get("paths") or []:
        out.append(f"  CONFLICT {p}")
    if d.get("error"):
        out.append(f"error: {d['error']}")
    out += [f"  {h}" for h in d.get("hook") or []]
    return out


def hook_lines(tests, repo="."):
    """The test files the hook passes to pytest, as bytes: LF only and forward slashes. Windows text mode wrote CRLF,
    the CR stuck to every name in `sh` and pytest said "file or directory not found: tests/test_mandate.py"."""
    return "".join(Path(os.path.relpath(t, repo)).as_posix() + "\n" for t in tests).encode()


def main(argv=None, prog="python -m fieldkit.method.mergeproof"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hook")
    h.add_argument("--key")
    h.add_argument("--base", default="main")
    p = sub.add_parser("prove")
    p.add_argument("ours")
    p.add_argument("theirs")
    p.add_argument("result", nargs="?", default="HEAD")
    p.add_argument("--checkout", help="a checkout whose files are the result (the generators run there)")
    c = sub.add_parser("ci-record")
    c.add_argument("sha")
    u = sub.add_parser("update")
    u.add_argument("--base", default="main")
    for s in (h, p, c, u):
        s.add_argument("--repo", default=".")
        s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "hook":
        try:
            skip, why, tests = hook_decide(a.repo, a.key, a.base)
        except Exception as e:  # noqa: BLE001 - any doubt runs the full suite
            skip, why, tests = False, f"the proof could not run ({e})", []
        print(("merge proof: " if skip else "merge proof: full suite: ") + why, file=sys.stderr)
        sys.stdout.flush()
        sys.stdout.buffer.write(hook_lines(tests, a.repo))
        sys.stdout.flush()
        return 0 if skip else 1
    if a.cmd == "prove":
        ours, theirs = rev(a.repo, a.ours), rev(a.repo, a.theirs)
        if not ours or not theirs or not rev(a.repo, a.result):
            ap.error("unknown commit")
        r = prove(a.repo, ours, theirs, a.result, tree_dir=a.checkout)
        emit(r, a.json, lambda d: [("ONLY A MERGE" if d["merge_clean"] else "NOT ONLY A MERGE") + f": {summary(d)}"]
             + [f"  {x['kind']:<20} {x['path']}" + (f"  ({x['why']})" if x["why"] else "") for x in d["rows"]])
        return 0 if r["merge_clean"] else 3
    if a.cmd == "ci-record":
        sha = rev(a.repo, a.sha)
        if not sha:
            ap.error(f"unknown commit {a.sha}")
        ok, why = ci_record(a.repo, sha)
        emit({"ok": ok, "sha": sha, "why": why}, a.json, lambda d: [("recorded: " if ok else "NOT recorded: ") + why])
        return 0 if ok else 3
    r = update(a.repo, a.base)
    emit(r, a.json, lines)
    return 0 if r.get("ok") else 3


if __name__ == "__main__":
    sys.exit(main())
