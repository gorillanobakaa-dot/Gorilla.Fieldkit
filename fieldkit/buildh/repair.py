"""Repair the two port failures of 2026-10-02 instead of only reporting them.

dangling excision   a block-removal hunk applied in part: the `// GORILLA excised: X` marker is there, but a tail
                    of the block it meant to remove is still below it, and the file no longer parses. Repair: from
                    the line after the marker, drop lines until the brace depth goes below where it started (that
                    line is the orphaned block's own closing brace), then prove it with `node --check`. Nothing is
                    guessed: the marker says what was meant, the braces say where the orphan ends, node says whether
                    it worked. If node still refuses, the file is restored and the step is left for a person.

moved member        `X.MEMBER` where X's module no longer defines MEMBER (upstream moved the table). Repair: among
                    the modules in the same component directory, exactly ONE must define MEMBER and export a single
                    object; give the file a lazy getter for it (if missing) and rewrite `X.MEMBER` to
                    `lazy.<That>.MEMBER`; prove with `node --check` and the member check. Two or zero candidates:
                    refused, reported.

Every repair is recorded as a hand step (`handedit.record`) so the verifier judges it like any hand port.
"""
import re
import shutil
from pathlib import Path

from . import firefox, handedit, symbols, task

MARKER = re.compile(r"GORILLA excised", re.I)
STRINGS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')
EXPORT_OBJ = re.compile(r"^export\s+(?:const|var|let)\s+(\w+)\s*=\s*(?:Object\.freeze\()?\{", re.M)
LAZY_BLOCK = re.compile(r"(ChromeUtils\.defineESModuleGetters\(\s*lazy\s*,\s*\{)(.*?)(\n\}\s*\))", re.S)


def prove(path):
    """`node --check` as the proof of a repair -> None only when node is installed AND the file parses. A missing
    node is a failed proof (firefox.node_check returns None for it, which here would have read as a pass)."""
    if not shutil.which("node"):
        return "node is not installed: the repair cannot be proven"
    return firefox.node_check(path)


def _depth_change(line):
    code = STRINGS.sub('""', line.split("//")[0])
    return code.count("{") - code.count("}")


def dangling_excision(workdir, rel, err_line):
    """-> (ok, what). Removes the orphaned tail below the nearest excision marker above `err_line`."""
    p = Path(workdir) / rel
    raw = p.read_bytes()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    lines = raw.decode("utf-8").split(nl.decode())
    hi = min(err_line, len(lines))
    marker = next((i for i in range(hi - 1, max(-1, hi - 80), -1) if MARKER.search(lines[i])), None)
    if marker is None:
        return False, f"no GORILLA excised marker within 80 lines above line {err_line}"
    depth, end = 0, None
    for i in range(marker + 1, len(lines)):
        depth += _depth_change(lines[i])
        if depth < 0:
            end = i
            break
    if end is None:
        return False, "no orphaned closing brace below the marker"
    removed = lines[marker + 1:end + 1]
    if len(removed) > 400:
        return False, f"{len(removed)} lines would go: too much to call an orphaned tail"
    new = lines[:marker + 1] + lines[end + 1:]
    p.write_bytes(nl.decode().join(new).encode("utf-8"))
    err = prove(p)
    if err:
        p.write_bytes(raw)
        return False, f"still does not parse after dropping {len(removed)} lines ({err[:80]}); restored"
    return True, f"dropped the {len(removed)}-line orphan below '{lines[marker].strip()[:60]}'"


def _uri_for(workdir, module_path):
    rel = Path(module_path).resolve().relative_to(Path(workdir).resolve()).as_posix()
    m = re.match(r"browser/components/([^/]+)/content/(.+)$", rel)
    if m:
        return f"chrome://browser/content/{m.group(1)}/{m.group(2)}"
    m = re.match(r"browser/modules/(.+)$", rel)
    if m:
        return f"resource:///modules/{m.group(1)}"
    return f"moz-src:///{rel}"


MEMBER_EXPR = re.compile(r"\b(?:lazy\.)?[A-Z]\w+\.[A-Z][A-Z0-9_]{2,}\b")


