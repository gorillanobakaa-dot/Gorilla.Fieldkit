"""The claims audit: what the public patch set SAYS the browser does, held against what the tree and the installed
build PROVE. Deterministic and fail closed.

Why (2026-10-03): people clone the public repository, read what we claim and check the patches against it. A claim
nobody checks is a promise we cannot keep; a patch the README describes but the tree does not carry is worse. So:

    fieldkit build-harness claims TASK [--report PATH] [--strict] [--install-dir DIR]

1. PATCH IMPLEMENTATION AUDIT. Every patch of every ENABLED group (policy order, config/patch_policy.json of the
   owner repo), every hunk scored against the ported tree with verify.score_hunk (the same judge the build gate uses),
   then refined from the task record:

     APPLIED                 the hunk's effect is in the tree
     ALREADY-UPSTREAM        in the tree, and already in pristine upstream (nothing for the fork to carry)
     RELOCATED               upstream moved the code; the recorded relocation step's change holds at the new home
     HAND-PORTED             a person ported it (recorded hand step) and the meaning check holds
     OBSOLETE                the code is gone upstream (neither old nor new lines exist); explained only when the
                             task record closed the step as obsolete too (two sources agree)
     DROPPED-BY-MAINTAINER   the policy excludes the patch (with its exclude_reason) or the owner dropped the step
     SUPERSEDED              a LATER patch of the set removes what this hunk adds (or restores what it removes), and
                             that is exactly what the tree lacks: explained by the patch set itself
     NOT-APPLIED             the tree does not carry it (or carries only part of it)
     UNJUDGEABLE             nothing in the hunk can be judged by text, and the whole block is not found either way

   Per patch: implemented % (APPLIED + ALREADY-UPSTREAM + RELOCATED + HAND-PORTED over the hunks not explained away)
   and a verdict IMPLEMENTED / PARTIAL / MISSING / OBSOLETE-EXPLAINED / DROPPED-BY-MAINTAINER. A PARTIAL or MISSING
   patch without a recorded maintainer decision (claims/CLAIMS.yaml patch_decisions, naming a decision of the
   register) FAILS. Every NEW_FILES entry: IDENTICAL, CHANGED-BY-HAND-STEP, DIFFERS or MISSING; every path of a
   DELETED_FILES manifest must be gone.

2. CLAIMS REGISTER, <owner>/claims/CLAIMS.yaml. Seeded by EXTRACTING every claim sentence from the public sources
   (the README and notes the public repository tracks, the owner's README and docs/PRIVACY-AND-HARDENING.md, the
   '#' headers of in-scope patches and their GORILLA-marked added comments). Each claim: id, exact text, sha256 of
   the text, source file and line, evidence. Evidence kinds: the decision register's (decisions.KINDS: pref,
   tree_contains, tree_lacks, omni_absent, installed_absent, mozconfig_has, image_sharp, fieldkit_test) plus
       patch: GROUP/FILE.patch        the patch must be IMPLEMENTED in the tree
       patch_group: GROUP             every in-scope patch of the group IMPLEMENTED or explained
       leakgate_policy: NAME          PASS in the latest RELEASE leak-gate run, and that run is the installed build
       proof_row: NAME                passed in the post-install proof since the latest install into this folder
       decision: D-157-xx             ENFORCED in the decision register
   Links are seeded only where the text itself names them (a claim inside a patch -> that patch; a pref line ->
   its pref check; a named file, group, build flag, decision or named technique -> what it names; "no telemetry /
   phone home" -> NETWORK_POLICY + TELEMETRY_POLICY + D-157-00) and marked `seeded: auto` for the maintainer to
   review. Nothing is invented: a claim nothing names stays without evidence and is UNPROVEN.
   The register is appended to, never rewritten: existing entries are the maintainer's.

3. VERDICTS per claim: PROVEN (every check ran and passed), UNPROVEN (no evidence, or a check could not run: no
   install, no release leak-gate result for this build), CONTRADICTED (a check ran and failed), STALE (the source no
   longer says the registered text, or the text no longer matches its sha256). A check that cannot run is never
   a pass.

4. REPORT (default <owner>/claims/AUDIT-<major>.md): public-safe markdown, paths relative to the owner repository,
   the tree's commits and the installed BuildID, generated date. fieldkit.core.privacy scans it before it is
   written; any finding and nothing is written.

5. --strict (the regression baseline and a release): OK only when every claim is PROVEN and every enabled patch is
   IMPLEMENTED or explained.

Read-only against the tree and the install. It writes only the register (append) and the report.
"""
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

from . import decisions as dec
from . import firefox, proof
from . import verify as vf

REGISTER = Path("claims") / "CLAIMS.yaml"
HUNK_OK = ("APPLIED", "ALREADY-UPSTREAM", "RELOCATED", "HAND-PORTED")
CLAIM_KINDS = dec.KINDS + ("patch", "patch_group", "leakgate_policy", "proof_row", "decision")
VERDICTS = ("PROVEN", "UNPROVEN", "CONTRADICTED", "STALE")
PATCH_EXPLAINED = ("IMPLEMENTED", "OBSOLETE-EXPLAINED", "DROPPED-BY-MAINTAINER")
WIN_MOZCONFIG = "config/mozconfig.win64"
PRIMARY_DOCS = ("gorilla-patchset/README.md", "docs/PRIVACY-AND-HARDENING.md", "gorilla-patchset/WHAT-WE-CHANGED-AND-WHY.md",
                "gorilla-patchset/THE-SEALED-APPLIANCE.md", "gorilla-patchset/windows/README.md",
                "gorilla-patchset/windows/THE-SEALED-APPLIANCE.md", "README.md")
GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}       # never let a read refresh the tree's index

# -- what counts as a claim -----------------------------------------------------------------------------------------
TECHNIQUE = re.compile(r"mozambique|physical lock|lobotom\w*|annihilat\w*|sealed appliance|starvation|hard-lock|kill-sweep|"
                       r"headshot|telemetry kill|egress lockdown", re.I)
ASSERT = re.compile(
    r"\b(remov(e|es|ed|al|ing)|disabl(e|es|ed|ing)|block(s|ed|ing)?|lock(s|ed)?|cut(s)?|never|strip(s|ped)?|"
    r"kill(s|ed)?|excis(e|ed|es|ion)|neuter(s|ed)?|hard-wired|hardwired|dead code|refus(e|es|ed)|reject(s|ed)?|"
    r"delet(e|es|ed)|prevent(s|ed)?|no longer|does not|doesn't|do not|cannot|can't|is not|are not|isn't|aren't|"
    r"nothing|none|zero|no|not|off|without|only|forc(e|es|ed)|ships?|bundl(e|es|ed)|stops?|stopped|silenc\w*|"
    r"sealed|gone)\b", re.I)
NUMBER = re.compile(r"\b\d[\d,.]*\s*(%|patches|files|preferences|prefs|hosts|MB|GB|KB|ms|seconds|minutes|hours|years|"
                    r"lines|groups|hunks|domains|endpoints|modules|calls|requests|connections)\b", re.I)
TELEMETRY_CLAIM = re.compile(
    r"\b(no|never|without|zero|stripp\w*|kill\w*|off|disabl\w*|cut|stop\w*|remov\w*|starv\w*|silenc\w*)\W+(?:[\w'-]+\W+){0,4}?"
    r"(telemetry|phon\w*[ -]home|calls? home|data collection)|"
    r"(telemetry|phon\w*[ -]home|calls? home|data collection)\W+(?:[\w'-]+\W+){0,4}?"
    r"(off|disabl\w*|stripp\w*|remov\w*|kill\w*|never|cut|dead|starv\w*|silenc\w*|no-op)\b", re.I)
DECISION_REF = re.compile(r"\bD-\d{3}-\d{2}\b")
BUILD_FLAG = re.compile(r"(?<![\w-])--(disable|enable)-[a-z0-9][a-z0-9-]*[a-z0-9]")
NEGATED_FLAG = re.compile(r"\b(no|not|never|without|isn't|doesn't|there is no|might expect|would expect|instead of)\b[^.;:]*$", re.I)
PATHLIKE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.@-]+\.(?:cpp|h|mjs|js|jsm|rs|py|ftl|css|ipdl|build|mn|json|yaml|toml|"
                      r"webidl|idl|xhtml|html|in|ini|c|cc|mm))\b")
BACKTICK = re.compile(r"`([^`\s]{3,120})`")
PREFNAME = re.compile(r"^[a-z][\w-]*(\.[\w-]+)+$")
COMMENT = re.compile(r"(//|/\*|^\s*\*|^\s*#|<!--|^\s*--\s)")
PROOF_PHRASES = ((re.compile(r"HTTP log|nsHttp", re.I), "egress"), (re.compile(r"omni\.ja", re.I), "excised"),
                 (re.compile(r"\bad[ /]and tracker|ad/tracker", re.I), "adblock"))
GROUP_REF = re.compile(r"\b(\d\d\.[A-Za-z](?:[\w.-]*[A-Za-z0-9])?)")


class Unreadable(Exception):
    """A check that cannot run. Never a pass."""


def sha(text):
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text)).strip()


def _plain(s):
    return re.sub(r"[*_`~]+", "", s)


