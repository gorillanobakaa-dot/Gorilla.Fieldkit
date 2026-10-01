"""Keyed text files ported by KEY: .properties (`key = value`, `\\` continuations), .dtd (`<!ENTITY key "value">`),
.ini (`key = value` under `[section]`). The owner's 155.0.1 build carries 38 such patches; they all applied with
`patch` in the 157 port, so this tier is for the drift of later versions, same shape as the Fluent and prefs tiers:
a value the owner set lands on the key wherever it now lives; a key upstream removed goes to the owner with
candidates; a key the owner adds is placed by the line-level merge.

  changes(hunk, file)   -> {"set": {key: new raw line(s)}, "drop": [keys], "add": [raw lines]}
  port(lines, hunk, file) -> (new_lines, notes, gone)
  check(before, after, hunk, file, collateral=True) -> problems
"""
import re

PROP = re.compile(r"^\s*([^#!=:\s][^=:]*?)\s*[=:]\s*(.*)$")
DTD = re.compile(r'^\s*<!ENTITY\s+([\w.\-]+)\s+(["\'])(.*)\2\s*>\s*$')
INI_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def kind(file):
    if file.endswith(".properties"):
        return "properties"
    if file.endswith(".dtd"):
        return "dtd"
    if file.endswith((".ini", ".inc")):
        return "ini"
    return None


def entries(lines, file):
    """-> [{key, start, end, value}] (end exclusive; .properties continuations with a trailing backslash are one entry)."""
    k = kind(file)
    out, i, section = [], 0, ""
    while i < len(lines):
        l = lines[i]
        if k == "ini":
            m = INI_SECTION.match(l)
            if m:
                section = m.group(1).strip() + "/"
                i += 1
                continue
        if k == "dtd":
            m = DTD.match(l)
            if m:
                out.append({"key": m.group(1), "start": i, "end": i + 1, "value": m.group(3)})
            i += 1
            continue
        m = PROP.match(l)
        if m and not l.lstrip().startswith(("#", "!", ";")):
            start, value = i, m.group(2).rstrip()
            while k == "properties" and value.endswith("\\") and i + 1 < len(lines):
                i += 1
                value = value[:-1] + lines[i].strip()
            out.append({"key": section + m.group(1).strip(), "start": start, "end": i + 1, "value": " ".join(value.split())})
        i += 1
    return out


def _by_key(ents):
    return {e["key"]: e for e in ents}


def changes(hunk, file):
    before = _by_key(entries([l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")], file))
    after = _by_key(entries([l[1:] for l in hunk["lines"] if l[:1] in (" ", "+")], file))
    plus_raw = [l[1:] for l in hunk["lines"] if l.startswith("+")]
    out = {"set": {}, "drop": [], "add": []}
    for key, e in after.items():
        if key in before:
            if e["value"] != before[key]["value"]:
                out["set"][key] = e
        else:
            out["add"].append(key)
    out["drop"] = [key for key in before if key not in after]
    out["_plus_raw"] = plus_raw
    return out


def candidates(key, keys):
    stem = re.sub(r"\d+$", "", key.rsplit("/", 1)[-1])
    return sorted(k for k in keys if k != key and (k.rsplit("/", 1)[-1].startswith(stem) or re.sub(r"\d+$", "", k.rsplit("/", 1)[-1]) == stem))[:5]


def _render(key, value, file, like):
    k = kind(file)
    name = key.rsplit("/", 1)[-1]
    if k == "dtd":
        q = '"' if '"' not in value else "'"
        return [f"<!ENTITY {name} {q}{value}{q}>"]
    sep = "=" if "=" in like or ":" not in like else ":"
    m = re.match(r"^(\s*)([^=:]*?)(\s*[=:]\s*)", like)
    return [f"{m.group(1)}{name}{m.group(3)}{value}" if m else f"{name}{sep}{value}"]


def port(lines, hunk, file):
    ch = changes(hunk, file)
    have = _by_key(entries(lines, file))
    new, notes, gone, edits = list(lines), [], [], []
    for key, e in ch["set"].items():
        if key not in have:
            gone.append((key, candidates(key, have)))
        else:
            h = have[key]
            edits.append((h["start"], h["end"], _render(key, e["value"], file, lines[h["start"]])))
    for key in ch["drop"]:
        if key in have:
            edits.append((have[key]["start"], have[key]["end"], []))
        else:
            notes.append(f"{key}: already gone from this Firefox, nothing to drop")
    if gone:
        return list(lines), notes, gone
    if ch["add"]:
        gone.append(("(new keys need a place: " + ", ".join(ch["add"][:3]) + ")", []))
        return list(lines), notes, gone
    for start, end, repl in sorted(edits, reverse=True):
        new[start:end] = repl
    if edits:
        notes.append(f"ported by key: {len(ch['set'])} set, {len([e for e in edits if not e[2]])} dropped")
    return new, notes, []


def check(before, after, hunk, file, collateral=True):
    ch = changes(hunk, file)
    b, a = _by_key(entries(before, file)), _by_key(entries(after, file))
    why = []
    for key, e in ch["set"].items():
        if key not in a:
            why.append(f"key {key} is missing")
        elif a[key]["value"] != e["value"]:
            why.append(f"key {key} does not read as the patch wants: {a[key]['value'][:80]}")
    for key in ch["drop"]:
        if key in a and key in b:
            why.append(f"key {key} should be gone")
    for key in ch["add"]:
        if key not in a:
            why.append(f"key {key} is missing")
    if not collateral:
        return why
    touched = set(ch["set"]) | set(ch["drop"]) | set(ch["add"])
    for key, e in b.items():
        if key in touched:
            continue
        if key not in a:
            why.append(f"key {key} vanished, but the patch does not touch it")
        elif a[key]["value"] != e["value"]:
            why.append(f"key {key} changed, but the patch does not touch it")
    for key in a:
        if key not in b and key not in touched:
            why.append(f"key {key} appeared, but the patch does not add it")
    return why