def upstream_renames(old_text, new_text):
    """{"X.MEMBER": "lazy.Y.OTHER"}: pairs of replaced lines in upstream's own diff of a file that differ ONLY in
    one member expression. Upstream renamed/moved the table; its diff says exactly how."""
    import difflib
    a, b = old_text.splitlines(), new_text.splitlines()
    out = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        # pair each removed line with the added line that is identical once the member expression is masked
        added = {MEMBER_EXPR.sub("@", lb): lb for lb in b[j1:j2] if len(MEMBER_EXPR.findall(lb)) == 1}
        for la in a[i1:i2]:
            ma = MEMBER_EXPR.findall(la)
            lb = added.get(MEMBER_EXPR.sub("@", la)) if len(ma) == 1 else None
            if lb is not None:
                mb = MEMBER_EXPR.findall(lb)
                if ma[0] != mb[0]:
                    out[ma[0].replace("lazy.", "", 1)] = mb[0]
    return out


def renamed_member(workdir, rel, ident, member, old_text, new_text):
    """-> (ok, what). Applies upstream's own rename of X.MEMBER (from its diff old->new of this file)."""
    key = f"{ident}.{member}"
    target = upstream_renames(old_text, new_text).get(key)
    if not target:
        return False, f"{key}: upstream's diff of {Path(rel).name} shows no rename for it"
    w = Path(workdir)
    p = w / rel
    raw = p.read_bytes()
    text = raw.decode("utf-8")
    tname = target.replace("lazy.", "", 1).split(".")[0]
    if target.startswith("lazy.") and not re.search(r"\b" + re.escape(tname) + r"\s*:\s*\"", text):
        m = re.search(r"\b" + re.escape(tname) + r"\s*:\s*\"[^\"]+\"", new_text)
        blk = LAZY_BLOCK.search(text)
        if not m or not blk:
            return False, f"{key}: {tname} needs a lazy getter and upstream's file does not show one"
        nl = "\r\n" if "\r\n" in text else "\n"
        text = text[:blk.end(2)] + f"{nl}  {m.group(0)}," + text[blk.end(2):]
    text, n = re.subn(r"\b(?:lazy\.)?" + re.escape(ident) + r"\." + re.escape(member) + r"\b", target, text)
    text = text.replace(f"lazy.lazy.", "lazy.")
    p.write_bytes(text.encode("utf-8"))
    err = prove(p)
    left = [x for x in symbols.missing_members(w, rel) if x.startswith(f"{key} ")]
    if err or left:
        p.write_bytes(raw)
        return False, f"{key}: rename refused by node/member check ({err or left}); restored"
    return True, f"{key} -> {target} ({n} place(s), upstream's own rename)"


def moved_member(workdir, rel, ident, member):
    """-> (ok, what). Rewrites X.MEMBER to lazy.<Owner>.MEMBER when exactly one module in the component defines it."""
    w = Path(workdir)
    p = w / rel
    comp = p.parent if p.parent.name != "content" else p.parent.parent
    cands = []
    for cand in sorted(comp.rglob("*.mjs")):
        if "/test" in cand.relative_to(w).as_posix() or cand == p:
            continue
        text = symbols.COMMENT.sub("", cand.read_text(encoding="utf-8", errors="replace"))
        exports = EXPORT_OBJ.findall(text)
        if len(exports) == 1 and symbols.defines(text, member):
            cands.append((cand, exports[0]))
    if len(cands) != 1:
        return False, f"{ident}.{member}: {len(cands)} module(s) in {comp.relative_to(w).as_posix()} define {member}: {[c[1] for c in cands]}"
    cand, name = cands[0]
    raw = p.read_bytes()
    nl = b"\r\n" if b"\r\n" in raw else b"\n"
    text = raw.decode("utf-8")
    if not re.search(r"\b" + re.escape(name) + r"\s*:\s*\"", text):
        uri = _uri_for(w, cand)
        m = LAZY_BLOCK.search(text)
        if not m:
            return False, f"{rel} has no ChromeUtils.defineESModuleGetters(lazy, {{...}}) block to add {name} to"
        text = text[:m.end(2)] + f'{nl.decode()}  {name}: "{uri}",' + text[m.end(2):]
    text, n = re.subn(r"\b(?:lazy\.)?" + re.escape(ident) + r"\." + re.escape(member) + r"\b", f"lazy.{name}.{member}", text)
    p.write_bytes(text.encode("utf-8"))
    err = prove(p)
    left = [x for x in symbols.missing_members(w, rel) if x.startswith(f"{ident}.{member} ")]
    if err or left:
        p.write_bytes(raw)
        return False, f"rewrite refused by node/member check ({err or left}); restored"
    return True, f"{ident}.{member} -> lazy.{name}.{member} ({n} place(s), getter from {cand.name})"