def is_claim(s):
    p = _plain(s)
    if len(p) < 20 or not re.search(r"[A-Za-z]{3}", p):
        return False
    return bool(TECHNIQUE.search(p) or ASSERT.search(p) or NUMBER.search(p))


# -- extraction -----------------------------------------------------------------------------------------------------
def _sentences(para):
    """[(line, text)] lines of one paragraph -> [(line, sentence)]."""
    text, starts = "", []
    for ln, s in para:
        starts.append((len(text), ln))
        text += s + " "
    out, pos = [], 0
    for m in list(re.finditer(r"(?<=[.!?])[\"')\]*_]*\s+(?=[A-Z0-9\"'(*`_\[])", text)) + [None]:
        end = m.start() if m else len(text)
        sent = text[pos:end].strip()
        if sent:
            ln = [l for off, l in starts if off <= pos][-1]
            out.append((ln, sent))
        pos = m.end() if m else len(text)
    return out


def doc_units(text):
    """Markdown / plain text -> [(line, unit)]: headings, table rows, and the sentences of paragraphs and list items.
    Code blocks and HTML comments are not claims."""
    out, para, fence, comment, listy = [], [], False, False, False

    def flush():
        if para:
            out.extend(_sentences(para))
            para.clear()
    for i, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith(("```", "~~~")):
            flush()
            fence = not fence
            continue
        if fence:
            continue
        if s and raw.startswith(("    ", "\t")) and not para and not listy:
            continue                                    # an indented code block (a traceback, a command)
        if s and not raw.startswith((" ", "\t")):
            listy = bool(re.match(r"([-*+]|\d+[.)])\s", s))
        if comment:
            comment = "-->" not in s
            continue
        if s.startswith("<!--"):
            flush()
            comment = "-->" not in s
            continue
        if not s:
            flush()
            continue
        s = re.sub(r"^(>\s?)+", "", s).strip()
        if not s:
            flush()
            continue
        if s.startswith("|"):
            flush()
            if not re.fullmatch(r"[|:\-\s]+", s):
                out.append((i, s))
            continue
        if re.match(r"#{1,6}\s", s):
            flush()
            out.append((i, s.lstrip("#").strip()))
            continue
        if re.fullmatch(r"[-*_=]{3,}", s):
            flush()
            continue
        if re.match(r"([-*+]|\d+[.)])\s", s):
            flush()
        para.append((i, s))
    flush()
    return out


def patch_units(text):
    """A .patch -> [(line, unit)]: its '#' header lines (before the diff) and the distinct GORILLA-marked comments
    its added lines carry (the patch's own statements about what the code now does)."""
    out, seen = [], set()
    lines = text.splitlines()
    for i, l in enumerate(lines, 1):
        if l.startswith(("diff ", "--- ", "Index: ", "+++ ", "@@")):
            break
        if l.startswith("#"):
            s = l.lstrip("#").strip()
            if s and not s.lower().startswith("files:"):
                out.append((i, s))
    for i, l in enumerate(lines, 1):
        if not l.startswith("+") or l.startswith("+++"):
            continue
        body = l[1:].strip()
        if not body or not COMMENT.search(body) or not (re.search("gorilla", body, re.I) or TECHNIQUE.search(body)):
            continue
        if body not in seen:
            seen.add(body)
            out.append((i, body))
    return out


AUDIT_REPORT_HEAD = "# Claims audit:"


