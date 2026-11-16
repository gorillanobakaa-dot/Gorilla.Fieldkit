"""`migrate preflight`: one verdict before a migration compiles (P29, owner order 2026-11-16).

Firefox 160 compiled for hours, then failed at run time or in the install on causes a reading of the tree would have
shown first. Five checks, one verdict, one NEXT:

  static      the patched tree against its pristine base (differential: only what the patches broke is named)
                LAZY          a lazy module getter whose module the patch set removed (GenAI.sys.mjs, 160)
                SETTING       a Setting id a preferences page uses that no addSetting registers (linkPreviewEnabled)
                FLUENT        an l10nId / data-l10n-id no .ftl defines (highlight-to-search-settings-*)
                SUPPORT-LINK  app.support.baseURL empty while formatUTMParams calls `new URL` unguarded
  policy      patch_policy.json against itself: a patch only the other system builds, selected on this one, must be
              left out (exclude_on) or carried on purpose (carry_on.PLATFORM.PATCH: "reason"); a reason text naming
              it without either is a CONTRADICTION (0122-widget_windows_moz_build.patch on linux, 160)
  set-proof   pristine + each platform's selection applies (buildh/setproof; needs the vault copy, else NOT RUN)
  stage-key   the recipe's source stage is keyed on the patch set (emerge lint STALE-KEY)
  stall       the child-stall guard is armed (core/childrun used by replay, snapshot and task)

Read-only: git object reads of the source tree (`git --no-optional-locks`), the policy file and the recipe.
"""
import json
import re
import subprocess
from pathlib import Path

CHECKS = ("static", "policy", "set-proof", "stage-key", "stall")
STATIC = ("LAZY", "SETTING", "FLUENT", "SUPPORT-LINK")
PREFS = "browser/components/preferences"
PREF_FILES = ("browser/app/profile/firefox.js", "modules/libpref/init/all.js")
SUPPORT_LINK = "toolkit/content/widgets/moz-support-link/moz-support-link.mjs"

_LAZY = re.compile(r"(\w+):\s*\n?\s*\"((?:resource|moz-src|chrome):[^\"]+)\"")
_ADD_SETTING = re.compile(r"addSetting\(\s*(?:/\*.*?\*/\s*)?\(?\s*\{\s*id:\s*\"(\w+)\"", re.S)
_USED_ID = re.compile(r"\{\s*id:\s*\"([\w-]+)\"")
_L10N = re.compile(r"(?:l10nId:\s*|data-l10n-id=)[\"']([a-zA-Z][\w-]*)[\"']")
_FTL_ID = re.compile(r"^([a-zA-Z][\w-]*)\s*=", re.M)
_BASE_EMPTY = re.compile(r"pref\(\s*\"app\.support\.baseURL\"\s*,\s*\"\"")


