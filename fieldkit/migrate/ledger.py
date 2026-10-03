"""The intent ledger: what every hunk of the patch set is FOR, and how the built browser proves it.

The maintainer's requirement (2026-10-03): "we applied over 2000 code changes, over 1000 patches ... diff line by line
... not possible anymore because the code has changed so much ... WE IMPLEMENT THE IDEA, THE CONCEPT." A line diff
stops being proof once Firefox moves code; an intent does not. So every hunk cluster of the public patch set is
registered in <owner>/intents/INTENTS.yaml:

    id              stable: I-<10 hex> of group / patch / file / the cluster's anchor (its prefs, functions and
                    markers; its changed lines when it has none), so a re-cut hunk keeps its id
    group, patch    the patch-set group and the patch file (relative to the patch-set root)
    hunks           [{file, n, at}]: the hunks of ONE patch in ONE file that serve one purpose. A cluster is a single
                    hunk, or consecutive hunks of a file under the same enclosing function; a multi-file patch with a
                    header (21.PORT.FIXES, 22.EGRESS.LOCKDOWN) is one purpose per file
    purpose         {quote, source}: QUOTED, never written by this code: a GORILLA comment the hunk adds, else the
                    patch header, a README line naming the patch, a linked decision's title, a claim in the hunk,
                    the group's reason in the patch policy; nothing -> quote UNKNOWN
    anchors         what survives a refactor: prefs, functions, file basenames, hosts, symbols, string ids, markers
    behaviour_check decision-register check kinds (fieldkit/buildh/decisions.py), chosen by rule:
                      a pref line in all.js / firefox.js        -> pref {name, value, locked} on the INSTALLED build
                        (dropped, with the reason, when a later patch sets the same pref to another value, or when
                        the line sits under #if: the shipped file is preprocessed)
                      a StaticPrefList.yaml entry               -> tree_contains of the entry as the patch writes it
                      a file the patch deletes                  -> tree_absent
                      a file the patch adds                     -> tree_present (+ installed_present under distribution/)
                      an early return in a function             -> tree_contains of the return line
                      a GORILLA marker / any specific added line -> tree_contains of it (strings: the message line)
                      only removals                             -> tree_lacks of the most specific removed line
                      NEW_FILES of a group                      -> tree_present (+ installed_present under distribution/)
                      DELETED_FILES manifest                    -> tree_absent + proof_row excised (post-install row)
                    otherwise UNCHECKABLE with the reason
    decisions       register ids linked by a shared pref or path, or named in the patch header
    claims          CLAIMS.yaml ids whose sentence sits inside the hunk, or whose pref evidence the hunk sets
    in_scope        false for a disabled group or an excluded patch (the reason is kept)
    status          {<release>: <status>}: written per migration by `migrate seed TASK` (status.py)

Generated and reviewable: `migrate seed` regenerates the generated fields and keeps everything else an entry holds
(a maintainer's `review:` note, `checks_manual:`). Append-only for ids: an intent whose hunk left the patch set stays,
marked `retired:` with the date; an id is never reused or removed.
"""
import hashlib
import json
import re
import time
from pathlib import Path

import yaml

from ..buildh import claims as cl
from ..buildh import decisions as dec
from ..buildh import firefox, proof
from ..buildh import verify as vf

LEDGER_REL = Path("intents") / "INTENTS.yaml"
# MIGRATION-PLAN.md section 4: privacy core first, then build fixes, then the look and the snapshot delta
PLAN_ORDER = ("13.", "12.", "09.", "07.", "05.", "22.", "02.", "03.", "04.", "06.", "11.", "21.", "08.", "16.")
PREF_FILES = tuple(src for src, _, _ in proof.PREF_SOURCES)
STATIC_PREFS = "modules/libpref/init/StaticPrefList.yaml"
SHIPPED_AS_FILES = ("distribution/",)          # tree paths the installer copies into the install folder as they are
BUILD_KINDS = ("pref", "installed_absent", "installed_present", "omni_absent", "proof_row", "leakgate_policy", "decision")
TREE_KINDS = ("tree_contains", "tree_lacks", "tree_absent", "tree_present", "image_sharp", "mozconfig_has")