def is_audit_report(path):
    """True for a report this module wrote (2026-10-03: the published gorilla-patchset/AUDIT-157.md was read back as
    6,351 'claims', 789 of them 'contradicted' - the audit was auditing its own quotations of earlier verdicts)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readline().startswith(AUDIT_REPORT_HEAD)
    except OSError:
        return False


def public_files(owner_root):
    """The public documents: what git publishes from the public patch set (README and notes, not code, not NEW_FILES,
    not manifests, not this module's own published report), plus the owner repo's README.md and
    docs/PRIVACY-AND-HARDENING.md. -> [relative posix paths]."""
    owner = Path(owner_root)
    pub = owner / "gorilla-patchset"
    rels = []
    r = subprocess.run(["git", "-C", str(pub), "ls-files"], capture_output=True, text=True, errors="replace", env=GIT_ENV)
    tracked = r.stdout.splitlines() if r.returncode == 0 else \
        [p.relative_to(pub).as_posix() for p in pub.rglob("*") if p.is_file() and ".git" not in p.parts] if pub.is_dir() else []
    for f in sorted(tracked):
        low = f.lower()
        if "/new_files/" in low or low.endswith(".manifest.txt") or not low.endswith((".md", ".txt")):
            continue
        if is_audit_report(pub / f):
            continue
        rels.append(f"gorilla-patchset/{f}")
    for f in ("README.md", "docs/PRIVACY-AND-HARDENING.md"):
        if (owner / f).is_file():
            rels.append(f)
    return rels


def extract(owner_root, scope):
    """-> [{source, line, text, sha256}] from every public document and every in-scope patch, in a stable order."""
    owner = Path(owner_root)
    out = []
    for rel in public_files(owner):
        text = (owner / rel).read_text(encoding="utf-8", errors="replace")
        seen = set()
        for ln, u in doc_units(text):
            if is_claim(u) and norm(u) not in seen:
                seen.add(norm(u))
                out.append({"source": rel, "line": ln, "text": norm(u), "sha256": sha(u)})
    for p in scope["patches"]:
        rel = scope["pset_rel"] + "/" + p
        text = (owner / rel).read_text(encoding="utf-8", errors="replace")
        for ln, u in patch_units(text):
            if is_claim(u):
                out.append({"source": rel, "line": ln, "text": norm(u), "sha256": sha(u), "patch": p})
    return out


# -- scope: what the policy enables ---------------------------------------------------------------------------------
def scope_of(owner_root):
    """-> {pset, pset_rel, groups: [(group, spec)] enabled in policy order, patches: [rel] in scope, excluded:
    {rel: reason}, disabled: {group: reason}, targets: {file: [patch rels]}}."""
    pset, groups = firefox._policy(owner_root)
    sc = {"pset": pset, "pset_rel": Path(os.path.relpath(pset, owner_root)).as_posix(), "groups": [], "patches": [],
          "excluded": {}, "disabled": {}, "targets": {}, "texts": {}}
    for g, spec in groups.items():
        if spec.get("status") != "enabled":
            sc["disabled"][g] = spec.get("reason", spec.get("status", ""))
            continue
        sc["groups"].append((g, spec))
        ex = set(spec.get("exclude", []))
        for pf in sorted((pset / g).rglob("*.patch")):
            rel = pf.relative_to(pset).as_posix()
            if pf.name in ex:
                sc["excluded"][rel] = spec.get("exclude_reason") or "excluded by the patch policy"
                continue
            sc["patches"].append(rel)
            text = pf.read_text(encoding="utf-8", errors="replace")
            sc["texts"][rel] = text
            for f in firefox.parse_patch(text):
                sc["targets"].setdefault(f["file"], []).append(rel)
    return sc


# -- the tree -------------------------------------------------------------------------------------------------------
class Tree:
    """The ported tree and its pristine root commit, read-only (git cat-file --batch for the pristine blobs)."""

    def __init__(self, workdir):
        self.w = Path(workdir)
        self.cache, self.pcache, self._cat = {}, {}, None
        r = subprocess.run(["git", "-C", str(self.w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True, env=GIT_ENV)
        self.root = (r.stdout.split() or [None])[0]
        r = subprocess.run(["git", "-C", str(self.w), "rev-parse", "HEAD"], capture_output=True, text=True, env=GIT_ENV)
        self.head = r.stdout.strip() or None
        r = subprocess.run(["git", "-C", str(self.w), "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                           text=True, errors="replace", env=GIT_ENV)
        self.dirty = len([l for l in r.stdout.splitlines() if l.strip()]) if r.returncode == 0 else None

    def body(self, rel):
        if rel not in self.cache:
            p = self.w / rel
            self.cache[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines() if p.is_file() else None
        return self.cache[rel]

    def raw(self, rel):
        p = self.w / rel
        return p.read_bytes() if p.is_file() else None

    def pristine(self, rel):
        if not self.root:
            return None
        if rel not in self.pcache:
            if self._cat is None:
                self._cat = subprocess.Popen(["git", "-C", str(self.w), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, env=GIT_ENV)
            self._cat.stdin.write(f"{self.root}:{rel}\n".encode("utf-8"))
            self._cat.stdin.flush()
            head = self._cat.stdout.readline().decode("utf-8", "replace").split()
            if len(head) == 3 and head[1] == "blob":
                data = self._cat.stdout.read(int(head[2]))
                self._cat.stdout.read(1)
                self.pcache[rel] = data.decode("utf-8", "replace").splitlines()
            else:
                self.pcache[rel] = None
        return self.pcache[rel]

    def close(self):
        if self._cat:
            try:
                self._cat.stdin.close()
                self._cat.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._cat = None


HASH_COMMENT = (".py", ".build", ".mn", ".ftl", ".properties", ".toml", ".ini", ".sh", ".yaml", ".yml", ".configure", ".mozbuild")


def _is_comment(line, file):
    s = line.strip()
    if not s:
        return False
    if file.endswith(HASH_COMMENT) or Path(file).name in ("moz.build", "jar.mn", "Makefile.in"):
        return s.startswith("#")
    return s.startswith(("//", "/*", "*", "*/", "<!--"))


class _NoRenames:
    """verify.score_hunk with firefox.renamed_near switched off for one call (single-threaded CLI use only)."""

    def __enter__(self):
        self.saved = firefox.renamed_near
        firefox.renamed_near = lambda *a, **k: []

    def __exit__(self, *exc):
        firefox.renamed_near = self.saved


def score(body, hunk, file, pristine=None):
    """verify.score_hunk, with two second opinions that can only turn NOT-APPLIED into APPLIED when the CODE is in
    place, and say so in the detail:
      - comment drift: the public patch's comment lines differ from the tree's (an emoji, a reworded note) while every
        code line of the hunk is applied (09.REMOTE Marionette h2, 2026-10-03);
      - a 'renamed' verdict whose only renamed partners are comment lines (a JSDoc `@param` line is no rename of
        `this.#initPrefs(testOverrides);`, ExperimentAPI h4 and QuickSuggest h3), or whose old AND new line both
        stand in pristine upstream (two upstream lines side by side are no rename: 22.EGRESS 012, Utils.sys.mjs)."""
    v, d = _score_once(body, hunk, file, pristine)
    if v not in ("NOT-APPLIED", "PARTIAL") or body is None:
        return v, d
    code = [l for l in hunk["lines"] if not (l[:1] in ("+", "-") and _is_comment(l[1:], file))]
    if code != hunk["lines"] and any(l[:1] in ("+", "-") and len(l[1:].strip()) >= vf.SHORT for l in code):
        v3, d3 = _score_once(body, {**hunk, "lines": code}, file, pristine)
        if v3 == "APPLIED":
            n = len(hunk["lines"]) - len(code)
            return "APPLIED", f"code applied ({d3}); {n} comment line(s) of the public patch read differently in the tree"
    return v, d


def _score_once(body, hunk, file, pristine=None):
    v, d = vf.score_hunk(body, hunk, file)
    if v == "NOT-APPLIED" and body is not None and "under new names" in d:
        removed, added, _ = firefox.hunk_sides(hunk)
        rem = [l.strip() for l in removed if len(l.strip()) >= vf.SPECIFIC and not firefox.TRIVIAL.match(l.strip())]
        pairs = firefox.renamed_near(body, hunk, rem, added)
        up = {l.strip() for l in pristine or []}
        if pairs and all(_is_comment(b, file) or _is_comment(a, file) or (a.strip() in up and b.strip() in up) for a, b in pairs):
            with _NoRenames():
                v2, d2 = vf.score_hunk(body, hunk, file)
            if v2 == "APPLIED":
                return "APPLIED", d2 + " (the renamed-line heuristic matched only comments or lines upstream has side by side)"
    return v, d


def _block(lines):
    return "\n".join(l.strip() for l in lines if l.strip())


def _block_check(body, hunk):
    """For a hunk with no judgeable line: is the whole post-image (context + added) there as one block? -> verdict."""
    if body is None:
        return "OBSOLETE-CANDIDATE", "the file does not exist"
    post = _block([l[1:] for l in hunk["lines"] if l[:1] in (" ", "+")])
    pre = _block([l[1:] for l in hunk["lines"] if l[:1] in (" ", "-")])
    hay = "\n" + _block(body) + "\n"
    if post and "\n" + post + "\n" in hay and (post == pre or "\n" + pre + "\n" not in hay or not pre):
        return "APPLIED", "the hunk's whole new block is in the file"
    if pre and "\n" + pre + "\n" in hay:
        return "NOT-APPLIED", "the hunk's old block is still in the file"
    return "UNJUDGEABLE", "only short or punctuation lines, and neither the old nor the new block is in the file"


def _hand_holds(tree, s):
    a = s.get("args") or {}
    h = a.get("hunk")
    if firefox.deletes_whole_file(h):                       # a recorded deletion holds while the file is gone
        gone = tree.raw(a["file"]) is None
        return gone, "deleted, as recorded" if gone else "the file should be deleted and still exists"
    if isinstance(h, dict) and h.get("binary"):
        raw = tree.raw(a["file"])
        ok = raw is not None and hashlib.sha256(raw).hexdigest() == h.get("sha256")
        return ok, "binary hand edit holds" if ok else "binary hand edit: the file is not the recorded bytes"
    b = tree.body(a.get("file", ""))
    if b is None:
        return False, "the file does not exist"
    why = firefox.hand_port_holds(b, h, tree.pristine(a["file"]), firefox.hand_keeps(s.get("hand_note")))
    return (not why), ("hand port holds" if not why else "hand port: " + "; ".join(why)[:160])


def _step_index(steps):
    by = {}
    for s in steps:
        a = s.get("args") or {}
        if s.get("kind") == "model" and a.get("patch") and "hunk" in a:
            m = re.search(r"-h(\d+)$", s["id"])
            if m:
                by.setdefault((a["patch"], int(m.group(1))), []).append(s)
    return by


def _relocations(steps):
    """Owner 'file no longer exists' steps closed by a relocation -> {patch rel: [replacement step ids]}."""
    out = {}
    for s in steps:
        if s.get("kind") != "owner" or "patches a file that no longer exists" not in s.get("title", ""):
            continue
        rel = s["title"].split(" patches a file", 1)[0].strip()
        ids = re.findall(r"'([^']+)'", " ".join(s.get("last_why") or []).split("replaced by", 1)[-1]) \
            if "replaced by" in " ".join(s.get("last_why") or []) else []
        out[rel] = ids
    return out


def _change(h):
    return [l[0] + l[1:].strip() for l in h.get("lines", []) if l[:1] in ("+", "-") and l[1:].strip()]


def _same_change(step, h):
    sh = (step.get("args") or {}).get("hunk")
    if not isinstance(sh, dict) or sh.get("binary"):
        return False
    return _change(sh) == _change(h)


def hunk_status(tree, rel, file, n, h, idx, steps_by_id, relocated, later=None, order=None):
    """-> (status, detail, explained). A step of the task record speaks for this hunk only when it carries the same
    change (the same removed and added lines): the task was ported from a snapshot of the patch set, and a public
    patch whose hunk differs from the one the record names is judged by the tree alone."""
    st = [s for s in idx.get((rel, n), []) if _same_change(s, h)]
    for s in st:
        if s.get("dropped_by_owner"):
            return "DROPPED-BY-MAINTAINER", f"dropped by the maintainer (fingerprint {s.get('drop_fingerprint', '?')})", True
    same = [s for s in st if (s.get("args") or {}).get("file") == file]
    moved = [s for s in st if (s.get("args") or {}).get("file") not in (None, file)]
    if moved and not same:
        s = moved[0]
        a = s["args"]
        if s.get("hand_port") or s.get("done_by") == "hand":
            ok, d = _hand_holds(tree, s)
        else:
            v, d = score(tree.body(a["file"]), a["hunk"], a["file"], tree.pristine(a["file"]))
            ok = v == "APPLIED"
        return ("RELOCATED" if ok else "NOT-APPLIED"), f"moved upstream to {a['file']}: {d}", ok
    s = same[0] if same else None
    if s and s.get("status") == "done" and (s.get("hand_port") or s.get("done_by") == "hand"):
        ok, d = _hand_holds(tree, s)
        if ok:
            return "HAND-PORTED", d, True
    body = tree.body(file)
    v, d = score(body, h, file, tree.pristine(file))
    if v == "NO-SIGNAL":
        v, d = _block_check(body, h)
        if v == "OBSOLETE-CANDIDATE":
            v = "TARGET-GONE"
    if v == "APPLIED":
        pv, _ = score(tree.pristine(file), h, file)
        if pv == "NO-SIGNAL":
            pv, _ = _block_check(tree.pristine(file), h)
        if pv == "APPLIED":
            _, added, _ = firefox.hunk_sides(h)
            new = [l.strip() for l in added if len(l.strip()) >= vf.SHORT]
            have = {l.strip() for l in tree.pristine(file) or []}
            why = ("pristine upstream already has the lines this hunk adds" if new and all(l in have for l in new) else
                   "pristine upstream no longer has the lines this hunk removes (and no renamed form near): nothing to carry")
            return "ALREADY-UPSTREAM", why, True
        return "APPLIED", d, True
    if v == "TARGET-GONE":
        reloc = relocated.get(rel)
        if reloc is not None and body is None:
            appends = [steps_by_id[i] for i in reloc if i in steps_by_id and i.startswith("append-")]
            if appends:
                from . import relocate
                holds = all(relocate.check_append_source({"workdir": str(tree.w)}, origin=rel, **{k: x["args"][k] for k in ("file", "lines")})["ok"]
                            for x in appends)
                return ("RELOCATED" if holds else "NOT-APPLIED"), \
                    f"generated file; additions appended to {', '.join(x['args']['file'] for x in appends)}" + ("" if holds else ": not there"), holds
        if s and s.get("status") == "obsolete":
            return "OBSOLETE", "code gone upstream; the record closed it as obsolete: " + " ".join(s.get("last_why") or [])[:200], True
        sup = _superseded(body, h, file, later, order)
        if sup:
            return "SUPERSEDED", f"a later patch of the set changes these lines again: {', '.join(sup)}", True
        return "OBSOLETE", "code gone upstream (" + d + "); no recorded decision", False
    sup = _superseded(body, h, file, later, order)
    if sup:
        return "SUPERSEDED", f"a later patch of the set changes these lines again: {', '.join(sup)} ({d})", True
    if v == "UNJUDGEABLE":
        return "UNJUDGEABLE", d, False
    extra = "; the record says obsolete, the tree disagrees" if s and s.get("status") == "obsolete" else ""
    return "NOT-APPLIED", (f"[{v}] " if v != "NOT-APPLIED" else "") + d + extra, False


def _superseded(body, h, file, later, order):
    """The patches applied AFTER this one (policy order, then file order) that remove every added line the tree lacks
    and add back every removed line the tree still has (22.EGRESS.LOCKDOWN.157: 013 takes out the pref block 012
    adds). -> [patch rels] or []."""
    if body is None or not later or order is None:
        return []
    removed, added, _ = firefox.hunk_sides(h)
    have = {l.strip() for l in body}
    miss = [l.strip() for l in added if len(l.strip()) >= vf.SHORT and not firefox.TRIVIAL.match(l.strip()) and l.strip() not in have]
    still = [l.strip() for l in removed if len(l.strip()) >= vf.SPECIFIC and l.strip() in have and l.strip() not in {a.strip() for a in added}]
    if not miss and not still:
        return []
    after = [x for x in later.get(file, []) if x[0] > order]
    who = set()
    for l in miss:
        hit = [rel for _, rel, rem, _ in after if l in rem]
        if not hit:
            return []
        who.add(hit[0])
    for l in still:
        hit = [rel for _, rel, _, add in after if l in add]
        if not hit:
            return []
        who.add(hit[0])
    return sorted(who)


def later_index(sc):
    """{file: [(order, patch rel, removed lines, added lines)]} over the in-scope patches in application order."""
    out = {}
    for i, rel in enumerate(sc["patches"]):
        for f in firefox.parse_patch(sc["texts"][rel]):
            rem, add = set(), set()
            for h in f["hunks"]:
                r, a, _ = firefox.hunk_sides(h)
                rem.update(l.strip() for l in r)
                add.update(l.strip() for l in a)
            out.setdefault(f["file"], []).append((i, rel, rem, add))
    return out


def patch_verdict(hunks):
    """[{status, explained}] -> (verdict, implemented %, gaps)."""
    impl = sum(1 for x in hunks if x["status"] in HUNK_OK)
    expl = sum(1 for x in hunks if x["status"] not in HUNK_OK and x["explained"])
    gaps = len(hunks) - impl - expl
    denom = len(hunks) - expl
    if not hunks:
        return "MISSING", 0.0, 0
    if denom == 0:
        drop = all(x["status"] == "DROPPED-BY-MAINTAINER" for x in hunks)
        return ("DROPPED-BY-MAINTAINER" if drop else "OBSOLETE-EXPLAINED"), 100.0, 0
    pct = round(100.0 * impl / denom, 1)
    if gaps == 0:
        return "IMPLEMENTED", pct, 0
    return ("MISSING" if impl == 0 else "PARTIAL"), pct, gaps


def _purpose(owner, sc, rel, text):
    head = [l.lstrip("#").strip() for l in text.splitlines()[:12] if l.startswith("#") and not l.lstrip("#").strip().lower().startswith("files:")]
    if head:
        return " ".join(h for h in head if h)[:300], "patch header"
    g, name = rel.split("/", 1)
    for doc in sorted((sc["pset"] / g).glob("README*")) + [sc["pset"] / "MAP_IBM.md"]:
        if doc.is_file():
            for ln in doc.read_text(encoding="utf-8", errors="replace").splitlines():
                if name in ln and len(norm(ln)) > len(name) + 10:
                    return _plain(norm(ln))[:300], Path(os.path.relpath(doc, owner)).as_posix()
    spec = dict(sc["groups"]).get(g, {})
    return norm(spec.get("reason", ""))[:300], "group (patch policy)"


def audit_patches(owner_root, workdir, steps, patch_decisions=None, register_ids=None, tree=None, sc=None):
    owner = Path(owner_root)
    sc = sc or scope_of(owner)
    tree = tree or Tree(workdir)
    idx, by_id, reloc = _step_index(steps), {s["id"]: s for s in steps}, _relocations(steps)
    patch_decisions = patch_decisions or {}
    later, order = later_index(sc), {rel: i for i, rel in enumerate(sc["patches"])}
    out = []
    for g, spec in sc["groups"]:
        ex = set(spec.get("exclude", []))
        for pf in sorted((sc["pset"] / g).rglob("*.patch")):
            rel = pf.relative_to(sc["pset"]).as_posix()
            text = sc["texts"].get(rel) or pf.read_text(encoding="utf-8", errors="replace")
            purpose, purpose_src = _purpose(owner, sc, rel, text)
            hunks = []
            for f in firefox.parse_patch(text):
                for n, h in enumerate(f["hunks"], 1):
                    if pf.name in ex:
                        hunks.append({"file": f["file"], "n": n, "status": "DROPPED-BY-MAINTAINER", "explained": True,
                                      "detail": "excluded by the patch policy: " + norm(spec.get("exclude_reason", ""))[:200]})
                        continue
                    stt, d, e = hunk_status(tree, rel, f["file"], n, h, idx, by_id, reloc, later, order.get(rel))
                    hunks.append({"file": f["file"], "n": n, "status": stt, "detail": d, "explained": e})
            verdict, pct, gaps = patch_verdict(hunks)
            pd = patch_decisions.get(rel) or {}
            decided = bool(pd.get("decision")) and (register_ids is None or pd["decision"] in register_ids)
            explained = verdict in PATCH_EXPLAINED or decided
            out.append({"patch": rel, "group": g, "purpose": purpose, "purpose_source": purpose_src, "hunks": hunks,
                        "verdict": verdict, "implemented_pct": pct, "gaps": gaps, "explained": explained,
                        "decision": pd.get("decision") if decided else None, "fail": not explained})
    # NEW_FILES and DELETED_FILES
    hand_edited = {(s.get("args") or {}).get("file") for s in steps if s.get("hand_port") and s.get("status") == "done"}
    replaced = {}                       # a recorded replace-files step copied bytes from a NON-public snapshot set
    for s in steps:
        a = s.get("args") or {}
        if s["id"].startswith("replace-files-") and s.get("status") == "done" and a.get("harness_root") and a.get("group"):
            try:
                replaced[a["group"]] = firefox._policy(a["harness_root"])[0] / a["group"] / "REPLACE_FILES"
            except (OSError, ValueError, KeyError):
                pass
    nf = []
    for g, spec in sc["groups"]:
        for src, rel in firefox.new_files(sc["pset"], g):
            raw = tree.raw(rel)
            rf = replaced.get(g)
            if raw is None:
                v = "MISSING"
            elif raw == src.read_bytes():
                v = "IDENTICAL"
            elif rel in hand_edited:
                v = "CHANGED-BY-HAND-STEP"
            elif rf is not None and (rf / rel).is_file() and (rf / rel).read_bytes() == raw:
                v = "REPLACED-NOT-PUBLIC"       # the shipped bytes come from a recorded step, not from the public set
            else:
                v = "DIFFERS"
            nf.append({"group": g, "file": rel, "status": v, "fail": v in ("MISSING", "DIFFERS", "REPLACED-NOT-PUBLIC")})
    deleted = []
    for g, spec in sc["groups"]:
        m = sc["pset"] / g / "DELETED_FILES.manifest.txt"
        if m.is_file():
            for l in m.read_text(encoding="utf-8").splitlines():
                if l.strip():
                    deleted.append({"group": g, "file": l.strip(), "present": (tree.w / l.strip()).exists()})
    return {"patches": out, "new_files": nf, "deleted": deleted, "excluded": sc["excluded"], "disabled": sc["disabled"]}


# -- seeding evidence -----------------------------------------------------------------------------------------------
def _conditional_in_patch(text, line_no):
    """Is the added line at `line_no` (1-based, in the .patch) under a preprocessor #if within its hunk?"""
    lines = text.splitlines()
    depth = 0
    for l in reversed(lines[:line_no - 1]):
        if l.startswith("@@"):
            break
        body = l[1:] if l[:1] in (" ", "+", "-") else l
        if l.startswith("-"):
            continue
        if proof.PRE_END.match(body):
            depth -= 1
        elif proof.PRE_IF.match(body):
            depth += 1
    return depth > 0


def tree_prefs(workdir):
    """{name: value} the ported tree's all.js and firefox.js set (the last line wins): the dictionary that says which
    backticked names in prose are prefs."""
    out = {}
    for src, _, _ in proof.PREF_SOURCES + (proof.BRANDING_PREFS,):
        p = Path(workdir) / src
        if p.is_file():
            for k, (v, _, _) in proof.pref_lines(p.read_text(encoding="utf-8", errors="replace")).items():
                out[k] = v
    return out


def seed_evidence(claim, sc, prefs):
    """Evidence the claim's own text names, each marked `seeded: auto`. Never invents: no name, no link."""
    ev, seen = [], set()

    def add(kind, arg):
        key = (kind, json.dumps(arg, sort_keys=True))
        if key not in seen:
            seen.add(key)
            ev.append({kind: arg, "seeded": "auto"})
    text = claim["text"]
    plain = _plain(text)
    if claim.get("patch"):
        add("patch", claim["patch"])
        m = proof.PREF.match(text)
        if m and not _conditional_in_patch(sc["texts"].get(claim["patch"], ""), claim["line"]):
            add("pref", {"name": m.group(1), "value": proof._norm(m.group(2)), "locked": m.group(3) == "locked"})
    for t in sorted({x.lower() for x in TECHNIQUE.findall(plain)}):
        stem = t[:7]
        for rel in sc["patches"]:
            if rel != claim.get("patch") and any(l.startswith("+") and stem in l.lower() for l in sc["texts"][rel].splitlines()):
                add("patch", rel)
    if TELEMETRY_CLAIM.search(plain):
        add("leakgate_policy", "NETWORK_POLICY")
        add("leakgate_policy", "TELEMETRY_POLICY")
        add("decision", "D-157-00")
    for d in sorted(set(DECISION_REF.findall(text))):
        add("decision", d)
    for m in BUILD_FLAG.finditer(text):
        # "no --disable-telemetry exists upstream", "You might expect a --disable-telemetry flag": a sentence that
        # says a flag is NOT used is not evidence that it is (2026-10-04: three true claims were CONTRADICTED)
        if NEGATED_FLAG.search(text[max(0, m.start() - 40):m.start()]):
            continue
        add("mozconfig_has", {"file": WIN_MOZCONFIG, "text": f"ac_add_options {m.group(0)}"})
    enabled = {g for g, _ in sc["groups"]}
    for g in sorted(set(GROUP_REF.findall(text))):
        if g in enabled:
            add("patch_group", g)
    names = set(PATHLIKE.findall(text)) | {b for b in BACKTICK.findall(text) if "." in b}
    basenames = {}
    for f in sc["targets"]:
        basenames.setdefault(f.rsplit("/", 1)[-1], []).append(f)
    for nm in sorted(names):
        hits = sc["targets"].get(nm) or (sc["targets"].get(basenames[nm][0]) if len(basenames.get(nm, [])) == 1 else None)
        for rel in sorted(set(hits or [])):
            add("patch", rel)
    for b in sorted(set(BACKTICK.findall(text))):
        if PREFNAME.match(b) and b in prefs:
            m = re.search(re.escape(b) + r"`?\s*(?:=|:|is|to)?\s*`?(true|false|-?\d+)\b", text)
            val = m.group(1) if m else None
            if val is None and prefs[b] in ("true", "false") and re.search(r"\b(off|disabled|false)\b", plain, re.I) \
                    and not re.search(r"\b(on|enabled|true)\b", plain, re.I):
                val = "false"
            if val is not None:
                arg = {"name": b, "value": val}
                if re.search(r"\blocked\b", plain, re.I):
                    arg["locked"] = True
                add("pref", arg)
    for rx, row in PROOF_PHRASES:
        if rx.search(text):
            add("proof_row", row)
    return ev


# -- the register ---------------------------------------------------------------------------------------------------
HEADER = """# Gorilla Firefox: the public claims register.
#
# Every sentence the public repository uses to say what the browser does or does not do, with the evidence that
# proves it. `fieldkit build-harness claims TASK` extracts the claims, appends new ones here (never rewrites an
# entry), checks every entry and writes the public audit report next to this file.
#
# Rules:
# - text and sha256 are the claim as published; when the source changes, the entry turns STALE.
# - evidence: one check per item (kinds in fieldkit/buildh/claims.py). `seeded: auto` = linked by the extractor from
#   the claim's own words; the maintainer reviews it and removes the marker. An entry without evidence is UNPROVEN.
# - sources_excluded: {path: {reason, by: maintainer}}: a public document the maintainer takes out of the audit (it is
#   listed as excluded in the report, never silently). Only the maintainer adds these.
# - patch_decisions: the maintainer's recorded reason why a patch may stay PARTIAL or MISSING: {decision: D-157-xx,
#   reason: ...}, the decision must exist in decisions/PRODUCT-DECISIONS.yaml. Only the maintainer adds these.
"""


def load_register(owner_root):
    p = Path(owner_root) / REGISTER
    if not p.is_file():
        return {"path": str(p), "exists": False, "claims": [], "patch_decisions": {}, "sources_excluded": {}, "problems": []}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    claims = data.get("claims") or []
    problems, seen = [], set()
    for c in claims:
        i = c.get("id")
        if not i or i in seen:
            problems.append(f"claim without a unique id: {str(c.get('text'))[:60]!r}")
        seen.add(i)
        for need in ("text", "sha256", "source", "line"):
            if c.get(need) in (None, ""):
                problems.append(f"{i}: missing {need}")
        for e in c.get("evidence") or []:
            kinds = [k for k in e if k != "seeded"]
            if len(kinds) != 1 or kinds[0] not in CLAIM_KINDS:
                problems.append(f"{i}: unknown evidence {e!r} (kinds: {', '.join(CLAIM_KINDS)})")
    excl = data.get("sources_excluded") or {}
    for src, why in excl.items():
        if not isinstance(why, dict) or not why.get("reason") or why.get("by") != "maintainer":
            problems.append(f"sources_excluded {src}: needs reason and by: maintainer")
    return {"path": str(p), "exists": True, "claims": claims, "patch_decisions": data.get("patch_decisions") or {},
            "sources_excluded": {k: v for k, v in excl.items() if isinstance(v, dict) and v.get("reason") and v.get("by") == "maintainer"},
            "problems": problems}


def save_register(owner_root, reg):
    p = Path(owner_root) / REGISTER
    p.parent.mkdir(parents=True, exist_ok=True)
    retired = reg.get("retired")
    if retired is None and p.is_file():        # a save that does not handle retired entries keeps them as they are
        retired = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("retired")
    data = {"version": 1, "patch_decisions": reg.get("patch_decisions") or {},
            "sources_excluded": reg.get("sources_excluded") or {}, "claims": reg["claims"]}
    if retired:
        data["retired"] = retired
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=4096, default_flow_style=None)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n" + body)
    return p


