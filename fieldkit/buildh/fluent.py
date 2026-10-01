"""Fluent (.ftl) hunks ported by MESSAGE, not by line.

Live run 11 (2026-10-01, browser.ftl h30): the owner's build re-indented the file (2 spaces -> 4), so the hunk
was 60 lines of which 5 were real wording changes ("Open in New Gorilla Tab"), and upstream 157 had renamed
those very message ids (open-in-tab -> open-in-tab2). Line matching cannot see any of that; a model given line
operations deleted the wrong lines three times. Fluent files have a grammar, so:

  entries(lines)        -> ordered entries {id, start, end, parts} where parts = [(name, normalised text)];
                           name is "value" or ".attr"; whitespace is collapsed, indentation ignored
  semantics(hunk)       -> what the hunk MEANS: changed/added/removed ids with their new content, or
                           "reformat only" when nothing but whitespace differs; Ambiguous when the hunk cuts
                           an entry whose id line is outside it
  port(lines, hunk)     -> new lines: each changed id rewritten in the file's own style; ids that no longer
                           exist are returned as 'gone' with rename candidates (the owner decides)
  check(before, after, hunk) -> problems: every intended id has the intended content, nothing else changed
"""
import re

ENTRY = re.compile(r"^(-?[A-Za-z][\w-]*)\s*=\s*(.*)$")
ATTR = re.compile(r"^\.([\w-]+)\s*=\s*(.*)$")


class Ambiguous(ValueError):
    pass


def _norm(text):
    return " ".join(text.split())


def entries(lines):
    """-> list of {id, start, end (exclusive), parts: [(name, text)], indent}. Comments and blanks are not entries."""
    out, cur = [], None

    def close(i):
        nonlocal cur
        if cur:
            cur["end"] = i
            cur["parts"] = [(n, _norm(" ".join(v))) for n, v in cur["raw"]]
            del cur["raw"]
            out.append(cur)
            cur = None
    for i, line in enumerate(lines):
        if line[:1] in (" ", "\t") and line.strip():
            if cur is None:
                continue                                    # an orphan continuation: belongs to an entry before us
            s = line.strip()
            m = ATTR.match(s)
            if m:
                cur["raw"].append(["." + m.group(1), [m.group(2)]])
                if cur["indent"] is None:
                    cur["indent"] = line[:len(line) - len(line.lstrip())]
            else:
                cur["raw"][-1][1].append(s)
            continue
        close(i)
        m = ENTRY.match(line)
        if m:
            cur = {"id": m.group(1), "start": i, "raw": [["value", [m.group(2)]]], "indent": None}
    close(len(lines))
    for e in out:
        e["parts"] = [(n, t) for n, t in e["parts"] if not (n == "value" and t == "")]
    return out


def _by_id(ents):
    return {e["id"]: e for e in ents}


def semantics(hunk):
    """-> {"reformat_only": bool, "changed": {id: parts}, "added": {id: parts}, "removed": [ids]}"""
    hl = hunk["lines"]
    first_entry = next((i for i, l in enumerate(hl) if ENTRY.match(l[1:])), None)
    if first_entry is None:
        raise Ambiguous("no message in the hunk")
    if any(l[:1] in ("+", "-") and l[1:].strip() for l in hl[:first_entry]):
        raise Ambiguous("the hunk changes lines of a message whose id is outside the hunk")
    before = _by_id(entries([l[1:] for l in hl if l[:1] in (" ", "-")]))
    after = _by_id(entries([l[1:] for l in hl if l[:1] in (" ", "+")]))
    changed = {i: after[i]["parts"] for i in after if i in before and after[i]["parts"] != before[i]["parts"]}
    added = {i: after[i]["parts"] for i in after if i not in before}
    removed = [i for i in before if i not in after]
    return {"reformat_only": not changed and not added and not removed, "changed": changed, "added": added, "removed": removed}


def _render(entry_id, parts, indent):
    if len(parts) == 1 and parts[0][0] == "value":
        return [f"{entry_id} = {parts[0][1]}"]
    out = [f"{entry_id} ="]
    for name, text in parts:
        out.append(f"{indent}{name} = {text}" if name != "value" else f"{indent}{text}")
    return out


def candidates(gone_id, ids):
    """Upstream renames usually keep the stem: open-in-tab -> open-in-tab2."""
    stem = re.sub(r"\d+$", "", gone_id)
    return sorted(i for i in ids if i != gone_id and (i.startswith(stem) or re.sub(r"\d+$", "", i) == stem))


def port(lines, hunk):
    """-> (new_lines, notes, gone) ; gone = [(id, [candidate ids])]. With gone ids nothing is written: the owner
    decides whether the change belongs to the renamed message."""
    sem = semantics(hunk)
    if sem["reformat_only"]:
        return list(lines), ["reformat only: the hunk changes whitespace, not a single message"], []
    ents = entries(lines)
    have = _by_id(ents)
    gone = [(i, candidates(i, have)) for i in list(sem["changed"]) + sem["removed"] if i not in have]
    if gone:
        return list(lines), [], gone
    default_indent = next((e["indent"] for e in ents if e["indent"]), "    ")
    new = list(lines)
    # rewrite from the bottom so earlier offsets stay valid
    work = [(have[i]["start"], have[i]["end"], i, sem["changed"][i]) for i in sem["changed"]]
    work += [(have[i]["start"], have[i]["end"], i, None) for i in sem["removed"]]
    for start, end, i, parts in sorted(work, reverse=True):
        new[start:end] = _render(i, parts, have[i]["indent"] or default_indent) if parts is not None else []
    if sem["added"]:
        # new messages go where the hunk puts them: after the last entry of the hunk that exists in the file
        hunk_ids = [m.group(1) for l in hunk["lines"] for m in [ENTRY.match(l[1:])] if m and l[:1] in (" ", "+")]
        prev = None
        for i in hunk_ids:
            if i in sem["added"]:
                break
            if i in have:
                prev = i
        at = have[prev]["end"] if prev else 0
        block = []
        for i, parts in sem["added"].items():
            block += _render(i, parts, default_indent)
        new[at:at] = block
    notes = [f"ported by message id: {len(sem['changed'])} changed, {len(sem['added'])} added, {len(sem['removed'])} removed"]
    return new, notes, []


def check(before, after, hunk):
    """-> problems. Intended ids carry the intended content; every other message is unchanged."""
    sem = semantics(hunk)
    b, a = _by_id(entries(before)), _by_id(entries(after))
    why = []
    for i, parts in {**sem["changed"], **sem["added"]}.items():
        if i not in a:
            why.append(f"message {i} is missing")
        elif a[i]["parts"] != parts:
            why.append(f"message {i} does not read as the patch wants: {a[i]['parts']}")
    for i in sem["removed"]:
        if i in a:
            why.append(f"message {i} should be gone")
    intended = set(sem["changed"]) | set(sem["added"]) | set(sem["removed"])
    for i in b:
        if i not in intended and (i not in a or a[i]["parts"] != b[i]["parts"]):
            why.append(f"message {i} changed, but the patch does not touch it")
    for i in a:
        if i not in b and i not in intended:
            why.append(f"message {i} appeared, but the patch does not add it")
    return why
