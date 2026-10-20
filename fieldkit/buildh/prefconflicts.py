# Version: 1.0.0 · updated 26-10-16-23-40
"""One pref, one value: no two definitions the patch set adds may disagree.

2026-10-16: D-158-12 decided Segoe UI and firefox.js said Segoe UI, but 05.PREFS still wrote the 157 "Inter" into
all.js. Firefox loads all.js first and firefox.js last, so Segoe UI shipped; the clash stayed in the set, the owner's
Windows preflight flagged it, and a settled decision was asked again (about twenty times on Linux the day before).
The owner: "make it a deterministic tool for these kind of things ... we can't have this happening again".

What is judged: every pref(...) / lockPref(...) / sticky_pref(...) line the SET ADDED (a line pristine does not have)
in the pref files Firefox reads (PREF_FILES). Two added definitions of one pref CONFLICT when their values differ and
their #if conditions can both be true: the same condition, or one of them unconditional. Per-system values under
different #ifdef branches are not a conflict. Upstream's own lines are never judged: overriding them is the point.

Runs (fail closed): set-proof on pristine + each system's selection (the patch set's pre-push hook, both systems),
the build gate, and `fieldkit build-harness pref-conflicts TASK`."""
import re
import subprocess
from pathlib import Path

PREF_FILES = ("modules/libpref/init/all.js", "browser/app/profile/firefox.js",
              "browser/branding/gorilla/pref/firefox-branding.js")
_DEF = re.compile(r'^\s*(pref|lockPref|sticky_pref)\(\s*"([^"]+)"\s*,\s*(.+?)\s*(,\s*(locked|sticky)\s*)?\)\s*;')


def definitions(text, upstream_lines=()):
    """-> [{name, value, cond, line, added}] in file order; cond is the #if stack as one string ("" = always)."""
    up, out, stack = set(upstream_lines), [], []
    for n, line in enumerate(text.splitlines(), 1):
        t = line.strip()
        if t.startswith("#if"):
            stack.append(t)
            continue
        if t.startswith(("#elif", "#else")):
            if stack:
                stack[-1] = stack[-1] + " | " + t
            continue
        if t.startswith("#endif"):
            if stack:
                stack.pop()
            continue
        m = _DEF.match(line)
        if m:
            out.append({"name": m.group(2), "value": m.group(3).strip(), "cond": " && ".join(stack), "line": n,
                        "added": line not in up})
    return out


def _overlap(a, b):
    return a == b or not a or not b


def conflicts(defs_by_file):
    """{rel: [definitions]} -> [{name, a, b}] for added definitions that disagree under overlapping conditions."""
    added = [(rel, d) for rel, ds in defs_by_file.items() for d in ds if d["added"]]
    by = {}
    for rel, d in added:
        by.setdefault(d["name"], []).append((rel, d))
    out = []
    for name, ds in sorted(by.items()):
        for i, (ra, a) in enumerate(ds):
            for rb, b in ds[i + 1:]:
                if a["value"] != b["value"] and _overlap(a["cond"], b["cond"]):
                    out.append({"name": name, "a": f"{ra}:{a['line']} = {a['value']}",
                                "b": f"{rb}:{b['line']} = {b['value']}"})
    return out


def _show(tree, rev, rel):
    r = subprocess.run(["git", "-C", str(tree), "show", f"{rev}:{rel}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def scan(tree, pristine_tree=None, pristine_rev=None):
    """Conflicts in `tree`. Upstream lines come from `pristine_rev` in `pristine_tree` (default: the tree's own
    first commit, which the harness makes from the pristine release)."""
    tree = Path(tree)
    src = Path(pristine_tree) if pristine_tree else tree
    if pristine_rev is None:
        r = subprocess.run(["git", "-C", str(src), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True)
        pristine_rev = (r.stdout.split() or [None])[0]
    defs = {}
    for rel in PREF_FILES:
        p = tree / rel
        if p.is_file():
            up = _show(src, pristine_rev, rel).splitlines() if pristine_rev else []
            defs[rel] = definitions(p.read_text(encoding="utf-8", errors="replace"), up)
    return conflicts(defs)


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def _mark_runs(body, runs):
    """Indexes of body lines that form one of `runs` (a list of consecutive '+' line contents), whole runs only:
    a lone "#endif" is in every pref block (2026-10-16: matching single lines removed 17 lines instead of 8)."""
    hit = set()
    for run in runs:
        n = len(run)
        for k in range(len(body) - n + 1):
            seg = body[k:k + n]
            if all(s.startswith("+") and not s.startswith("+++") and s[1:] == r for s, r in zip(seg, run)):
                hit.update(range(k, k + n))
    return hit


def drop_added_lines(text, runs):
    """Remove whole runs of '+' lines (each run: a list of line contents without the '+', consecutive in the hunk)
    from a unified diff, and correct each hunk's new-side count and the later hunks' new-side start.
    -> (text, removed count). The fix for a conflict goes into the patch that adds the wrong line, so every system
    gets one value, not only this tree."""
    out, removed, shift = [], 0, 0
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        m = _HUNK.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        a, ac, b, bc, tail = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4), m.group(5)
        body, j = [], i + 1
        while j < len(lines) and not _HUNK.match(lines[j]) and not lines[j].startswith("diff --git "):
            body.append(lines[j])
            j += 1
        hit = _mark_runs(body, runs)
        kept = [l for k, l in enumerate(body) if k not in hit]
        gone = len(body) - len(kept)
        removed += gone
        newc = (int(bc) if bc is not None else 1) - gone
        out.append(f"@@ -{a}{',' + ac if ac is not None else ''} +{b + shift},{newc} @@{tail}")
        out += kept
        shift -= gone
        i = j
        if j < len(lines) and lines[j].startswith("diff --git "):
            shift = 0
    return "\n".join(out), removed


def lines(found):
    if not found:
        return ["PREF-CONFLICTS: none - every pref the set defines has one value"]
    out = [f"PREF-CONFLICTS: {len(found)} pref(s) the set defines with two values (one decision, one value)"]
    out += [f"  {c['name']}: {c['a']}  vs  {c['b']}" for c in found]
    out.append("NEXT: keep the decided value (the decision register says which), delete or correct the other line in "
               "the patch that adds it")
    return out