def retire_stale(task_id, install_dir=None, today=None):
    """Move every STALE entry (its document no longer says it, or no longer exists) from `claims` to `retired`, with
    the date and the reason. The current wording of a changed sentence is registered as a new claim by the next
    audit, so nothing the documents say goes unjudged; the old wording stays on record instead of being deleted.
    2026-10-04: 58 stale entries had sat in the register through every build since the docs were rewritten.
    -> sorted retired ids."""
    from . import buildrun, task
    owner = buildrun._owner_root(task.load(task_id))
    res = run(task_id, install_dir=install_dir, write=False)
    stale = {c["id"]: c.get("why") or "stale" for c in res["claims"] if c.get("verdict") == "STALE"}
    p = Path(owner) / REGISTER
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    keep, retired = [], list(data.get("retired") or [])
    for c in data.get("claims") or []:
        if c.get("id") in stale:
            retired.append({**c, "retired": today or time.strftime("%Y-%m-%d"), "retired_why": stale[c["id"]]})
        else:
            keep.append(c)
    save_register(owner, {"claims": keep, "patch_decisions": data.get("patch_decisions") or {},
                          "sources_excluded": data.get("sources_excluded") or {}, "retired": retired})
    return sorted(stale)


def merge(reg, found, sc, prefs):
    """Register entries + freshly extracted claims -> (entries to judge, new entries). Existing entries are kept as they
    are; an extracted claim not yet registered gets the next id and seeded evidence."""
    have = {(c["source"], c["sha256"]) for c in reg["claims"]}
    # evidence the extractor seeded and nobody reviewed yet follows the extractor: re-derived on every run, so a fixed
    # seeding rule reaches existing claims too (reviewed entries - no `seeded: auto` - are never touched)
    by_key = {(c["source"], c["sha256"]): c for c in found}
    for c in reg["claims"]:
        f = by_key.get((c["source"], c["sha256"]))
        if f and c.get("seeded") == "auto" and all(e.get("seeded") == "auto" for e in c.get("evidence") or []):
            c["evidence"] = seed_evidence(f, sc, prefs)
    nums = [int(m.group(1)) for c in reg["claims"] if (m := re.match(r"C-(\d+)$", str(c.get("id"))))]
    nxt = max(nums, default=0) + 1
    new = []
    for c in found:
        if (c["source"], c["sha256"]) in have:
            continue
        have.add((c["source"], c["sha256"]))
        entry = {"id": f"C-{nxt:04d}", "text": c["text"], "sha256": c["sha256"], "source": c["source"], "line": c["line"],
                 "seeded": "auto", "evidence": seed_evidence(c, sc, prefs)}
        nxt += 1
        new.append(entry)
    return reg["claims"] + new, new