def run(task_id, say=print):
    """Repair what the two verify rows report, record each repair, re-check. -> {"ok", "repaired", "refused"}."""
    import subprocess
    t = task.load(task_id)
    w = Path(t["workdir"])
    root = subprocess.run(["git", "-C", str(w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    changed = subprocess.run(["git", "-C", str(w), "diff", "--name-only", root[0], "HEAD"], capture_output=True, text=True).stdout.split() if root else []
    repaired, refused = [], []
    for prob in firefox.syntax_problems(w, changed):
        rel, _, rest = prob.partition(": ")
        if "mozbuild refuses an empty" in rest:
            from .mozbuild_rules import fix_empty_assignments
            fixed = fix_empty_assignments(w / rel)
            what = f"empty mozbuild assignment(s) removed: {fixed}"
            say(f"  [{'repaired' if fixed else 'refused'}] {rel}: {what}")
            (repaired if fixed else refused).append(f"{rel}: {what}")
            if fixed:
                handedit.record(task_id, [rel], f"repair: {what} (mozbuild refuses an empty value)", kind="port")
            continue
        if "mozbuild refuses an unsorted" in rest:
            from .mozbuild_rules import fix_unsorted_lists
            fixed = fix_unsorted_lists(w / rel)
            what = f"mozbuild list(s) sorted: {fixed}"
            say(f"  [{'repaired' if fixed else 'refused'}] {rel}: {what}")
            (repaired if fixed else refused).append(f"{rel}: {what}")
            if fixed:
                handedit.record(task_id, [rel], f"repair: {what} (mozbuild refuses an unsorted list)", kind="port")
            continue
        m = re.search(r"line (\d+)", rest)
        if not m or not rel.endswith((".mjs", ".js")):
            refused.append(prob)
            continue
        ok, what = dangling_excision(w, rel, int(m.group(1)))
        say(f"  [{'repaired' if ok else 'refused'}] {rel}: {what}")
        (repaired if ok else refused).append(f"{rel}: {what}")
        if ok:
            handedit.record(task_id, [rel], f"repair: dangling excision - {what}", kind="port")
    def pristine(rel):
        """The upstream file this tree started from (root commit), or None."""
        b = subprocess.run(["git", "-C", str(w), "show", f"{root[0]}:{rel}"], capture_output=True) if root else None
        return b.stdout.decode("utf-8", "replace") if b is not None and b.returncode == 0 else None

    for prob in symbols.problems(w, changed):
        rel, _, rest = prob.partition(": ")
        m = re.match(r"(\w+)\.([A-Z][A-Z0-9_]+)", rest)
        if not m:
            refused.append(prob)
            continue
        # 1. upstream's own answer: the file as the port left it vs the pristine file, paired line by line
        #    (the owner's lines may predate even the previous release, so never diff two pristine versions)
        up = pristine(rel)
        ok, what = (False, "no pristine file to diff against")
        if up is not None:
            ok, what = renamed_member(w, rel, m.group(1), m.group(2), (w / rel).read_text(encoding="utf-8", errors="replace"), up)
        # 2. the one module in the component that defines the member
        if not ok:
            ok2, what2 = moved_member(w, rel, m.group(1), m.group(2))
            ok, what = (ok2, what2) if ok2 else (False, what + "; " + what2)
        say(f"  [{'repaired' if ok else 'refused'}] {rel}: {what}")
        (repaired if ok else refused).append(f"{rel}: {what}")
        if ok:
            handedit.record(task_id, [rel], f"repair: moved member - {what}", kind="port")
    t = task.load(task_id)
    task.journal(t, "repair", repaired=repaired[:20], refused=refused[:20])
    return {"ok": not refused, "repaired": repaired, "refused": refused}