HUNK_HEAD = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")
FUNC = (re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("),
        re.compile(r"^\s*(?:async\s+|static\s+|get\s+|set\s+)*#?([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{"),
        re.compile(r"\b([A-Z]\w*::~?[A-Za-z_]\w*)\s*\("),
        re.compile(r"^\s*(?:pub(?:\(crate\))?\s+)?(?:async\s+)?fn\s+(\w+)"),
        re.compile(r"^\s*def\s+(\w+)"))
NOT_FUNC = {"if", "for", "while", "switch", "catch", "return", "function", "else", "do", "try", "with", "sizeof", "new"}
HOST = re.compile(r"\b(?:https?|wss?)://([a-z0-9][a-z0-9.-]*\.[a-z]{2,})", re.I)
SYMBOL = re.compile(r"#\s*define\s+(\w+)|\b([A-Z][A-Z0-9]*_[A-Z0-9_]{2,})\b")
FTL_ID = re.compile(r"^\s*(-?[a-zA-Z][\w-]*)\s*=")
L10N_ATTR = re.compile(r"data-l10n-id=\"([\w-]+)\"")
PROP_KEY = re.compile(r"^\s*([\w.-]+)\s*=")
STATIC_NAME = re.compile(r"^-\s+name:\s*(\S+)")
GETPREF = re.compile(r"(?:[gs]et\w*Pref|Preferences::\w+|prefs\.\w+)\(\s*[\"']([a-z][\w-]*(?:\.[\w-]+)+)[\"']")
MARKER = re.compile(r"gorilla", re.I)
RETURN = re.compile(r"^\s*return\b")
COMMENT = re.compile(r"^\s*(//|/\*|\*|#(?!\s*(if|ifdef|ifndef|else|elif|endif|define|include|undef)\b)|<!--|--\s)")
SYMBOL_SKIP = {"TRUE", "FALSE", "NULL", "NS_OK", "NS_ERROR_FAILURE", "MOZ_ASSERT", "NS_IMETHODIMP"}


# -- reading patches ------------------------------------------------------------------------------------------------
def parse(text):
    """A unified diff -> [{file, key, mode, hunks: [{n, header, context, lines, first, last}]}].

    Hunk bodies are read by the counts in the @@ header, so a removed line that starts with '-- ' and the next file's
    '--- a/...' header are never confused. `key` is the name firefox.parse_patch gives the file (the claims audit's
    key: '/dev/null' for a deleted file); `file` is the real path. `first`/`last` are 1-based lines in the patch."""
    lines = text.splitlines()
    files, cur, i = [], None, 0

    def strip(p):
        return p[2:] if p.startswith(("a/", "b/")) else p
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old = line[4:].split("\t")[0].strip()
            new = lines[i + 1][4:].split("\t")[0].strip()
            mode = "deleted" if new == "/dev/null" else "new" if old == "/dev/null" else "change"
            cur = {"file": strip(old) if mode == "deleted" else strip(new), "key": strip(new), "mode": mode, "hunks": []}
            files.append(cur)
            i += 2
            continue
        m = HUNK_HEAD.match(line)
        if m and cur is not None:
            a = 1 if m.group(2) is None else int(m.group(2))
            b = 1 if m.group(4) is None else int(m.group(4))
            body, j = [], i + 1
            while j < len(lines) and (a > 0 or b > 0):
                x = lines[j]
                t = x[:1]
                if x.startswith("\\"):
                    pass
                elif t == " " or x == "":
                    a, b = a - 1, b - 1
                    x = x or " "
                elif t == "-":
                    a -= 1
                elif t == "+":
                    b -= 1
                else:
                    break
                body.append(x)
                j += 1
            while j < len(lines) and lines[j].startswith("\\"):
                body.append(lines[j])
                j += 1
            cur["hunks"].append({"n": len(cur["hunks"]) + 1, "header": line, "context": m.group(5).strip(), "lines": body,
                                 "first": i + 1, "last": j})
            i = j
            continue
        i += 1
    return files


def _sides(h):
    rem = [l[1:] for l in h["lines"] if l.startswith("-")]
    add = [l[1:] for l in h["lines"] if l.startswith("+")]
    return rem, add


def _funcs(text):
    out = []
    for rx in FUNC:
        for m in rx.finditer(text):
            name = m.group(1)
            if name and name not in NOT_FUNC and len(name) > 2:
                out.append(name)
    return out


def anchors_of(path, hunks):
    """Concept anchors of a cluster: names that survive a refactor (a moved function keeps its name, a pref its key)."""
    prefs, funcs, hosts, syms, strings, markers = [], [], [], [], [], []
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    for h in hunks:
        funcs += _funcs(h["context"])
        for l in h["lines"]:
            if l[:1] not in ("+", "-"):
                continue
            body = l[1:]
            m = proof.PREF.match(body)
            if m:
                prefs.append(m.group(1))
            m = STATIC_NAME.match(body.strip()) if path.endswith(STATIC_PREFS) else None
            if m:
                prefs.append(m.group(1))
            prefs += GETPREF.findall(body)
            funcs += _funcs(body)
            hosts += [x.lower() for x in HOST.findall(body)]
            for a, b in SYMBOL.findall(body):
                s = a or b
                if s and s not in SYMBOL_SKIP:
                    syms.append(s)
            if ext == "ftl":
                m = FTL_ID.match(body)
                if m:
                    strings.append(m.group(1))
            elif ext == "properties":
                m = PROP_KEY.match(body)
                if m:
                    strings.append(m.group(1))
            strings += L10N_ATTR.findall(body)
            if l[:1] == "+" and MARKER.search(body):
                markers.append(body.strip()[:200])
        if path.endswith(STATIC_PREFS):
            blk = _static_block(h)                        # the entry the change sits in names the pref
            m = STATIC_NAME.match(blk.strip()) if blk else None
            if m:
                prefs.append(m.group(1))

    def uniq(xs, n=25):
        seen, out = set(), []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out[:n]
    a = {"file": path.rsplit("/", 1)[-1]}
    for k, v in (("prefs", prefs), ("functions", funcs), ("hosts", hosts), ("symbols", syms), ("strings", strings),
                 ("markers", markers)):
        v = uniq(v, 12 if k == "markers" else 25)
        if v:
            a[k] = v
    return a


def _clusters(pfile, multi_file_purpose):
    """Hunks of one patch file -> [[hunk, ...]]: consecutive hunks under the same enclosing function form one cluster;
    for a multi-file purpose patch the whole file is one cluster."""
    hs = pfile["hunks"]
    if multi_file_purpose or pfile["mode"] in ("new", "deleted"):
        return [hs] if hs else []
    out = []
    for h in hs:
        f = _funcs(h["context"])
        if out and f and _funcs(out[-1][-1]["context"])[:1] == f[:1]:
            out[-1].append(h)
        else:
            out.append([h])
    return out


def _typed(v):
    v = proof._norm(v)
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _pref_sets(hunks):
    """-> [(name, value, locked, conditional)] for every pref line the cluster ADDS (in order)."""
    out = []
    for h in hunks:
        depth = 0
        for l in h["lines"]:
            if l.startswith("-"):
                continue
            body = l[1:]
            if proof.PRE_IF.match(body):
                depth += 1
            elif proof.PRE_END.match(body):
                depth = max(0, depth - 1)
            if l.startswith("+"):
                m = proof.PREF.match(body)
                if m:
                    out.append((m.group(1), _typed(m.group(2)), m.group(3) == "locked", depth > 0))
    return out


def _static_block(h):
    """The StaticPrefList entry a hunk adds or changes, as the patch writes it (post-image lines from '- name:')."""
    post = [(l[:1], l[1:]) for l in h["lines"] if l[:1] in (" ", "+")]
    for k, (t, body) in enumerate(post):
        if t != "+":
            continue
        start = next((j for j in range(k, -1, -1) if STATIC_NAME.match(post[j][1].strip())), None)
        if start is None:
            continue
        end = k
        while end + 1 < len(post) and post[end + 1][0] == "+" and not STATIC_NAME.match(post[end + 1][1].strip()):
            end += 1
        return "\n".join(b for _, b in post[start:end + 1])
    return None


def _window(line, removed, width=120):
    """A long line (a vendored checksum file is ONE line) is checked by a window around where it differs from the
    line it replaces, so the check stays readable and still tells the patched line from the old one."""
    if len(line) <= 200:
        return line
    best = 0
    for r in removed:
        r = r.strip()
        n = 0
        while n < min(len(r), len(line)) and r[n] == line[n]:
            n += 1
        best = max(best, n)
    start = max(0, best - 40)
    return line[start:start + width]


def _text_check(path, h):
    """-> (check, label) or (None, reason) for an ordinary code or string hunk."""
    rem, add = _sides(h)
    rem_s = {x.strip() for x in rem}
    cand = [x.strip() for x in add if len(x.strip()) >= vf.SHORT and not firefox.TRIVIAL.match(x.strip())
            and x.strip() not in rem_s]
    if cand:
        ret = [x for x in cand if RETURN.match(x)]
        mark = [x for x in cand if MARKER.search(x)]
        code = [x for x in cand if not COMMENT.match(x)]
        if ret:
            return {"tree_contains": {"path": path, "text": _window(max(ret, key=len), rem)}}, "early return"
        if mark:
            return {"tree_contains": {"path": path, "text": _window(max(mark, key=len), rem)}}, "GORILLA marker"
        pick = max(code or cand, key=len)
        label = "string" if path.endswith((".ftl", ".properties", ".dtd")) else "added line"
        return {"tree_contains": {"path": path, "text": _window(pick, rem)}}, label
    add_s = {x.strip() for x in add}
    gone = [x.strip() for x in rem if len(x.strip()) >= vf.SPECIFIC and x.strip() not in add_s
            and not firefox.TRIVIAL.match(x.strip()) and not any(x.strip() in a for a in add_s)]
    if gone:
        return {"tree_lacks": {"path": path, "text": _window(max(gone, key=len), add)}}, "removed line"
    if sorted(x.strip() for x in rem if x.strip()) == sorted(x.strip() for x in add if x.strip()):
        return None, "whitespace or blank lines only: nothing behaves differently, so there is nothing to prove"
    return None, "only short or punctuation lines change: no line is specific enough to prove the hunk on its own"


def checks_for(path, mode, hunks):
    """-> (checks, notes, pref_sets). pref checks are completed later (supersession needs the whole patch set)."""
    checks, notes = [], []
    if mode == "deleted":
        return [{"tree_absent": [path]}], ["the patch deletes this file"], []
    if mode == "new":
        c = [{"tree_present": [path]}]
        if path.startswith(SHIPPED_AS_FILES):
            c.append({"installed_present": [path]})
        return c, ["the patch adds this file"], []
    sets = _pref_sets(hunks) if path in PREF_FILES else []
    if path.endswith(STATIC_PREFS):
        for h in hunks:
            blk = _static_block(h)
            if blk:
                checks.append({"tree_contains": {"path": path, "text": blk}})
        if checks:
            notes.append("static prefs are compiled into xul.dll (no shipped text to read): checked in the tree")
    for h in hunks:
        if sets and any(proof.PREF.match(l[1:]) for l in h["lines"] if l.startswith("+")):
            continue                                  # the pref check speaks for this hunk
        if path.endswith(STATIC_PREFS) and _static_block(h):
            continue
        c, label = _text_check(path, h)
        if c and c not in checks:
            checks.append(c)
            notes.append(f"hunk #{h['n']}: {label}")
        elif not c:
            notes.append(f"hunk #{h['n']}: {label}")
    return checks, notes, sets


# -- quoting a purpose ----------------------------------------------------------------------------------------------
def _hunk_purpose(hunks, file):
    for h in hunks:
        for l in h["lines"]:
            if l.startswith("+") and MARKER.search(l) and cl._is_comment(l[1:], file):
                q = re.sub(r"^\s*(//+|/\*+|\*+|#+|<!--|--)\s*", "", l[1:]).strip().rstrip("*/").strip()
                if len(q) >= 12:
                    return q[:300]
    return None


def _id(*parts):
    return "I-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10]


def plan_rank(group, policy_index=0):
    for i, p in enumerate(PLAN_ORDER):
        if group.startswith(p):
            return (i, policy_index)
    return (len(PLAN_ORDER), policy_index)


# -- generating the ledger ------------------------------------------------------------------------------------------
def _decision_index(owner):
    try:
        reg = dec.load(owner)
    except (FileNotFoundError, OSError, ValueError):
        return []
    out = []
    for e in reg["entries"]:
        if e.get("status") == "retired":
            continue
        prefs, paths = set(), set()
        for c in e.get("verify") or []:
            kind, arg = next(iter(c.items()))
            if kind == "pref":
                prefs.add(arg.get("name"))
            elif kind in ("tree_contains", "tree_lacks"):
                paths.add(arg.get("path"))
            elif kind in ("tree_absent", "tree_present"):
                paths.update(arg)
        out.append({"id": e.get("id"), "title": e.get("title"), "prefs": prefs, "paths": paths})
    return out


def _claims_index(owner, pset_rel):
    """-> (by patch source: [(line, id, text)], by pref: [ids])."""
    p = Path(owner) / cl.REGISTER
    if not p.is_file():
        return {}, {}
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(p.read_text(encoding="utf-8"), Loader=loader) or {}
    by_src, by_pref = {}, {}
    for c in data.get("claims") or []:
        src = str(c.get("source") or "")
        if src.startswith(pset_rel + "/") and src.endswith(".patch"):
            by_src.setdefault(src[len(pset_rel) + 1:], []).append((c.get("line") or 0, c.get("id"), c.get("text") or ""))
        for e in c.get("evidence") or []:
            if "pref" in e and isinstance(e["pref"], dict):
                by_pref.setdefault(e["pref"].get("name"), []).append(c.get("id"))
    return by_src, by_pref


def _all_groups(owner):
    """Every group of the policy (enabled or not), in policy order: the ledger carries the whole patch set."""
    pset, groups = firefox._policy(owner)
    return pset, list(groups.items())


def generate(owner):
    """-> {"intents": [...], "stats": {...}} from the public patch set of the owner repository. Read-only."""
    owner = Path(owner)
    pset, groups = _all_groups(owner)
    pset_rel = Path(pset).relative_to(owner).as_posix() if Path(pset).is_relative_to(owner) else str(pset)
    sc = cl.scope_of(owner)
    decs = _decision_index(owner)
    by_src, by_pref = _claims_index(owner, pset_rel)
    intents, stats = [], {"patch_files": 0, "hunks": 0, "added_lines": 0, "removed_lines": 0, "pref_lines": 0,
                          "pref_lines_added": 0, "new_files": 0, "deleted_files": 0}
    pref_order = []                                    # (intent index, name, value, locked, file) in application order
    for gi, (g, spec) in enumerate(groups):
        enabled = spec.get("status") == "enabled"
        excl = set(spec.get("exclude", []))
        gdir = Path(pset) / g
        for pf in sorted(gdir.rglob("*.patch")) if gdir.is_dir() else []:
            rel = pf.relative_to(pset).as_posix()
            text = sc["texts"].get(rel) or pf.read_text(encoding="utf-8", errors="replace")
            stats["patch_files"] += 1
            scope_note = None if enabled and pf.name not in excl else (
                f"group {g} is {spec.get('status')}: {cl.norm(spec.get('reason', ''))[:160]}" if not enabled else
                f"excluded by the patch policy: {cl.norm(spec.get('exclude_reason', ''))[:160]}")
            header_purpose, header_src = cl._purpose(owner, sc, rel, text) if sc else (None, None)
            dec_refs = sorted(set(cl.DECISION_REF.findall("\n".join(l for l in text.splitlines()[:12] if l.startswith("#")))))
            files = parse(text)
            multi = len(files) > 1 and header_src == "patch header"
            claims_here = by_src.get(rel, [])
            ordinal = {}
            for f in files:
                for h in f["hunks"]:
                    stats["hunks"] += 1
                    r, a = _sides(h)
                    stats["added_lines"] += len(a)
                    stats["removed_lines"] += len(r)
                    stats["pref_lines"] += sum(1 for l in h["lines"] if l[:1] in "+-" and proof.PREF.match(l[1:]))
                    stats["pref_lines_added"] += sum(1 for l in h["lines"] if l[:1] == "+" and proof.PREF.match(l[1:]))
                for cluster in _clusters(f, multi):
                    path = f["file"]
                    anchors = anchors_of(path, cluster)
                    key = sorted(anchors.get("prefs", []) + anchors.get("functions", [])[:3] + anchors.get("markers", [])[:2])
                    if not key:
                        key = [x for h in cluster for x in h["lines"] if x[:1] in "+-" and x[1:].strip()]
                    base = _id(g, rel, path, *key)
                    n = ordinal.get(base, 0)
                    ordinal[base] = n + 1
                    iid = base if n == 0 else _id(g, rel, path, *key, str(n))
                    checks, notes, sets = checks_for(path, f["mode"], cluster)
                    lo, hi = min(h["first"] for h in cluster), max(h["last"] for h in cluster)
                    cids = [c for line, c, _ in claims_here if lo <= line <= hi]
                    prefs = anchors.get("prefs", [])
                    for p in prefs:
                        cids += [c for c in by_pref.get(p, []) if c not in cids][:5]
                    hp = _hunk_purpose(cluster, path)
                    links = [d["id"] for d in decs if (set(prefs) & d["prefs"]) or path in d["paths"] or d["id"] in dec_refs]
                    in_hunk = next((t for line, c, t in claims_here if lo <= line <= hi and len(t) > 20), None)
                    if hp:
                        purpose = {"quote": hp, "source": f"{pset_rel}/{rel}:{lo} (GORILLA comment the hunk adds)"}
                    elif header_purpose and header_src == "patch header":
                        purpose = {"quote": header_purpose, "source": f"{pset_rel}/{rel}:1 (patch header)"}
                    elif header_purpose and header_src not in ("group (patch policy)",):
                        purpose = {"quote": header_purpose, "source": str(header_src)}
                    elif links:
                        d = next(x for x in decs if x["id"] == links[0])
                        purpose = {"quote": str(d["title"]), "source": f"decisions/PRODUCT-DECISIONS.yaml {d['id']}"}
                    elif in_hunk:
                        purpose = {"quote": in_hunk[:300], "source": f"claims/CLAIMS.yaml (a claim inside the hunk)"}
                    elif header_purpose:
                        purpose = {"quote": header_purpose, "source": "config/patch_policy.json (the group's reason)"}
                    else:
                        purpose = {"quote": "UNKNOWN", "source": None}
                    e = {"id": iid, "group": g, "patch": rel, "kind": "hunks",
                         "hunks": [{"file": path, "n": h["n"], "at": h["header"][:160], "key": f["key"]} for h in cluster],
                         "purpose": purpose, "anchors": anchors, "behaviour_check": checks, "check_notes": notes,
                         "decisions": links, "claims": cids[:12]}
                    if len(cids) > 12:
                        e["claims_more"] = len(cids) - 12
                    if scope_note:
                        e["in_scope"], e["scope_note"] = False, scope_note
                    intents.append(e)
                    for name, val, locked, cond in sets:
                        pref_order.append((len(intents) - 1, name, val, locked, cond, path))
        # whole files the group adds or removes (not hunks, but changes the patch set carries)
        nf = firefox.new_files(pset, g) if (gdir / "NEW_FILES").is_dir() else []
        stats["new_files"] += len(nf)
        by_dir = {}
        for _, rel in nf:
            by_dir.setdefault("/".join(rel.split("/")[:3]) if rel.count("/") >= 3 else rel.rsplit("/", 1)[0] if "/" in rel else ".", []).append(rel)
        for d, paths in sorted(by_dir.items()):
            c = [{"tree_present": sorted(paths)}]
            shipped = [p for p in paths if p.startswith(SHIPPED_AS_FILES)]
            if shipped:
                c.append({"installed_present": sorted(shipped)})
            readme = _readme_line(gdir, paths)
            e = {"id": _id(g, "NEW_FILES", d), "group": g, "patch": f"{g}/NEW_FILES", "kind": "new-files",
                 "files": len(paths), "purpose": readme or _group_reason(spec), "anchors": {"file": d, "files": sorted(p.rsplit("/", 1)[-1] for p in paths)[:25]},
                 "behaviour_check": c, "check_notes": [f"{len(paths)} new file(s) under {d}"], "decisions": [], "claims": []}
            if not enabled:
                e["in_scope"], e["scope_note"] = False, f"group {g} is {spec.get('status')}"
            intents.append(e)
        man = gdir / "DELETED_FILES.manifest.txt"
        dels = [l.strip() for l in man.read_text(encoding="utf-8").splitlines() if l.strip()] if man.is_file() else []
        stats["deleted_files"] += len(dels)
        by_dir = {}
        for rel in dels:
            by_dir.setdefault("/".join(rel.split("/")[:3]), []).append(rel)
        for d, paths in sorted(by_dir.items()):
            e = {"id": _id(g, "DELETED_FILES", d), "group": g, "patch": f"{g}/DELETED_FILES.manifest.txt", "kind": "deleted-files",
                 "files": len(paths), "purpose": _readme_line(gdir, paths) or _group_reason(spec),
                 "anchors": {"file": d, "files": sorted(p.rsplit("/", 1)[-1] for p in paths)[:25]},
                 "behaviour_check": [{"tree_absent": sorted(paths)}, {"proof_row": "excised"}],
                 "check_notes": [f"{len(paths)} upstream file(s) under {d} the group deletes; the build layer is the "
                                 "post-install 'excised' row (packaged omni.ja paths differ from source paths)"],
                 "decisions": [], "claims": []}
            if not enabled:
                e["in_scope"], e["scope_note"] = False, f"group {g} is {spec.get('status')}"
            intents.append(e)
    _supersede(intents, pref_order)
    for e in intents:
        if not e["behaviour_check"]:
            e["behaviour_check"] = "UNCHECKABLE"
            e["uncheckable_why"] = "; ".join(e.get("check_notes") or []) or "no rule applies"
    return {"intents": intents, "stats": stats, "pset_rel": pset_rel}


def _readme_line(gdir, paths):
    for doc in sorted(Path(gdir).glob("README*")):
        for ln in doc.read_text(encoding="utf-8", errors="replace").splitlines():
            for p in paths[:50]:
                name = p.rsplit("/", 1)[-1]
                if len(name) > 6 and name in ln and len(ln.strip()) > len(name) + 10:
                    return {"quote": cl.norm(ln)[:300], "source": f"{doc.name} of the group"}
    return None


def _group_reason(spec):
    r = cl.norm(spec.get("reason", ""))
    return {"quote": r[:300], "source": "config/patch_policy.json (the group's reason)"} if r else {"quote": "UNKNOWN", "source": None}


def _supersede(intents, pref_order):
    """A pref the patch set sets more than once: only the value that wins at runtime is a check (firefox.js loads after
    all.js; within a file the last line wins). Earlier sets carry a note naming the intent that supersedes them."""
    final = {}
    for k, (idx, name, val, locked, cond, path) in enumerate(pref_order):
        rank = (1 if path.endswith("firefox.js") else 0)
        if name not in final or rank >= final[name][0]:
            final[name] = (rank, k, idx, val, locked)
    for k, (idx, name, val, locked, cond, path) in enumerate(pref_order):
        e = intents[idx]
        if e.get("in_scope") is False:
            continue
        if cond:
            e["check_notes"].append(f"pref {name}: under #if in the source; the shipped file is preprocessed, not judged")
            continue
        rank, wk, widx, fval, flocked = final[name]
        if wk != k and (fval, flocked) != (val, locked):
            w = intents[widx]
            where = "later in the same hunk cluster" if widx == idx else f"by {w['id']} ({w['patch']})"
            e["check_notes"].append(f"pref {name} = {val!r}: superseded {where}: {fval!r}{' locked' if flocked else ''}")
            if widx != idx:
                e.setdefault("superseded_by", []).append(w["id"])
            continue
        c = {"pref": {"name": name, "value": val, "locked": bool(locked)}}
        if c not in e["behaviour_check"]:
            e["behaviour_check"].append(c)


# -- the file -------------------------------------------------------------------------------------------------------
HEADER = """# Gorilla Firefox: the intent ledger (generated by `fieldkit build-harness migrate seed TASK`; reviewable).
#
# One intent per hunk cluster of the public patch set: WHY it exists (quoted, never invented), the ANCHORS that
# survive a Firefox refactor, and the BEHAVIOUR CHECK that proves it in the built browser (decision-register kinds).
# A migration is done when every in-scope intent is proven in the new build, not when the patches apply.
#
# Rules: ids are stable and never reused or removed (an intent that left the patch set is marked `retired:`).
# Generated fields are refreshed on every seed; anything else an entry holds (review:, checks_manual:) is kept.
# status: {release: status} per migration (OUT-OF-SCOPE, OPEN, NOT-IN-TREE, VERIFIED-IN-TREE, PROVEN-IN-BUILD,
# BUILD-CONTRADICTED, EXPLAINED). See fieldkit/migrate/ledger.py and MIGRATION-PLAN.md.
"""
GENERATED = ("group", "patch", "kind", "hunks", "files", "purpose", "anchors", "behaviour_check", "check_notes",
             "uncheckable_why", "decisions", "claims", "claims_more", "in_scope", "scope_note", "superseded_by")


def path_for(owner):
    return Path(owner) / LEDGER_REL


def load(owner):
    p = path_for(owner)
    if not p.is_file():
        return {"exists": False, "intents": [], "path": str(p)}
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(p.read_text(encoding="utf-8"), Loader=loader) or {}
    return {"exists": True, "intents": data.get("intents") or [], "stats": data.get("stats") or {}, "path": str(p),
            "generated": data.get("generated")}


def merge(old, fresh, today=None):
    """Old ledger entries + a fresh generation -> entries. Append-only for ids; maintainer fields kept."""
    today = today or time.strftime("%Y-%m-%d")
    by_id = {e["id"]: e for e in fresh}
    out, seen = [], set()
    for e in old:
        i = e.get("id")
        if i in seen:
            continue
        seen.add(i)
        if i in by_id:
            keep = {k: v for k, v in e.items() if k not in GENERATED}
            n = dict(by_id[i])
            n.update({k: v for k, v in keep.items() if k != "retired"})
            out.append(n)
        else:
            r = dict(e)
            r.setdefault("retired", f"{today}: no longer in the patch set")
            out.append(r)
    for e in fresh:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


def save(owner, intents, stats):
    p = path_for(owner)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"version": 1, "generated": time.strftime("%Y-%m-%d"), "stats": stats, "intents": intents},
                          sort_keys=False, allow_unicode=True, width=4096, default_flow_style=None)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n" + body)
    return p


