"""Two outputs of the same probe, before and after a change: what was lost, what changed, what is newly set.

    fieldkit build-harness probe-compare A B [prefix=TOK] [keys=2] [value=last|all] [limit=200]

The design-tokens probe (2026-10-07, theme item 1 of build 28) prints one line per token and surface,
`TOK|<surface>|<name>|<computed value>|<value in this scheme>`, some 2,000 lines per run. The token files had been
replaced by 155-era copies and some 40 tokens 157 uses were gone; reading two such outputs side by side by eye does
not find 40 lines in 2,000, so a throwaway script did the diff. This is that script, for any probe that prints
`KIND|key|...|value` lines (TOK, IMG, BRAND, SMALL, UI, THEME): `keys` fields after the prefix name the row, the last
field (or all fields after the key, value=all) is what is compared.

    LOST      set in A, "(unset)" or absent in B     (a token the change lost: the usual damage)
    changed   set in both, different
    now-set   "(unset)" or absent in A, set in B

A and B are files holding the probe's output (the probe command's own output saved to a file is fine: leading
spaces and other lines are ignored). A key that occurs twice in one file keeps its last value; the count is reported.
Exit 3 when anything is LOST (every difference must be intended; a loss is the one that never is by accident).
"""
import collections
from pathlib import Path

UNSET = "(unset)"
CATEGORIES = ("LOST", "changed", "now-set")


def load(text, prefix="TOK", keys=2, value="last"):
    """-> ({key tuple: value}, duplicates). Only lines `prefix|...` with more than `keys` fields after the prefix."""
    rows, dups = {}, 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith(prefix + "|"):
            continue
        f = line.split("|")
        if len(f) < keys + 2:
            continue
        key = tuple(f[1:1 + keys])
        if key in rows:
            dups += 1
        rows[key] = f[-1] if value == "last" else "|".join(f[1 + keys:])
    return rows, dups


def compare(a, b):
    """{key: value} twice -> {"LOST": [(key, a, b)], "changed": [...], "now-set": [...], "same": n}."""
    out = {c: [] for c in CATEGORIES}
    same = 0
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, UNSET), b.get(k, UNSET)
        if x == y:
            same += 1
            continue
        cat = "now-set" if x == UNSET else "LOST" if y == UNSET else "changed"
        out[cat].append((k, x, y))
    out["same"] = same
    return out


def run(path_a, path_b, prefix="TOK", keys=2, value="last"):
    """-> {"a", "b", "prefix", "rows_a", "rows_b", "duplicates_a", "duplicates_b", "LOST", "changed", "now-set", "same"}"""
    a, da = load(Path(path_a).read_text(encoding="utf-8", errors="replace"), prefix, keys, value)
    b, db = load(Path(path_b).read_text(encoding="utf-8", errors="replace"), prefix, keys, value)
    if not a and not b:
        raise ValueError(f"neither file holds a {prefix}|... line: wrong prefix, or not the probe's output")
    return {"a": str(path_a), "b": str(path_b), "prefix": prefix, "rows_a": len(a), "rows_b": len(b),
            "duplicates_a": da, "duplicates_b": db, **compare(a, b)}


def lines(r, limit=200):
    out = [f"A: {r['a']} ({r['rows_a']} {r['prefix']} rows" + (f", {r['duplicates_a']} repeated key(s), last kept" if r["duplicates_a"] else "") + ")",
           f"B: {r['b']} ({r['rows_b']} {r['prefix']} rows" + (f", {r['duplicates_b']} repeated key(s), last kept" if r["duplicates_b"] else "") + ")",
           f"same: {r['same']}"]
    for c in CATEGORIES:
        out.append(f"== {c}: {len(r[c])}")
        for k, x, y in r[c][:limit]:
            out.append(f"  {' '.join(f'{p:8}' for p in k[:-1])} {k[-1]:45} {x[:50]:50} -> {y[:50]}")
        if len(r[c]) > limit:
            out.append(f"  ... {len(r[c]) - limit} more (limit={limit})")
    return out


def summary_counts(r):
    return collections.OrderedDict((c, len(r[c])) for c in CATEGORIES)