class Tree:
    """One commit of a git tree, read through git objects only (nothing checked out, nothing locked)."""

    def __init__(self, repo, rev):
        self.repo, self.rev = str(repo), rev
        self._ls = None

    def git(self, *a, timeout=600):
        r = subprocess.run(["git", "--no-optional-locks", "-C", self.repo, *a], capture_output=True, timeout=timeout)
        if r.returncode:
            raise RuntimeError(f"git {' '.join(a[:3])}: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
        return r.stdout.decode("utf-8", "replace")

    def ls(self, prefix=None):
        if prefix:
            return [x for x in self.git("ls-tree", "-r", "--name-only", self.rev, "--", prefix).splitlines() if x]
        if self._ls is None:
            self._ls = set(self.git("ls-tree", "-r", "--name-only", self.rev).splitlines())
        return self._ls

    def read(self, paths):
        """-> {path: text} for the paths that exist at this commit (one git cat-file --batch)."""
        paths = list(paths)
        if not paths:
            return {}
        inp = "".join(f"{self.rev}:{p}\n" for p in paths).encode("utf-8")
        r = subprocess.run(["git", "--no-optional-locks", "-C", self.repo, "cat-file", "--batch"], input=inp,
                           capture_output=True, timeout=600)
        out, buf, i = {}, r.stdout, 0
        for p in paths:
            nl = buf.index(b"\n", i)
            head = buf[i:nl].decode("utf-8", "replace").split()
            i = nl + 1
            if len(head) < 3 or head[-1] == "missing":
                continue
            n = int(head[2])
            out[p] = buf[i:i + n].decode("utf-8", "replace")
            i += n + 1
        return out


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JS_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_JS_LINE = re.compile(r"^\s*//.*$", re.M)
_DIRS_ENTRY = re.compile(r"^[^#\n]*\"([\w.-]+)\"", re.M)


def uncomment(text):
    """The text without HTML, block and whole-line // comments: a commented-out element is not shipped."""
    return _JS_LINE.sub("", _JS_BLOCK.sub("", _HTML_COMMENT.sub("", text)))


class Excised:
    """Is a path under a directory the patch set dropped from its parent's moz.build (built at base, not at rev)?
    Such a file stays in the tree but is never built or packaged, so what it references is not a run-time defect."""

    def __init__(self, base, rev):
        self.base, self.rev, self._memo = base, rev, {}

    def _listed(self, tree, parent):
        text = tree.read([f"{parent}/moz.build" if parent else "moz.build"])
        return {m for t in text.values() for m in _DIRS_ENTRY.findall(t)} if text else None

    def __call__(self, path):
        parts = path.split("/")[:-1]
        for i in range(1, len(parts)):
            parent, child = "/".join(parts[:i]), parts[i]
            key = (parent, child)
            if key not in self._memo:
                was, now = self._listed(self.base, parent), self._listed(self.rev, parent)
                self._memo[key] = bool(was and child in was and now is not None and child not in now)
            if self._memo[key]:
                return f"{parent}/{child}"
        return None


def changed(base, rev):
    """-> ({path at rev changed since base}, {path deleted since base})."""
    st = rev.git("diff", "--name-status", "--no-renames", base.rev, rev.rev)
    ch, gone = set(), set()
    for line in st.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            (gone if parts[0].startswith("D") else ch).add(parts[-1])
    return ch, gone


def _module_path(uri):
    if uri.startswith("moz-src:///"):
        return uri[len("moz-src:///"):]
    return None


def _getters(text):
    """-> [(symbol, uri)] declared in defineESModuleGetters(...) or declareLazy(...) blocks."""
    out = []
    for m in re.finditer(r"(?:defineESModuleGetters|declareLazy)\(", text):
        end = text.find("})", m.end())
        out += _LAZY.findall(text[m.end():end if end > 0 else len(text)])
    return out


def check_lazy(base, rev, ch, gone, excised=None):
    """A lazy module getter, used in a changed script, whose module the patch set removed (moz-src path gone, or
    the module file deleted and no file of that name left). A getter nobody reads never loads: not a defect."""
    out = []
    excised = excised or (lambda p: None)
    scripts = [p for p in ch if p.endswith((".mjs", ".js", ".jsm")) and not excised(p)]
    gone_names = {Path(p).name for p in gone}
    names_now = None
    for path, text in sorted(rev.read(scripts).items()):
        text = uncomment(text)
        for sym, uri in _getters(text):
            if not re.search(r"\.\s*" + re.escape(sym) + r"\b", text):
                continue
            mp = _module_path(uri)
            if mp:
                if mp not in base.ls():                                # already missing upstream
                    continue
                if mp in rev.ls() and not excised(mp):                 # present and still built
                    continue
            else:
                name = uri.rsplit("/", 1)[-1]
                if name not in gone_names:
                    continue
                if names_now is None:
                    names_now = {Path(x).name for x in rev.ls()}
                if name in names_now:
                    continue
            out.append(f"{path}: lazy.{sym} is used, but its module {uri} was removed by the patch set")
    return out


def _settings(tree, files):
    texts = tree.read(files)
    reg, used = set(), set()
    for text in texts.values():
        reg |= set(_ADD_SETTING.findall(uncomment(text)))
    for p, text in texts.items():
        if p.endswith((".mjs", ".js")) and "/config/" in p:
            text = uncomment(text)
            for m in _USED_ID.finditer(text):
                tail = re.split(r"\bid:|[\[{]", text[m.end():m.end() + 300], maxsplit=1)[0]
                if not re.search(r"\bvalue\s*:", tail):          # a radio/select option, not a Setting
                    used.add(m.group(1))
    return reg, used


def check_setting(base, rev, ch):
    """A Setting id a preferences config uses that nothing registers, and that upstream did register."""
    if not any(p.startswith(PREFS + "/") for p in ch):
        return []
    reg_r, used_r = _settings(rev, [p for p in rev.ls(PREFS) if p.endswith((".mjs", ".js"))])
    reg_b, used_b = _settings(base, [p for p in base.ls(PREFS) if p.endswith((".mjs", ".js"))])
    upstream_unreg = used_b - reg_b
    return [f"{PREFS}: Setting \"{i}\" is used but no addSetting registers it"
            for i in sorted(used_r - reg_r - upstream_unreg)]


def _ftl_ids(tree):
    ftl = [p for p in tree.ls() if p.endswith(".ftl") and "/en-US/" in p or p.endswith(".ftl") and "/locales/" not in p]
    ids = set()
    for text in tree.read(ftl).values():
        ids |= set(_FTL_ID.findall(text))
    return ids


def check_fluent(base, rev, ch, excised=None):
    """An l10n id a changed, built file uses (outside comments) that no .ftl defines, minus the ids already
    missing upstream."""
    excised = excised or (lambda p: None)
    files = [p for p in ch if p.endswith((".mjs", ".js", ".xhtml", ".html")) and not excised(p)]
    used_r = {p: set(_L10N.findall(uncomment(t))) for p, t in rev.read(files).items()}
    if not any(used_r.values()):
        return []
    have_r = _ftl_ids(rev)
    missing = {(p, i) for p, ids in used_r.items() for i in ids if i not in have_r}
    if not missing:
        return []
    have_b = _ftl_ids(base)
    used_b = {p: set(_L10N.findall(uncomment(t))) for p, t in base.read(sorted({p for p, _ in missing})).items()}
    upstream = {(p, i) for p, ids in used_b.items() for i in ids if i not in have_b}
    return [f"{p}: data-l10n-id \"{i}\" is defined in no .ftl" for p, i in sorted(missing - upstream)]


_FTL_LINK = re.compile(r"<link\s+rel=\"localization\"\s+href=\"([^\"]+\.ftl)\"")


def check_fluent_unlinked(base, rev, ch, excised=None):
    """An l10n id a changed file still uses whose only .ftl the patch set unlinked from the document that shows
    that file (a <link rel=\"localization\"> live at base, gone or commented out at rev): the id renders blank."""
    excised = excised or (lambda p: None)
    docs = [p for p in ch if p.endswith(".xhtml") and not excised(p)]
    rb, rr = base.read(docs), rev.read(docs)
    out = []
    for doc in sorted(rr):
        dropped = set(_FTL_LINK.findall(uncomment(rb.get(doc, "")))) - set(_FTL_LINK.findall(uncomment(rr[doc])))
        if not dropped:
            continue
        kept = set(_FTL_LINK.findall(uncomment(rr[doc])))
        ftls = [p for p in rev.ls() if p.endswith(".ftl") and "/en-US/" in p]
        def ids_of(hrefs):
            files = [p for p in ftls if any(p.endswith("/en-US/" + h) for h in hrefs)]
            return {i for t in rev.read(files).values() for i in _FTL_ID.findall(t)}
        lost = ids_of(dropped) - ids_of(kept)
        home = doc.rsplit("/", 1)[0] + "/"
        users = [p for p in ch if p.startswith(home) and p.endswith((".mjs", ".js", ".xhtml", ".html"))
                 and not excised(p)]
        for p, t in sorted(rev.read(users).items()):
            for i in sorted(set(_L10N.findall(uncomment(t))) & lost):
                out.append(f"{p}: data-l10n-id \"{i}\" lives only in {', '.join(sorted(dropped))}, "
                           f"which {doc} no longer links")
    return out


def check_support_link(rev):
    """app.support.baseURL empty (a relative support link is no URL) while formatUTMParams parses it unguarded."""
    prefs = rev.read(PREF_FILES)
    empty = [p for p, t in prefs.items() if _BASE_EMPTY.search(t)]
    if not empty:
        return []
    text = rev.read([SUPPORT_LINK]).get(SUPPORT_LINK, "")
    i = text.find("function formatUTMParams")
    if i < 0:
        return []
    body = text[i:]
    end = re.search(r"\n}\s*\n", body)
    body = body[:end.end()] if end else body
    j = body.find("new URL(")
    if j < 0 or "try" in body[:j]:
        return []
    return [f"{empty[0]} sets app.support.baseURL to \"\", and {SUPPORT_LINK} formatUTMParams calls new URL() "
            f"without a try: every support link with utm-content throws"]


def static(repo, rev="HEAD", base_rev=None):
    """-> {check: [finding]} for the four static checks of the patched tree `rev` against `base_rev` (default: the
    tree's root commit, the pristine release the harness checkpoints on)."""
    r = Tree(repo, rev)
    if not base_rev:
        roots = r.git("rev-list", "--max-parents=0", rev).split()
        base_rev = roots[-1]
    b = Tree(repo, base_rev)
    ch, gone = changed(b, r)
    ex = Excised(b, r)
    return {"base": base_rev, "rev": r.git("rev-parse", rev).strip(), "changed": len(ch) + len(gone),
            "LAZY": check_lazy(b, r, ch, gone, ex), "SETTING": check_setting(b, r, ch),
            "FLUENT": check_fluent(b, r, ch, ex) + check_fluent_unlinked(b, r, ch, ex),
            "SUPPORT-LINK": check_support_link(r)}


# --- policy ----------------------------------------------------------------------------------------------------------

def policy_check(firefox_root, platforms=None):
    """-> [(kind, platform, group/patch, message)] for patch_policy.json against itself.

    kind: WAIVED (carried on purpose, with its reason), SELECTS-FOREIGN (a patch only the other system builds, selected
    here, declared nowhere), CONTRADICTION (a reason text names it, yet neither exclude_on nor carry_on declares it),
    STALE-WAIVER (a carry_on entry for a patch that is absent, not foreign, or has no reason). Only WAIVED passes."""
    from ..buildh import firefox, setproof
    pol = json.loads((Path(firefox_root) / "config" / "patch_policy.json").read_text(encoding="utf-8"))
    pset = Path(firefox_root) / pol["patchset_root"]
    out = []
    for plat in platforms or setproof.PLATFORMS:
        groups = firefox.for_platform(pol["groups"], plat)
        for g, spec in groups.items():
            raw = pol["groups"][g]
            waivers = dict((raw.get("carry_on") or {}).get(plat) or {})
            if spec.get("status") != "enabled":
                for name in waivers:
                    out.append(("STALE-WAIVER", plat, f"{g}/{name}", f"carry_on.{plat} on a group {plat} does not apply"))
                continue
            ex = set(spec.get("exclude", []))
            seen = set()
            talk = " ".join(str(raw.get(k) or "") for k in ("reason", "exclude_on_reason"))
            for pf in sorted((pset / g).rglob("*.patch")):
                seen.add(pf.name)
                if pf.name in ex:
                    continue
                files = {b for _a, b in setproof._SECTION.findall(pf.read_text(encoding="utf-8", errors="replace"))}
                other = setproof.foreign_only(files, plat)
                why = str(waivers.get(pf.name) or "").strip()
                if not other:
                    if pf.name in waivers:
                        out.append(("STALE-WAIVER", plat, f"{g}/{pf.name}", f"carry_on.{plat} for a patch {plat} builds: "
                                    "drop the waiver"))
                    continue
                if why:
                    out.append(("WAIVED", plat, f"{g}/{pf.name}", f"writes only {other} files, carried on {plat}: {why}"))
                elif pf.name in waivers:
                    out.append(("STALE-WAIVER", plat, f"{g}/{pf.name}", f"carry_on.{plat} with no reason"))
                elif pf.name in talk:
                    out.append(("CONTRADICTION", plat, f"{g}/{pf.name}",
                                f"writes only {other} files; the group's reason names it, yet neither exclude_on.{plat} "
                                f"nor carry_on.{plat} declares it -> add carry_on.{plat}.\"{pf.name}\": \"<reason>\" "
                                f"to group {g} (or exclude_on.{plat})"))
                else:
                    out.append(("SELECTS-FOREIGN", plat, f"{g}/{pf.name}",
                                f"writes only {other} files, yet {plat} selects it -> exclude_on.{plat}, or "
                                f"carry_on.{plat} with a reason"))
            for name in sorted(set(waivers) - seen):
                out.append(("STALE-WAIVER", plat, f"{g}/{name}", f"carry_on.{plat} for a patch not in the group"))
    return out


# --- the other checks ------------------------------------------------------------------------------------------------

def stage_key(recipe="firefox-windows"):
    """-> the emerge lint STALE-KEY findings of the recipe (the source stage keyed without the patch set)."""
    import yaml
    from ..emerge import lint
    spec = yaml.safe_load((lint.RECIPES / f"{recipe}.yaml").read_text(encoding="utf-8"))
    return [f"{w}: {m}" for k, w, m in lint.lint_spec(recipe, spec) if k == "STALE-KEY"]


def stall_armed(here=None):
    """-> [finding]: the child-stall guard (core/childrun, PR 574) exists and the patch children run under it."""
    here = Path(here) if here else Path(__file__).resolve().parents[1]
    if not (here / "core" / "childrun.py").is_file():
        return ["fieldkit/core/childrun.py is missing: a stalled patch child (replay, snapshot, task) hangs the run"]
    out = []
    for rel in ("buildh/replay.py", "buildh/snapshot.py", "buildh/task.py"):
        p = here / rel
        if not p.is_file() or "childrun.run" not in p.read_text(encoding="utf-8", errors="replace"):
            out.append(f"fieldkit/{rel} runs its child without childrun.run: a stall there is not caught")
    return out


def set_proof(firefox_root, pristine, platforms=None):
    """-> (status, [lines]) for setproof.prove on each platform; NOT RUN when the pristine checkout is absent."""
    from ..buildh import setproof
    if not pristine or not (Path(pristine) / ".git").exists():
        return "NOT RUN", [f"no pristine checkout at {pristine} (build-harness vault fetch firefox)"]
    ok, out = True, []
    for p in platforms or setproof.PLATFORMS:
        r = setproof.prove(Path(firefox_root), Path(pristine), p, say=lambda m: None)
        ok &= r["ok"]
        out += setproof.lines(r)
    return ("PASS" if ok else "FAIL"), out


def _pristine_for(version):
    from ..buildh import vault
    try:
        vs = [r["version"] for r in vault.listing() if r["product"] == "firefox"]
    except Exception:  # noqa: BLE001  no vault configured
        return None
    if version:
        vs = [v for v in vs if v == version or v.startswith(version + ".")]
    if not vs:
        return None
    v = sorted(vs, key=lambda x: [int(n) if n.isdigit() else 0 for n in x.split(".")])[-1]
    return vault.root() / "firefox" / v


def run(firefox_root, src=None, rev="HEAD", base_rev=None, pristine=None, recipe="firefox-windows",
        skip=(), platforms=None):
    """-> {verdict, checks: [{check, status, lines}], next}. verdict FAIL when a check fails; INCOMPLETE when every
    check that ran passed but one could not run or was skipped; PASS only when all five ran and passed."""
    firefox_root = Path(firefox_root)
    src = Path(src) if src else firefox_root / "src"
    checks = []

    def add(name, status, lines):
        checks.append({"check": name, "status": status, "lines": list(lines)})

    if "static" in skip:
        add("static", "SKIPPED", ["skipped on request"])
    else:
        try:
            s = static(src, rev, base_rev)
            lines = [f"{k}: {len(s[k])} finding(s)" for k in STATIC]
            lines += [f"  {k} {f}" for k in STATIC for f in s[k]]
            bad = any(s[k] for k in STATIC)
            add("static", "FAIL" if bad else "PASS",
                [f"{s['rev'][:10]} against pristine {s['base'][:10]}, {s['changed']} changed file(s)"] + lines)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            add("static", "NOT RUN", [f"{src}: {e}"])
    if "policy" in skip:
        add("policy", "SKIPPED", ["skipped on request"])
    else:
        try:
            pc = policy_check(firefox_root)                  # every system: cheap, and a foreign patch is a
                                                             # Linux problem caught on Windows (0122)
            bad = [x for x in pc if x[0] != "WAIVED"]
            add("policy", "FAIL" if bad else "PASS", [f"{k} {p} {w}: {m}" for k, p, w, m in pc] or
                ["no patch selected on a system that does not build it"])
        except (OSError, ValueError, KeyError) as e:
            add("policy", "NOT RUN", [f"{firefox_root / 'config' / 'patch_policy.json'}: {e}"])
    if "set-proof" in skip:
        add("set-proof", "SKIPPED", ["skipped on request"])
    else:
        st, lines = set_proof(firefox_root, pristine, platforms)
        add("set-proof", st, lines)
    if "stage-key" in skip:
        add("stage-key", "SKIPPED", ["skipped on request"])
    else:
        sk = stage_key(recipe)
        add("stage-key", "FAIL" if sk else "PASS", sk or [f"{recipe}: the source stage is keyed on the patch set"])
    if "stall" in skip:
        add("stall", "SKIPPED", ["skipped on request"])
    else:
        sa = stall_armed()
        add("stall", "FAIL" if sa else "PASS", sa or ["childrun guards the replay, snapshot and task patch children"])
    fails = [c["check"] for c in checks if c["status"] == "FAIL"]
    gaps = [c["check"] for c in checks if c["status"] in ("NOT RUN", "SKIPPED")]
    if fails:
        verdict = f"FAIL ({', '.join(fails)})"
        nxt = f"fix what the {fails[0]} lines name, then run migrate preflight again; do not compile before it passes"
    elif gaps:
        verdict = f"INCOMPLETE ({', '.join(gaps)} not run)"
        nxt = "every check that ran passed; run the missing ones (a pristine for set-proof) before trusting it"
    else:
        verdict = "PASS"
        nxt = "compile (fieldkit emerge run " + recipe + ")"
    return {"verdict": verdict, "ok": not fails, "checks": checks, "next": nxt}


def lines(r):
    out = []
    for c in r["checks"]:
        out.append(f"PREFLIGHT {c['check']}: {c['status']}")
        out += [f"  {x}" for x in c["lines"]]
    out.append(f"PREFLIGHT VERDICT: {r['verdict']}")
    out.append(f"NEXT: {r['next']}")
    return out