# -- running the evidence -------------------------------------------------------------------------------------------
def installed_build_id(install_dir):
    if not install_dir:
        return None
    for name in ("application.ini", "platform.ini"):
        p = Path(install_dir) / name
        if p.is_file():
            m = re.search(r"^BuildID=(\d+)", p.read_text(encoding="utf-8", errors="replace"), re.M)
            if m:
                return m.group(1)
    return None


def _leakgate(ctx, policy):
    if "leakgate" not in ctx:
        p = Path(ctx["owner"]) / "state" / "leakgate_result.json"
        try:
            ctx["leakgate"] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            ctx["leakgate"] = e
    st = ctx["leakgate"]
    if isinstance(st, Exception):
        raise Unreadable(f"no leak-gate result: state/leakgate_result.json {'is missing' if isinstance(st, OSError) else 'does not parse'}")
    bid = ctx.get("build_id")
    if not bid:
        raise Unreadable("no installed build: the leak-gate result cannot be tied to it")
    if not st.get("release_run"):
        raise Unreadable("the latest leak-gate run was not a release run")
    if str(st.get("BUILD")) != str(bid):
        raise Unreadable(f"the latest release leak-gate run is of build {st.get('BUILD')}, the installed build is {bid}")
    got = (st.get("policies") or {}).get(policy)
    if got is None:
        raise Unreadable(f"{policy} is not in the leak-gate result")
    return got == "PASS", f"{policy} {got} (release run {st.get('when', '?')}, build {bid})"


