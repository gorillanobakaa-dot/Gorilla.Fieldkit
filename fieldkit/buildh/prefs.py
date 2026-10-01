"""Preference files (firefox.js, all.js) ported by PREF NAME, not by line.

Read from the real 155->157 hunks (2026-10-01): the owner's changes are `pref("name", value[, locked])` lines whose
NAMES still exist in the new file in 16 of 18 cases (h34), 10 of 16 (h35), 10 of 11 (h39), usually with upstream's
default value and often moved or wrapped in a new #ifdef. A value the owner set belongs on the pref of that name
wherever it now lives. Names upstream removed go to the owner with candidates; names the owner ADDS are placed by
the line-level merge (they have no home yet).

  changes(hunk)         -> {"set": {name: new line text}, "drop": [names], "add": [lines]} from the hunk's +/- lines
  port(lines, hunk)     -> (new_lines, notes, gone) ; sets each named pref in place (the pref must be defined
                           exactly once), drops owner-removed prefs, leaves 'add' lines to the caller
  check(before, after, hunk) -> problems: every set pref reads as wanted; nothing else named changed
"""
import re

PREF = re.compile(r'^\s*(?:pref|user_pref|sticky_pref|lockPref)\("([^"]+)"\s*,')


def name_of(line):
    m = PREF.match(line)
    return m.group(1) if m else None


def _norm(line):
    """A pref line without indentation and without a trailing // comment (the owner comments his prefs)."""
    return re.sub(r"\s*//.*$", "", line.strip())


def changes(hunk):
    removed = {name_of(l[1:]): l[1:] for l in hunk["lines"] if l.startswith("-") and name_of(l[1:])}
    added = {name_of(l[1:]): l[1:] for l in hunk["lines"] if l.startswith("+") and name_of(l[1:])}
    out = {"set": {}, "drop": [], "add": []}
    for n, line in added.items():
        if n in removed:
            if _norm(removed[n]) != _norm(line):
                out["set"][n] = line.strip()
        else:
            out["add"].append(line)
    out["drop"] = [n for n in removed if n not in added]
    return out


def defined(lines):
    """name -> [line indexes] of every pref definition in the file."""
    out = {}
    for i, l in enumerate(lines):
        n = name_of(l)
        if n:
            out.setdefault(n, []).append(i)
    return out


def candidates(name, names):
    """Upstream renames keep most of the dotted path: a.b.c.enabled -> a.b.c.enable, a.b.cNew.enabled."""
    parts = name.split(".")
    if len(parts) < 3:
        return []
    prefix = ".".join(parts[:-1])
    out = [n for n in names if n != name and n.startswith(prefix + ".")]
    if not out:
        prefix = ".".join(parts[:-2])
        out = [n for n in names if n != name and n.startswith(prefix + ".") and n.split(".")[-1] == parts[-1]]
    return sorted(out)[:5]


def port(lines, hunk):
    """-> (new_lines, notes, gone). `gone` = [(name, candidates)] for prefs to set or drop that no longer exist."""
    ch = changes(hunk)
    have = defined(lines)
    new = list(lines)
    notes, gone, edits = [], [], []
    for n, text in ch["set"].items():
        if n not in have:
            gone.append((n, candidates(n, have)))
        elif len(have[n]) != 1:
            gone.append((n, [f"defined {len(have[n])} times in the file (lines {[i + 1 for i in have[n]]})"]))
        else:
            i = have[n][0]
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            edits.append((i, i + 1, [indent + text]))
    for n in ch["drop"]:
        if n in have and len(have[n]) == 1:
            edits.append((have[n][0], have[n][0] + 1, []))
        elif n in have:
            gone.append((n, [f"defined {len(have[n])} times in the file"]))
        else:
            notes.append(f"{n}: already gone from this Firefox, nothing to drop")
    if gone:
        return list(lines), notes, gone
    for start, end, repl in sorted(edits, reverse=True):
        new[start:end] = repl
    if edits:
        notes.append(f"ported by pref name: {len(ch['set'])} set, {len([e for e in edits if not e[2]])} dropped")
    return new, notes, []


def check(before, after, hunk, collateral=True):
    ch = changes(hunk)
    b, a = defined(before), defined(after)
    why = []
    for n, text in ch["set"].items():
        if n not in a:
            why.append(f"pref {n} is missing")
        elif _norm(after[a[n][0]]) != _norm(text):
            why.append(f"pref {n} does not read as the patch wants: {after[a[n][0]].strip()[:80]}")
    for n in ch["drop"]:
        if n in a and n in b:
            why.append(f"pref {n} should be gone")
    for n in [name_of(l) for l in ch["add"]]:
        if n and n not in a:
            why.append(f"pref {n} is missing")
    if not collateral:
        return why
    touched = set(ch["set"]) | set(ch["drop"]) | {name_of(l) for l in ch["add"]}
    for n, idx in b.items():
        if n in touched:
            continue
        if n not in a:
            why.append(f"pref {n} vanished, but the patch does not touch it")
        elif [_norm(before[i]) for i in idx] != [_norm(after[i]) for i in a[n]]:
            why.append(f"pref {n} changed, but the patch does not touch it")
    for n in a:
        if n not in b and n not in touched:
            why.append(f"pref {n} appeared, but the patch does not add it")
    return why