def checks_of(e):
    """The checks an intent is judged by: the maintainer's `checks_manual` when the entry has them (a reviewed
    replacement for a generated check that proved too weak or too strict), else the generated behaviour_check."""
    c = e.get("checks_manual") if isinstance(e.get("checks_manual"), list) and e.get("checks_manual") else e.get("behaviour_check")
    return [] if not isinstance(c, list) else c


def layer(check):
    kind = next(iter(check))
    return "build" if kind in BUILD_KINDS else "tree"


def coverage(intents):
    """-> counts over the in-scope, not retired intents."""
    live = [e for e in intents if e.get("in_scope", True) and not e.get("retired")]
    with_check = [e for e in live if checks_of(e)]
    with_build = [e for e in live if any(layer(c) == "build" for c in checks_of(e))]
    unknown = [e for e in live if (e.get("purpose") or {}).get("quote") == "UNKNOWN"]
    src = {}
    for e in live:
        s = str((e.get("purpose") or {}).get("source") or "UNKNOWN")
        key = ("GORILLA comment" if "GORILLA comment" in s else "patch header" if "patch header" in s else
               "decision" if s.startswith("decisions/") else "claim" if s.startswith("claims/") else
               "group reason" if "group's reason" in s else "README" if "README" in s or s.endswith(".md") else
               "UNKNOWN" if s == "UNKNOWN" else s)
        src[key] = src.get(key, 0) + 1
    pct = lambda a, b: round(100.0 * a / b, 1) if b else 0.0
    return {"intents": len(intents), "in_scope": len(live), "with_check": len(with_check),
            "with_check_pct": pct(len(with_check), len(live)), "with_build_check": len(with_build),
            "with_build_check_pct": pct(len(with_build), len(live)), "purpose_unknown": len(unknown),
            "purpose_sources": dict(sorted(src.items(), key=lambda kv: -kv[1])),
            "uncheckable": len(live) - len(with_check),
            "retired": sum(1 for e in intents if e.get("retired")),
            "out_of_scope": sum(1 for e in intents if e.get("in_scope") is False)}


def fingerprint(intents):
    return hashlib.sha256(json.dumps([e.get("id") for e in intents]).encode()).hexdigest()[:16]
