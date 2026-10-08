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

The splitter above is line-based and forgiving on purpose (it ports); it cannot tell whether a file PARSES. Two
checks use Mozilla's own Fluent parser (the fluent.syntax package, a declared dependency) instead:

  parse_errors(text)    -> [(line, code, message)] for every entry the parser rejects (Junk). Firefox drops such an
                           entry, so its message shows as an empty label. 2026-10-01 a throwaway script (ftl.py)
                           compared Junk counts and message shapes of the 157 port's .ftl files with pristine by hand;
                           2026-10-08 the same check, made permanent, found a select expression written on one line
                           in the 157 tree's browser/locales/en-US/browser/ipProtection.ftl (E0003; the message is lost).
  shape(text)           -> {id: (has value, (attribute names))}; shape_diff(pristine, now) -> removed ids, and ids whose
                           value or attributes were lost (code that reads `.label` of such a message gets nothing)
  orphans(workdir, pristine) -> removed ids the tree's code still names (an element left with no text: 2026-10-08,
                           four in Settings); duplicates(text) -> ids defined twice in one file
"""
import re

ENTRY = re.compile(r"^(-?[A-Za-z][\w-]*)\s*=\s*(.*)$")
ATTR = re.compile(r"^\.([\w-]+)\s*=\s*(.*)$")


class Ambiguous(ValueError):
    pass


class ParserMissing(RuntimeError):
    """fluent.syntax is not installed: nothing was parsed (reported as a tool gap, never as a pass)."""


def _parse(text):
    try:
        from fluent.syntax import FluentParser
    except ImportError as e:
        raise ParserMissing("fluent.syntax is not installed (pip install fluent.syntax)") from e
    return FluentParser(with_spans=True).parse(text)


def parse_errors(text):
    """-> [(line, code, message)] one per Junk entry: where the parser gave up (the annotation's line)."""
    from fluent.syntax import ast
    out = []
    for e in _parse(text).body:
        if isinstance(e, ast.Junk):
            at = e.annotations[0] if e.annotations else None
            pos = at.span.start if at and at.span else e.span.start
            out.append((text.count("\n", 0, pos) + 1, at.code if at else "E0000",
                        at.message if at else "unparsed text"))
    return out


def shape(text):
    """-> {id: (has_value, (attribute names, sorted))}; terms are named with their leading '-'."""
    from fluent.syntax import ast
    out = {}
    for e in _parse(text).body:
        if isinstance(e, (ast.Message, ast.Term)):
            name = ("-" if isinstance(e, ast.Term) else "") + e.id.name
            out[name] = (e.value is not None, tuple(sorted(a.id.name for a in e.attributes)))
    return out


def shape_diff(pristine_text, now_text):
    """-> {"removed": [ids], "lost": [(id, what it lost)]}: ids of the pristine file that are gone, and ids still
    there that lost their value or an attribute. Added ids, values and attributes are not reported."""
    was, now = shape(pristine_text), shape(now_text)
    lost = []
    for k, (val, attrs) in was.items():
        if k not in now:
            continue
        gone = ([] if not val or now[k][0] else ["value"]) + [f".{a}" for a in attrs if a not in now[k][1]]
        if gone:
            lost.append((k, gone))
    return {"removed": sorted(k for k in was if k not in now), "lost": sorted(lost)}


def orphans(workdir, pristine, files=None):
    """Messages the fork REMOVED that the tree's code still names -> [(ftl rel, id, [files naming it])].

    2026-10-08: the 08.Look patch rebuilt preferences.ftl from the 155 file and dropped four messages 157 added
    (settings-keyboard-shortcuts-group, ...); Settings drew a heading, a link and two buttons with no text, and Fluent
    rejected the page's translate promises with no reason (14 "uncaught exception: undefined"). shape_diff listed
    them among the fork's deliberate removals; a removed id that code still names is not deliberate.
    `files`: the .ftl files to look at (default: every .ftl that differs from `pristine`)."""
    import subprocess
    from pathlib import Path
    w = str(workdir)
    git = lambda *a: subprocess.run(["git", "-C", w, *a], capture_output=True)
    if files is None:
        files = [l for l in git("diff", "--name-only", pristine, "--", "*.ftl").stdout.decode().splitlines() if l]
    removed = {}
    for rel in files:
        up = git("show", f"{pristine}:{rel}")
        now = Path(w) / rel
        if up.returncode != 0 or not now.is_file():
            continue
        try:
            sd = shape_diff(up.stdout.decode("utf-8", "replace"), now.read_text(encoding="utf-8", errors="replace"))
        except ParserMissing:
            raise
        for i in sd["removed"]:
            removed.setdefault(i.lstrip("-"), []).append(rel)
    if not removed:
        return []
    # one git grep for all ids, then exact matches (an id is bounded by anything but a word character or '-')
    args = ["grep", "-n", "-F", "-I"] + [x for i in removed for x in ("-e", i)] + ["--", ".", ":!*.ftl", ":!**/test/**", ":!**/tests/**"]
    hits = {}
    out = git(*args).stdout.decode("utf-8", "replace")
    for line in out.splitlines():
        path = line.split(":", 1)[0]
        for i in removed:
            if i in line and re.search(r"(?<![\w-])" + re.escape(i) + r"(?![\w-])", line):
                hits.setdefault(i, set()).add(path)
    return sorted((rel, i, sorted(hits[i])[:5]) for i, rels in removed.items() if i in hits for rel in rels)


def duplicates(text):
    """-> [ids defined more than once in one file] (Firefox keeps the first and logs "Attempt to override")."""
    import collections
    from fluent.syntax import ast
    n = collections.Counter(("-" if isinstance(e, ast.Term) else "") + e.id.name
                            for e in _parse(text).body if isinstance(e, (ast.Message, ast.Term)))
    return sorted(k for k, c in n.items() if c > 1)


def drop_second_copies(lines):
    """-> (new lines, [(id, kept line, removed line)]): every id defined twice keeps its FIRST copy (the one Firefox
    uses) and loses the others, each with its own comment block (never a "##" heading or a GORILLA comment); a
    GORILLA REPAIR graft comment left with no entry under it goes too. The copies must be identical (else ValueError):
    nothing visible changes. Born 2026-10-08 (seven doubled ids in five 157 files, from 2026-07 grafts)."""
    ents = entries(lines)
    by = {}
    for e in ents:
        by.setdefault(e["id"], []).append(e)
    drop, report = set(), []
    for k, copies in by.items():
        if len(copies) < 2:
            continue
        if len({tuple(e["parts"]) for e in copies}) != 1:      # every part, by name AND normalised text
            raise ValueError(f"{k}: the copies differ; choose by hand")
        for e in copies[1:]:
            start, top = e["start"], e["start"]
            while top > 0 and lines[top - 1].startswith("#"):
                top -= 1
            if lines[top:start] and not any(l.startswith(("##", "# GORILLA")) for l in lines[top:start]):
                start = top
            end = e["end"]
            if end < len(lines) and not lines[end].strip() and start > 0 and not lines[start - 1].strip():
                end += 1
            drop.update(range(start, end))
            report.append((k, copies[0]["start"] + 1, e["start"] + 1))
    out = [l for n, l in enumerate(lines) if n not in drop]
    res, i = [], 0
    while i < len(out):
        if out[i].startswith("# GORILLA REPAIR"):
            j = i
            while j < len(out) and out[j].startswith("#"):
                j += 1
            k = j
            while k < len(out) and not out[k].strip():
                k += 1
            if k >= len(out) or out[k].startswith("#"):
                if k >= len(out):
                    while res and not res[-1].strip():
                        res.pop()
                i = k
                continue
        res.append(out[i])
        i += 1
    return res, report


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


def _texts(parts):
    """The texts of a message with `.label` and the plain value treated as one slot (upstream folds them)."""
    return {("value" if n == ".label" else n): t for n, t in parts}


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
    value = next((t for n, t in parts if n == "value"), None)
    out = [f"{entry_id} = {value}" if value is not None else f"{entry_id} ="]       # Fluent's own form: value on the id line
    for name, text in parts:
        if name != "value":
            out.append(f"{indent}{name} = {text}")
    return out


def candidates(gone_id, ids):
    """Upstream renames usually keep the stem: open-in-tab -> open-in-tab2."""
    stem = re.sub(r"\d+$", "", gone_id)
    return sorted(i for i in ids if i != gone_id and (i.startswith(stem) or re.sub(r"\d+$", "", i) == stem))


def intent(hunk):
    """semantics() with drift resolved: -> (sem, notes). A removed id and an added id that are the same message under
    two names (dont-show2 vs dont-show) are not a change the owner made: the owner's tree carries an OLDER name.
    Same text -> nothing to port; different text -> the owner's wording belongs on the name the file has (never add
    the old name back)."""
    sem = semantics(hunk)
    notes = []
    for r in list(sem["removed"]):
        twin = next((a_id for a_id in sem["added"] if r in candidates(a_id, [r]) or a_id in candidates(r, [a_id])), None)
        if twin is None:
            continue
        before_r = _by_id(entries([l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")]))[r]["parts"]
        sem["removed"].remove(r)
        own = sem["added"].pop(twin)
        if _texts(own) == _texts(before_r):                 # `.label =` and a plain value are the same text
            notes.append(f"{twin}: an older name of {r} in the owner's tree, same text: nothing to port")
        else:
            sem["changed"][r] = own
            sem.setdefault("twins", {})[r] = twin
            notes.append(f"{twin}: the owner's wording goes onto {r}, the name this Firefox uses")
    return sem, notes


def hunk_neighbours(hunk, mid):
    """The ids of the messages before and after `mid` in the hunk's OLD text (context + removed lines)."""
    ids = [m.group(1) for l in hunk["lines"] if l[:1] in (" ", "-") for m in [ENTRY.match(l[1:])] if m]
    if mid not in ids:
        return None, None
    k = ids.index(mid)
    return (ids[k - 1] if k else None), (ids[k + 1] if k + 1 < len(ids) else None)


def in_place(ents, k, hunk):
    """Does the k-th entry sit where the hunk has it (same neighbouring message ids, where the hunk names them)?"""
    prev, nxt = hunk_neighbours(hunk, ents[k]["id"])
    p = ents[k - 1]["id"] if k else None
    n = ents[k + 1]["id"] if k + 1 < len(ents) else None
    return (prev is None or prev == p) and (nxt is None or nxt == n)


def copy_to_remove(ents, mid, hunk):
    """Index of the copy of `mid` the hunk removes: the one in the hunk's place; the only copy when there is one
    and the hunk names no neighbour that exists. None when no copy sits there (already removed, or the owner MOVED
    the message and only its new copy is left: contextual-identity.ftl h2, live run 16, where a second pass took
    the moved copy as well)."""
    copies = [k for k, e in enumerate(ents) if e["id"] == mid]
    if not copies:
        return None
    placed = [k for k in copies if in_place(ents, k, hunk)]
    if placed:
        return placed[0]
    prev, nxt = hunk_neighbours(hunk, mid)
    ids = {e["id"] for e in ents}
    if len(copies) == 1 and prev not in ids and nxt not in ids:
        return copies[0]                                  # the hunk's neighbours are gone too: no better evidence
    return None


def port(lines, hunk):
    """-> (new_lines, notes, gone) ; gone = [(id, [candidate ids])]. With gone ids nothing is written: the owner
    decides whether the change belongs to the renamed message."""
    sem, notes = intent(hunk)
    if sem["reformat_only"]:
        return list(lines), ["reformat only: the hunk changes whitespace, not a single message"], []
    ents = entries(lines)
    have = _by_id(ents)
    if not sem["changed"] and not sem["added"] and not sem["removed"]:
        return list(lines), notes or ["nothing to port"], []
    # a message the hunk REMOVES that is already absent is done, not gone; a message the hunk ADDS that is already
    # there with the same parts is done too, and with other parts it is a change (live run 16, preferences.ftl h52:
    # `containers-remove-button3` was already gone and `containers-remove-button2` already added, and the port
    # was deferred as "no longer exists" for a removal it did not need to make)
    for r, twin in list((sem.get("twins") or {}).items()):
        if r not in have and twin in have and _texts(have[twin]["parts"]) == _texts(sem["changed"][r]):
            notes.append(f"already transferred: {twin} carries the owner's wording and {r} is gone")
            del sem["changed"][r]
    remove_at = {i: copy_to_remove(ents, i, hunk) for i in sem["removed"]}
    already_removed = [i for i in sem["removed"] if remove_at[i] is None]
    if already_removed:
        notes.append(f"already removed (no copy where the hunk has it): {', '.join(already_removed)}")
        sem["removed"] = [i for i in sem["removed"] if remove_at[i] is not None]
    for i in list(sem["added"]):
        if i in have:
            if have[i]["parts"] == sem["added"][i]:
                notes.append(f"already added: {i}")
            else:
                sem["changed"][i] = sem["added"][i]
            del sem["added"][i]
    gone = [(i, candidates(i, have)) for i in list(sem["changed"]) + sem["removed"] if i not in have]
    if gone:
        return list(lines), notes, gone
    if not sem["changed"] and not sem["added"] and not sem["removed"]:
        return list(lines), notes + ["nothing left to port"], []
    default_indent = next((e["indent"] for e in ents if e["indent"]), "    ")
    new = list(lines)
    # rewrite from the bottom so earlier offsets stay valid
    work = [(have[i]["start"], have[i]["end"], i, sem["changed"][i]) for i in sem["changed"]]
    work += [(ents[remove_at[i]]["start"], ents[remove_at[i]]["end"], i, None) for i in sem["removed"]]
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
    notes.append(f"ported by message id: {len(sem['changed'])} changed, {len(sem['added'])} added, {len(sem['removed'])} removed")
    return new, notes, []


# -- transferring the owner's wording onto a message upstream renamed or restructured -------------------------

def _edit(old, new):
    """The owner's change to one text as (X, Y): old = P+X+S, new = P+Y+S with the longest common ends. None when
    nothing changed."""
    if old == new:
        return None
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    s = 0
    while s < min(len(old), len(new)) - p and old[-1 - s] == new[-1 - s]:
        s += 1
    return old[p:len(old) - s], new[p:len(new) - s]


def _owner_parts(hunk, gone_id):
    before = _by_id(entries([l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")]))
    after = _by_id(entries([l[1:] for l in hunk["lines"] if l[:1] in (" ", "+")]))
    return before.get(gone_id, {}).get("parts"), after.get(gone_id, {}).get("parts")


def transfer(lines, hunk, gone):
    """Carry the owner's edits onto renamed messages, strictly. -> (new_lines, mapping {old_id: (new_id, parts)},
    notes, still_gone). Rules: exactly one candidate; for each text the owner changed, the candidate must still
    hold the old text (then it gets the owner's text) or contain the owner's replaced fragment exactly once (then
    the same replacement is made); anything else stays with the owner. A message whose text the owner did NOT
    change (the owner's tree merely carries an older id) is drift, not rebranding: nothing to port."""
    ents = entries(lines)
    have = _by_id(ents)
    new, mapping, notes, still = list(lines), {}, [], []
    edits = []
    for gone_id, cands in gone:
        old_parts, own_parts = _owner_parts(hunk, gone_id)
        if old_parts is None or own_parts is None:
            still.append((gone_id, cands)); continue
        if old_parts == own_parts:
            notes.append(f"{gone_id}: the owner changed no text here (an older id of the same message): nothing to port")
            continue
        if len(cands) != 1:
            still.append((gone_id, cands)); continue
        cand = have[cands[0]]
        cparts = dict(cand["parts"])
        old_d, own_d = dict(old_parts), dict(own_parts)
        # upstream may have folded `.label` into the value or the other way round
        def slot(name):
            if name in cparts:
                return name
            if name == ".label" and "value" in cparts:
                return "value"
            if name == "value" and ".label" in cparts:
                return ".label"
            return None
        result, ok = dict(cparts), True
        for name, own_text in own_parts:
            old_text = old_d.get(name)
            if old_text is None or old_text == own_text:
                continue
            target = slot(name)
            if target is None:
                ok = False; break
            cur = cparts[target]
            if cur == old_text:
                result[target] = own_text
            elif cur == own_text:                           # already transferred (an earlier run, or upstream agreed)
                result[target] = own_text
            elif cur.count(old_text) == 1:                  # upstream wrapped the same text: "... (beta)"
                result[target] = cur.replace(old_text, own_text)
            else:
                ok = False; break
        if not ok:
            still.append((gone_id, cands)); continue
        parts = [(n, result[n]) for n, _ in cand["parts"]]
        edits.append((cand["start"], cand["end"], cands[0], parts, cand["indent"]))
        mapping[gone_id] = (cands[0], parts)
        notes.append(f"{gone_id}: the owner's wording transferred to the renamed message {cands[0]}")
    default_indent = next((e["indent"] for e in ents if e["indent"]), "    ")
    for start, end, i, parts, indent in sorted(edits, reverse=True):
        new[start:end] = _render(i, parts, indent or default_indent)
    return new, mapping, notes, still


def check(before, after, hunk, mapping=None, collateral=True):
    """-> problems. Intended ids carry the intended content; every other message is unchanged (unless
    `collateral` is off: the final re-check, where later hunks legitimately touched the same file). `mapping`
    (from transfer) says which intended ids live under a new name, with the content they must have there."""
    sem, _ = intent(hunk)
    mapping = mapping or {}
    b, a = _by_id(entries(before)), _by_id(entries(after))
    why = []
    intended_now = {}
    for i, parts in {**sem["changed"], **sem["added"]}.items():
        if i in mapping:
            j, p = mapping[i]
            intended_now[j] = p
        elif i in b or i in sem["added"]:
            intended_now[i] = parts
    intended_now = {i: [tuple(p) for p in parts] for i, parts in intended_now.items()}   # JSON round-trips give lists
    twins = sem.get("twins") or {}
    for i, parts in intended_now.items():
        if i not in a and i in twins and twins[i] in a and _texts(a[twins[i]]["parts"]) == _texts(parts):
            continue                                        # the owner's name carries the owner's wording: transferred
        if i not in a:
            why.append(f"message {i} is missing")
        elif a[i]["parts"] != parts:
            why.append(f"message {i} does not read as the patch wants: {a[i]['parts']}")
    import collections
    cb, ca = collections.Counter(e["id"] for e in entries(before)), collections.Counter(e["id"] for e in entries(after))
    for i in sem["removed"]:
        if i in b and ca[i] >= cb[i]:
            why.append(f"message {i} should be gone" + (f" (one of its {cb[i]} copies)" if cb[i] > 1 else ""))
    if not collateral:
        return why
    intended = set(sem["changed"]) | set(sem["added"]) | set(sem["removed"]) | set(intended_now)
    for i in b:
        if i not in intended and (i not in a or a[i]["parts"] != b[i]["parts"]):
            why.append(f"message {i} changed, but the patch does not touch it")
    for i in a:
        if i not in b and i not in intended:
            why.append(f"message {i} appeared, but the patch does not add it")
    return why


# -- a message defined more times than the owner's truth has it ------------------------------------------------

def _neighbours(ents, k):
    prev = ents[k - 1]["id"] if k > 0 else None
    nxt = ents[k + 1]["id"] if k + 1 < len(ents) else None
    return prev, nxt


def dedupe(lines, truth_lines):
    """Remove the copies of a message that exceed the owner's truth, keeping the copies that sit where the truth
    has them (same neighbouring messages), else the last ones. -> (new_lines, [(id, removed_at_line)]).

    Live run 16 (2026-10-01, browser.ftl): the owner MOVES `urlbar-result-menu-trending-dont-show2` down the file.
    The group apply added the new copy; the hunk removing the old one was closed as a drift twin, so the tree held
    the message twice where the owner's tree holds it once. Firefox keeps the last copy and the l10n lint fails."""
    import collections
    ents, truth = entries(lines), entries(truth_lines)
    want = collections.Counter(e["id"] for e in truth)
    truth_nb = collections.defaultdict(list)
    for k, e in enumerate(truth):
        truth_nb[e["id"]].append(_neighbours(truth, k))
    have = collections.Counter(e["id"] for e in ents)
    drop = []
    for i, n in have.items():
        if n <= want[i] or n <= 1:
            # surplus means a message defined TWICE in the tree. A message the truth lacks because 157 renamed it
            # (`...-open-in-tab2`, carrying the owner's transferred wording) or added it is not surplus: the first
            # cut removed eleven such messages across two files (live run 16, 22:39) - repaired from the checkpoint
            continue
        copies = [k for k, e in enumerate(ents) if e["id"] == i]
        placed = [k for k in copies if _neighbours(ents, k) in truth_nb[i]]
        keep = placed[:want[i]] if len(placed) >= want[i] else (placed + [k for k in reversed(copies) if k not in placed])[:want[i]]
        drop += [k for k in copies if k not in keep]
    removed = []
    new = list(lines)
    for k in sorted(drop, reverse=True):
        e = ents[k]
        removed.append((e["id"], e["start"] + 1))
        del new[e["start"]:e["end"]]
    return new, sorted(removed, key=lambda r: r[1])


def restore_lost(lines, truth_lines, pristine_lines):
    """Put back a message the owner's truth AND the pristine upstream file both have, that the tree has fewer copies
    of than the truth, at the truth's position (after the same previous message, else before the same next one,
    else at the end). A message only the truth has is a port's job, not this one's; a message upstream dropped is
    not restored. -> (new_lines, [(id, inserted_at_line)])."""
    import collections
    ents, truth, pristine = entries(lines), entries(truth_lines), entries(pristine_lines)
    have = collections.Counter(e["id"] for e in ents)
    want = collections.Counter(e["id"] for e in truth)
    up = collections.Counter(e["id"] for e in pristine)
    new, inserted = list(lines), []
    for k, e in enumerate(truth):
        i = e["id"]
        if have[i] >= want[i] or up[i] == 0:
            continue
        block = truth_lines[e["start"]:e["end"]]
        cur = entries(new)
        prev = truth[k - 1]["id"] if k else None
        nxt = truth[k + 1]["id"] if k + 1 < len(truth) else None
        at = next((x["end"] for x in cur if x["id"] == prev), None) if prev else None
        while at is not None and at < len(new) and not new[at].strip():
            at += 1                                       # after the blank lines that follow the previous message
        if at is None:
            at = next((x["start"] for x in cur if x["id"] == nxt), None) if nxt else None
        if at is None:
            at = len(new)
        new[at:at] = block
        inserted.append((i, at + 1))
        have[i] += 1
    return new, inserted


def step_dedupe(t, file, truth_root, **kw):
    """Harness step: remove the surplus copies in `file` against the owner's truth copy of it."""
    from pathlib import Path as _P
    p = _P(t["workdir"]) / file
    tp = _P(truth_root) / file
    if not p.is_file() or not tp.is_file():
        return {"ok": False, "why": [f"{file} or its truth copy does not exist"]}
    raw = p.read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    truth = tp.read_text(encoding="utf-8", errors="replace").splitlines()
    new, removed = dedupe(raw.split(nl), truth)
    inserted = []
    if kw.get("pristine_lines") is not None:
        new, inserted = restore_lost(new, truth, kw["pristine_lines"])
    else:
        import subprocess
        r = subprocess.run(["git", "-C", t["workdir"], "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True)
        root = r.stdout.split()
        if root:
            pr = subprocess.run(["git", "-C", t["workdir"], "show", f"{root[0]}:{file}"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
            if pr.returncode == 0:
                new, inserted = restore_lost(new, truth, pr.stdout.splitlines())
    if removed or inserted:
        p.write_text(nl.join(new), encoding="utf-8", newline="")
    what = [f"removed surplus {i} at line {at}" for i, at in removed] + [f"restored {i} at line {at}" for i, at in inserted]
    return {"ok": True, "summary": f"{file}: " + ("; ".join(what) if what else "nothing to reconcile")}


def check_dedupe(t, file, truth_root, **kw):
    import collections
    from pathlib import Path as _P
    now = collections.Counter(e["id"] for e in entries((_P(t["workdir"]) / file).read_text(encoding="utf-8").splitlines()))
    want = collections.Counter(e["id"] for e in entries((_P(truth_root) / file).read_text(encoding="utf-8", errors="replace").splitlines()))
    over = [i for i, n in now.items() if n > 1 and n > want[i]]
    why = [f"{file}: still defined more than in the truth: {over[:3]}"] if over else []
    pristine = kw.get("pristine_lines")
    if pristine is None:
        import subprocess
        r = subprocess.run(["git", "-C", t["workdir"], "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True)
        pr = subprocess.run(["git", "-C", t["workdir"], "show", f"{r.stdout.split()[0]}:{file}"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace") if r.stdout.split() else None
        pristine = pr.stdout.splitlines() if pr is not None and pr.returncode == 0 else []
    up = collections.Counter(e["id"] for e in entries(pristine))
    lost = [i for i, n in want.items() if now[i] < n and up[i]]
    if lost:
        why.append(f"{file}: fewer copies than the owner's tree: {lost[:3]}")
    return {"ok": not why, "why": why}