def _proof(ctx, row):
    """The row's result in the post-install proof journalled since the latest install into this folder."""
    if "proof" not in ctx:
        rows, why = {}, None
        jp, target = ctx.get("journal"), ctx.get("install")
        if not jp or not Path(jp).is_file():
            why = "no task journal"
        elif not target:
            why = "no installed build"
        else:
            key = os.path.normcase(os.path.abspath(str(target)))
            events = [json.loads(l) for l in Path(jp).read_text(encoding="utf-8").splitlines() if l.strip()]
            mine = [e for e in events if os.path.normcase(os.path.abspath(str(e.get("target", "")))) == key]
            last_install = max((i for i, e in enumerate(mine) if e.get("event") == "install" and e.get("ok")), default=None)
            if last_install is None:
                why = "no recorded install into this folder"
            else:
                for e in mine[last_install + 1:]:
                    if e.get("event") != "post_install":
                        continue
                    names = {}
                    for name, rc, *_ in e.get("results") or []:
                        names.setdefault(name, []).append(rc)
                    for name, rcs in names.items():
                        rows[name] = (all(rc == 0 for rc in rcs), e.get("t"))
        ctx["proof"] = (rows, why)
    rows, why = ctx["proof"]
    if why:
        raise Unreadable(why)
    if row not in rows:
        raise Unreadable(f"the post-install row {row!r} has not run since the latest install")
    ok, when = rows[row]
    return ok, f"post-install {row}: {'passed' if ok else 'FAILED'} ({when})"


def _decision(ctx, did):
    if "decisions" not in ctx:
        try:
            ctx["decisions"] = {r["id"]: r for r in dec.check(ctx["owner"], ctx["workdir"], ctx["install"])["rows"]}
        except (FileNotFoundError, OSError, ValueError) as e:
            ctx["decisions"] = e
    d = ctx["decisions"]
    if isinstance(d, Exception):
        raise Unreadable(f"no decision register: {d}")
    r = d.get(did)
    if r is None:
        raise Unreadable(f"{did} is not in the decision register")
    if r["verdict"] == "UNCHECKABLE":
        raise Unreadable(f"{did} could not be checked: {'; '.join(r['evidence'])[:160]}")
    if r["verdict"] == "PENDING":
        raise Unreadable(f"{did} is decided but pending (not in the build)")
    # a recorded trade-off (ACCEPTED) is in force as much as an enforced check: the release rule is "every entry
    # ENFORCED or a recorded trade-off" (2026-10-03: claims naming D-157-30, the standing policy, were CONTRADICTED)
    return r["verdict"] in ("ENFORCED", "ACCEPTED"), f"{did} {r['verdict']}"


def _patch(ctx, rel):
    p = ctx["patches"].get(rel)
    if p is None:
        if rel in ctx["excluded"]:
            raise Unreadable(f"{rel} is excluded from this build by the patch policy")
        g = rel.split("/", 1)[0]
        if g in ctx["disabled"]:
            raise Unreadable(f"{rel}: group {g} is not enabled for this build")
        raise Unreadable(f"{rel} is not a patch of the patch set")
    # a patch the patch table counts as explained (OBSOLETE-EXPLAINED, DROPPED, or PARTIAL/MISSING with a maintainer
    # decision on record) proves its claims too: 2026-10-03, TranslationsParent PARTIAL under D-157-03 still turned
    # 50 claims of the translations patch CONTRADICTED
    why = f"; explained by {p['decision']}" if p.get("decision") else ""
    return p["verdict"] == "IMPLEMENTED" or bool(p.get("explained")), f"{rel}: {p['verdict']} ({p['implemented_pct']}%){why}"


def _group(ctx, g):
    ps = [p for p in ctx["patches"].values() if p["group"] == g]
    if not ps:
        if g in ctx["disabled"]:
            raise Unreadable(f"group {g} is not enabled for this build")
        raise Unreadable(f"group {g} has no patches in scope")
    bad = [p["patch"] for p in ps if p["verdict"] not in PATCH_EXPLAINED and not p.get("explained")]
    return not bad, f"{g}: {len(ps) - len(bad)} of {len(ps)} patches implemented or explained" + (f"; e.g. {bad[0]}" if bad else "")


def run_check(kind, arg, ctx):
    """-> (ok, evidence). Raises Unreadable when it cannot run."""
    if kind == "patch":
        return _patch(ctx, arg)
    if kind == "patch_group":
        return _group(ctx, arg)
    if kind == "leakgate_policy":
        return _leakgate(ctx, arg)
    if kind == "proof_row":
        return _proof(ctx, arg)
    if kind == "decision":
        return _decision(ctx, arg)
    try:
        return dec._check(kind, arg, ctx)
    except dec.Unreadable as e:
        raise Unreadable(str(e))
    except (OSError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
        raise Unreadable(f"{kind}: {e}")


def source_text(ctx, rel):
    if rel not in ctx["sources"]:
        p = Path(ctx["owner"]) / rel
        ctx["sources"][rel] = norm(_plain(p.read_text(encoding="utf-8", errors="replace"))) if p.is_file() else None
    return ctx["sources"][rel]


def judge(claim, ctx, found_keys):
    """-> {verdict, results: [(kind, arg, 'ok'|'FAIL'|'cannot run', evidence)], why}."""
    res = []
    if sha(claim.get("text", "")) != claim.get("sha256"):
        return {"verdict": "STALE", "results": res, "why": "the registered text no longer matches its sha256"}
    if (claim["source"], claim["sha256"]) not in found_keys:
        st = source_text(ctx, claim["source"])
        if st is None:
            return {"verdict": "STALE", "results": res, "why": f"{claim['source']} no longer exists"}
        if norm(_plain(claim["text"])) not in st:
            return {"verdict": "STALE", "results": res, "why": f"{claim['source']} no longer says this"}
    ev = claim.get("evidence") or []
    if not ev:
        return {"verdict": "UNPROVEN", "results": res, "why": "no evidence: nothing proves this claim"}
    failed = unrun = False
    for e in ev:
        kind = next(k for k in e if k != "seeded")
        arg = e[kind]
        try:
            ok, what = run_check(kind, arg, ctx)
        except Unreadable as x:
            unrun = True
            res.append((kind, arg, "cannot run", str(x)))
            continue
        res.append((kind, arg, "ok" if ok else "FAIL", what))
        failed = failed or not ok
    if failed:
        return {"verdict": "CONTRADICTED", "results": res, "why": "evidence fails"}
    if unrun:
        return {"verdict": "UNPROVEN", "results": res, "why": "a check could not run: unchecked is not proven"}
    return {"verdict": "PROVEN", "results": res, "why": "every check passed"}


# -- the audit ------------------------------------------------------------------------------------------------------
def audit(owner_root, workdir, install_dir=None, steps=(), journal=None, strict=False, write_register=True, task_id=None,
          version=None):
    owner = Path(owner_root)
    sc = scope_of(owner)
    tree = Tree(workdir)
    try:
        reg = load_register(owner)
        try:
            dreg = dec.load(owner)
            register_ids = {e.get("id") for e in dreg["entries"] if e.get("status") in ("enforced", "trade-off")}
        except (FileNotFoundError, OSError, ValueError):
            register_ids = set()
        pa = audit_patches(owner, workdir, list(steps), reg["patch_decisions"], register_ids, tree=tree, sc=sc)
    finally:
        tree.close()
    excl = reg["sources_excluded"]
    found = [c for c in extract(owner, sc) if c["source"] not in excl]
    prefs = tree_prefs(workdir)
    claims, new = merge(reg, found, sc, prefs)
    reg_path = None
    if write_register and (new or not reg["exists"]):
        reg_path = save_register(owner, {"claims": claims, "patch_decisions": reg["patch_decisions"], "sources_excluded": excl})
    claims = [c for c in claims if c["source"] not in excl]
    # claims registered from our own published report before is_audit_report existed stay in the append-only
    # register, but are not judged: they quote earlier verdicts, they are not claims about the browser
    own = {s for s in {c["source"] for c in claims} if is_audit_report(owner / s)}
    claims = [c for c in claims if c["source"] not in own]
    ctx ={"owner": str(owner), "workdir": str(workdir), "install": str(install_dir) if install_dir else None,
           "build_id": installed_build_id(install_dir), "journal": journal, "sources": {},
           "patches": {p["patch"]: p for p in pa["patches"]}, "excluded": pa["excluded"], "disabled": pa["disabled"]}
    found_keys = {(c["source"], c["sha256"]) for c in found}
    rows, new_ids = [], {c["id"] for c in new}
    for c in claims:
        j = judge(c, ctx, found_keys)
        rows.append({"id": c["id"], "text": c["text"], "source": c["source"], "line": c["line"],
                     "seeded": c.get("seeded"), "registered": c["id"] not in new_ids or bool(reg_path), **j})
    pats = pa["patches"]
    count = lambda xs, key, v: sum(1 for x in xs if x[key] == v)
    totals = {"claims": len(rows), **{v: count(rows, "verdict", v) for v in VERDICTS},
              "patches": len(pats), **{v: count(pats, "verdict", v) for v in
                                       ("IMPLEMENTED", "PARTIAL", "MISSING", "OBSOLETE-EXPLAINED", "DROPPED-BY-MAINTAINER")},
              "patches_failing": sum(1 for p in pats if p["fail"]),
              "hunks": sum(len(p["hunks"]) for p in pats),
              "new_files": len(pa["new_files"]), "new_files_failing": sum(1 for x in pa["new_files"] if x["fail"]),
              "deleted": len(pa["deleted"]), "deleted_present": sum(1 for x in pa["deleted"] if x["present"])}
    hs = {}
    for p in pats:
        for h in p["hunks"]:
            hs[h["status"]] = hs.get(h["status"], 0) + 1
    totals["hunk_status"] = dict(sorted(hs.items()))
    problems = list(reg["problems"])
    lenient_ok = not problems and not totals["CONTRADICTED"] and not totals["STALE"] and not totals["patches_failing"] \
        and not totals["new_files_failing"] and not totals["deleted_present"]
    strict_ok = lenient_ok and totals["PROVEN"] == totals["claims"]
    return {"task": task_id, "version": version, "owner": str(owner), "register": str(Path(owner) / REGISTER),
            "register_written": str(reg_path) if reg_path else None, "new_claims": len(new), "problems": problems,
            "tree": {"root": tree.root, "head": tree.head, "dirty": tree.dirty}, "build_id": ctx["build_id"],
            "install": bool(install_dir), "roots": {"<owner repo>": str(owner), "<tree>": str(workdir),
                                                    **({"<install>": str(install_dir)} if install_dir else {})},
            "claims": rows, "patch_audit": pa, "totals": totals, "strict": strict,
            "ok": strict_ok if strict else lenient_ok, "strict_ok": strict_ok, "lenient_ok": lenient_ok,
            "generated": time.strftime("%Y-%m-%d"), "sources": sorted({c["source"] for c in rows}), "sources_excluded": excl}


STRONG = re.compile(r"never|telemetry|phon\w* home|calls? home|removed|blocked|cannot|locked|nothing|no AI|stripped|cut", re.I)


PRIVACY_GROUPS = ("05.", "07.", "09.", "12.", "13.", "14.", "21.", "22.")


def worst_gaps(res, n=15):
    """The gaps that would embarrass us first, taken in turn from three lists so no one kind crowds out the others:
    contradicted claims of the front-page documents (one per distinct failing evidence), patches not in the tree
    (privacy groups first, MISSING before PARTIAL, most hunks missing first), and the loudest unproven front-page
    claims. Then everything else."""
    order = {d: i for i, d in enumerate(PRIMARY_DOCS)}
    contra, unproven, rest, seen = [], [], [], set()
    for c in res["claims"]:
        prim = c["source"] in order
        where = f"{c['id']} ({c['source']}:{c['line']}): \"{c['text'][:160]}\""
        if c["verdict"] == "CONTRADICTED":
            sig = tuple(sorted(json.dumps(r[1], sort_keys=True) for r in c["results"] if r[2] == "FAIL"))
            line = f"CONTRADICTED {where} - " + "; ".join(r[3] for r in c["results"] if r[2] == "FAIL")[:200]
            if prim and sig not in seen:
                seen.add(sig)
                contra.append((order[c["source"]], line))
            else:
                rest.append(line)
        elif c["verdict"] in ("UNPROVEN", "STALE") and prim:
            line = f"{c['verdict']} {where} - {c['why']}"
            (unproven if STRONG.search(c["text"]) else rest).append((order[c["source"]], line) if STRONG.search(c["text"]) else line)
    pats = []
    for p in res["patch_audit"]["patches"]:
        if p["fail"]:
            bad = [h for h in p["hunks"] if h["status"] not in HUNK_OK and not h["explained"]]
            pats.append(((not p["patch"].startswith(PRIVACY_GROUPS), p["verdict"] != "MISSING", -len(bad), p["patch"]),
                         f"{p['verdict']} patch {p['patch']} ({p['implemented_pct']}% implemented, {len(bad)} of {len(p['hunks'])} "
                         f"hunk(s) not in the tree, no maintainer decision): e.g. {bad[0]['file']} #{bad[0]['n']} "
                         f"{bad[0]['status']}: {bad[0]['detail'][:120]}"))
    rest += [f"NEW_FILES {x['group']}/{x['file']}: {x['status']}" for x in res["patch_audit"]["new_files"] if x["fail"]]
    rest += [f"DELETED_FILES {x['group']}: {x['file']} is still in the tree" for x in res["patch_audit"]["deleted"] if x["present"]]
    lists = [[t for _, t in sorted(contra, key=lambda x: x[0])], [t for _, t in sorted(pats)],
             [t for _, t in sorted(unproven, key=lambda x: x[0])]]
    out = []
    while len(out) < n and any(lists):
        for lst in lists:
            if lst and len(out) < n:
                out.append(lst.pop(0))
    return out + rest[:max(0, n - len(out))]


# -- the report -----------------------------------------------------------------------------------------------------
def _md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _arg(a):
    return a if isinstance(a, str) else json.dumps(a, ensure_ascii=False, sort_keys=True)


def report(res):
    t, tr = res["totals"], res["tree"]
    L = [f"# Claims audit: Gorilla Unleashed {res.get('version') or ''}".rstrip(), "",
         "Every claim the public repository makes about this browser, and what proves it; every patch of the enabled "
         "groups, and whether the ported source tree carries it. Generated by `fieldkit build-harness claims"
         f"{' ' + res['task'] if res.get('task') else ''}` (Fieldkit, `fieldkit/buildh/claims.py`). Fail closed: a check "
         "that could not run counts as unproven, never as proven.", "",
         f"- Generated: {res['generated']}",
         f"- Source tree: upstream base commit `{tr['root'] or 'unknown'}`, tree commit `{tr['head'] or 'unknown'}`"
         + ("" if tr["dirty"] in (0, None) else f" plus {tr['dirty']} uncommitted change(s)"),
         f"- Installed build: BuildID `{res['build_id']}`" if res["build_id"] else "- Installed build: none found (every check on the installed browser is unproven)",
         f"- Claims register: `{REGISTER.as_posix()}`; patch policy: `config/patch_policy.json`", "",
         "## Totals", "",
         f"| | |", "|---|---|",
         f"| Claims found | {t['claims']} |",
         f"| PROVEN | {t['PROVEN']} |", f"| UNPROVEN | {t['UNPROVEN']} |", f"| CONTRADICTED | {t['CONTRADICTED']} |",
         f"| STALE | {t['STALE']} |",
         f"| Patches in enabled groups | {t['patches']} ({t['hunks']} hunks) |",
         f"| IMPLEMENTED | {t['IMPLEMENTED']} |", f"| PARTIAL | {t['PARTIAL']} |", f"| MISSING | {t['MISSING']} |",
         f"| OBSOLETE-EXPLAINED | {t['OBSOLETE-EXPLAINED']} |", f"| DROPPED-BY-MAINTAINER | {t['DROPPED-BY-MAINTAINER']} |",
         f"| Patches failing (partial or missing, no maintainer decision) | {t['patches_failing']} |",
         f"| New files | {t['new_files']} ({t['new_files_failing']} missing or different) |",
         f"| Deleted files | {t['deleted']} ({t['deleted_present']} still present) |",
         f"| Hunks by status | {', '.join(f'{k} {v}' for k, v in t['hunk_status'].items())} |", "",
         f"Basic check (no claim contradicted or stale, every patch, new file and deletion in place or explained): "
         f"**{'PASS' if res['lenient_ok'] else 'FAIL'}**. Release check (the same, and every claim proven): "
         f"**{'PASS' if res['strict_ok'] else 'FAIL'}**.", ""]
    if res["problems"]:
        L += ["Register problems:", ""] + [f"- {_md(p)}" for p in res["problems"]] + [""]
    L += ["## The worst gaps", ""] + [f"{i}. {_md(g)}" for i, g in enumerate(worst_gaps(res), 1)] + [""]
    L += ["## Claims by document", "", "| Document | Claims | Proven | Unproven | Contradicted | Stale |", "|---|---|---|---|---|---|"]
    for s in res["sources"]:
        cs = [c for c in res["claims"] if c["source"] == s]
        L.append(f"| `{s}` | {len(cs)} | " + " | ".join(str(sum(c["verdict"] == v for c in cs)) for v in VERDICTS) + " |")
    if res.get("sources_excluded"):
        L += ["", "Documents the maintainer took out of the audit:", ""]
        L += [f"- `{s}`: {_md(v.get('reason', ''))[:240]}" for s, v in sorted(res["sources_excluded"].items())]
    L += ["", "## Every claim", "", "Each claim is quoted as published, with its line. A claim with no evidence line has no "
          "evidence at all: nothing proves it (UNPROVEN).", ""]
    for s in res["sources"]:
        L += [f"### `{s}`", ""]
        for c in [c for c in res["claims"] if c["source"] == s]:
            L.append(f"- **{c['verdict']}** {c['id']} (line {c['line']}): \"{_md(c['text'])}\"")
            if not c["results"] and c["verdict"] != "UNPROVEN":
                L.append(f"  - {_md(c['why'])}")
            for kind, arg, st, what in c["results"]:
                L.append(f"  - {st}: `{kind}: {_md(_arg(arg))[:160]}` - {_md(what)[:220]}")
        L.append("")
    pa = res["patch_audit"]
    L += ["## Patches", "", "Implemented % counts APPLIED, ALREADY-UPSTREAM, RELOCATED and HAND-PORTED hunks over the hunks "
          "not explained away (OBSOLETE closed as such in the task record, SUPERSEDED by a later patch of the set, "
          "DROPPED-BY-MAINTAINER).", "",
          "| Patch | Hunks | Implemented | Verdict | Purpose (where stated) |", "|---|---|---|---|---|"]
    for p in pa["patches"]:
        L.append(f"| `{p['patch']}` | {len(p['hunks'])} | {p['implemented_pct']}% | {p['verdict']}"
                 + (" (maintainer decision " + p["decision"] + ")" if p.get("decision") else "") + ("" if not p["fail"] else " FAIL")
                 + f" | {_md(p['purpose'])[:160]} ({_md(p['purpose_source'])}) |")
    L += ["", "### Hunks not in the tree", ""]
    for p in pa["patches"]:
        for h in p["hunks"]:
            if h["status"] not in HUNK_OK:
                L.append(f"- `{p['patch']}` {h['file']} #{h['n']}: **{h['status']}**{'' if h['explained'] else ' (unexplained)'} - {_md(h['detail'])[:220]}")
    L += ["", "## New files", "", "| Group | File | Status |", "|---|---|---|"]
    L += [f"| {x['group']} | `{x['file']}` | {x['status']} |" for x in pa["new_files"]]
    pres = [x for x in pa["deleted"] if x["present"]]
    L += ["", "## Deleted files", "", f"{len(pa['deleted'])} files the patch set deletes; {len(pres)} still present."]
    L += [f"- `{x['file']}` ({x['group']}) is still in the tree" for x in pres]
    if pa["excluded"] or pa["disabled"]:
        L += ["", "## Not part of this build (by the patch policy)", ""]
        L += [f"- group {g}: {_md(norm(r))[:240]}" for g, r in pa["disabled"].items()]
        L += [f"- `{rel}`: {_md(r)[:240]}" for rel, r in pa["excluded"].items()]
    L += ["", "## How to re-run this", "",
          "Clone the public repository, port the patch set to the stated upstream commit, build and install, then run "
          "`fieldkit build-harness claims <task> --strict` from Fieldkit. Every number above is recomputed from the tree, "
          "the installed build and the claims register; nothing is carried over from an earlier run.", ""]
    return scrub("\n".join(L), res.get("roots") or {})


def scrub(text, roots):
    """Absolute roots (the owner repo, the tree, the install) -> short labels, longest first, both slash styles: the
    report is published, and a check's evidence string may carry a path."""
    for label, root in sorted(roots.items(), key=lambda kv: -len(kv[1] or "")):
        if not root:
            continue
        for form in {root, root.replace("\\", "/"), root.replace("/", "\\"), root.replace("\\", "\\\\")}:
            text = text.replace(form + "\\", "").replace(form + "/", "") if label == "<owner repo>" else text
            text = text.replace(form, label)
    return text


#: Public identifiers the e-mail rule of the privacy scan mistakes for addresses, and nothing else: an image's
#: device-pixel suffix (the about logo's "at 2x" PNG) and the add-on id of the bundled uBlock Origin, which Mozilla's add-on site
#: publishes. Only these exact shapes are exempt; every other finding blocks the report.
PUBLIC_IDS = re.compile(r"[\w.-]+@\d+(?:\.\d+)?x\.(?:png|jpe?g|svg|webp|gif|ico)\b|\buBlock0@raymondhill\.net\b|\b[\w.-]+@mozilla\.org\b")   # Mozilla add-on and theme ids (public identifiers, not addresses)


def privacy_findings(text):
    """fieldkit.core.privacy on exactly the text that will be written, with PUBLIC_IDS taken out first."""
    from ..core import privacy
    return privacy.scan_text(PUBLIC_IDS.sub("<public-id>", text), privacy.private_terms())


def write_report(res, path):
    """Privacy scan first, on the exact bytes to be written; a report with a finding is never written.
    -> (path or None, findings)."""
    text = report(res)
    findings = privacy_findings(text)
    if findings:
        return None, findings
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if privacy_findings(p.read_text(encoding="utf-8")):         # what landed on disk, read back
        p.unlink()
        return None, privacy_findings(text) or [{"kind": "not-scanned", "line": 0, "excerpt": "the written report differs"}]
    return p, []


def lines(res):
    t = res["totals"]
    out = [f"CLAIMS AUDIT {res.get('task') or ''} (tree {str(res['tree']['head'])[:12]}, BuildID {res['build_id']})",
           f"  claims {t['claims']}: PROVEN {t['PROVEN']}, UNPROVEN {t['UNPROVEN']}, CONTRADICTED {t['CONTRADICTED']}, STALE {t['STALE']}"
           + (f" ({res['new_claims']} new, seeded)" if res["new_claims"] else ""),
           f"  patches {t['patches']} ({t['hunks']} hunks): IMPLEMENTED {t['IMPLEMENTED']}, PARTIAL {t['PARTIAL']}, MISSING {t['MISSING']}, "
           f"OBSOLETE-EXPLAINED {t['OBSOLETE-EXPLAINED']}, DROPPED {t['DROPPED-BY-MAINTAINER']}; failing {t['patches_failing']}",
           f"  hunks: {', '.join(f'{k} {v}' for k, v in t['hunk_status'].items())}",
           f"  new files {t['new_files']} ({t['new_files_failing']} missing/different); deleted {t['deleted']} ({t['deleted_present']} still present)"]
    out += [f"  REGISTER PROBLEM: {p}" for p in res["problems"]]
    out += ["  worst gaps:"] + [f"   {i:2}. {g}" for i, g in enumerate(worst_gaps(res), 1)]
    if res.get("report"):
        out.append(f"  report: {res['report']}")
    for f in res.get("privacy", []):
        out.append(f"  PRIVACY: {f['kind']} line {f['line']}: {f['excerpt']}")
    out.append("CLAIMS " + ("OK" if res["ok"] else "NOT OK") + (" (strict: every claim proven, every patch in place or explained)" if res["strict"] else ""))
    return out


def run(task_id, install_dir=None, report_path=None, strict=False, write=True):
    """The command: the task's tree and record, the owner repo, the installed build."""
    from . import buildrun, task
    t = task.load(task_id)
    owner = buildrun._owner_root(t)
    if not owner:
        raise task.Refused("no owner repository beside this task: no patch set, no claims")
    version = (t.get("meta", {}).get("upstream") or {}).get("version")
    res = audit(owner, t["workdir"], install_dir, t["steps"], journal=task.STATE / task_id / "journal.jsonl", strict=strict,
                write_register=write, task_id=task_id, version=version)
    res["report"], res["privacy"] = None, []
    if write:
        path = Path(report_path) if report_path else Path(owner) / "claims" / f"AUDIT-{(version or 'x').split('.')[0]}.md"
        p, findings = write_report(res, path)
        reg = Path(owner) / REGISTER
        reg_findings = privacy_findings(reg.read_text(encoding="utf-8")) if reg.is_file() else []
        res["report"], res["privacy"] = (str(p) if p else None), findings + [{**f, "kind": "register " + f["kind"]} for f in reg_findings]
        if res["privacy"]:
            res["ok"] = res["strict_ok"] = res["lenient_ok"] = False
    return res


def proof_row(t, install_dir, strict=False):
    """The post-install row (read-only: the register and the report are not written)."""
    from . import buildrun, task
    name = "claims: what the public repository claims is proven by this tree and this build"
    owner = buildrun._owner_root(t) if t.get("meta", {}).get("harness_root") else None
    if not owner:
        return {"check": name, "ok": False, "evidence": "no owner repository known for this task: unchecked is not passed"}
    try:
        res = audit(owner, t["workdir"], install_dir, t.get("steps", []), journal=task.STATE / t["id"] / "journal.jsonl",
                    strict=strict, write_register=False, task_id=t["id"])
    except (OSError, ValueError, KeyError) as e:
        return {"check": name, "ok": False, "evidence": f"the audit could not run: {e}"}
    x = res["totals"]
    return {"check": name, "ok": res["ok"],
            "evidence": f"claims {x['claims']}: proven {x['PROVEN']}, unproven {x['UNPROVEN']}, contradicted {x['CONTRADICTED']}, "
                        f"stale {x['STALE']}; patches failing {x['patches_failing']} of {x['patches']}",
            "bad": worst_gaps(res, 10)}
