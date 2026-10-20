"""Members of imported modules that the imported module no longer defines.

The quietest port failure of 2026-10-02: the owner's 155.0.1 urlbar providers read `UrlbarUtils.RESULT_SOURCE.SEARCH`;
157 moved RESULT_SOURCE (and RESULT_TYPE, PROVIDER_TYPE, ...) to UrlbarShared. The file parses, the build is green,
the lazy getter exists, and every keystroke throws `(intermediate value).RESULT_SOURCE is undefined` so Enter does
nothing. Only a check that follows the import and looks for the member catches it before a browser runs.

Scope, on purpose narrow (a check that cries wolf gets deleted, see the owner's playbook):
  * only files the port changed;
  * only UPPER_CASE members (`X.RESULT_SOURCE`, `lazy.Y.PROVIDER_TYPE`): the constant tables that upstream moves
    between modules; methods and fields are not judged;
  * only when the import resolves to one file in the tree (`import { X } from "..."`, `const { X } = ChromeUtils.importESModule("...")`,
    `ChromeUtils.defineESModuleGetters(lazy, { X: "..." })`) and that file is readable;
  * a member counts as defined when the module has `MEMBER:` / `MEMBER =` / `export const MEMBER` / `static MEMBER` / `get MEMBER()`.
"""
import re
from pathlib import Path

IMPORT_NAMED = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"([^"]+)"')
IMPORT_ESM = re.compile(r'(?:const|let|var)\s*\{([^}]*)\}\s*=\s*ChromeUtils\.importESModule\(\s*"([^"]+)"')
LAZY_BLOCK = re.compile(r"ChromeUtils\.defineESModuleGetters\(\s*lazy\s*,\s*\{(.*?)\}\s*\)", re.S)
LAZY_ENTRY = re.compile(r'(\w+)\s*:\s*"([^"]+)"')
MEMBER = re.compile(r"\b(?:lazy\.)?([A-Z]\w+)\.([A-Z][A-Z0-9_]{2,})\b")
# a `//` after a colon is a URL ("moz-src:///x"), not a comment: only whole-line and whitespace/punctuation-led ones go
COMMENT = re.compile(r"(?m)^\s*//[^\n]*|(?<=[\s;{}(),])//[^\n]*|/\*.*?\*/", re.S)


def module_path(workdir, uri):
    """A moz-src:///, resource:///, resource://gre/ or chrome://browser/content/ URI -> Path in the tree, or None."""
    w = Path(workdir)
    if uri.startswith("moz-src:///"):
        p = w / uri[len("moz-src:///"):]
        return p if p.is_file() else None
    if uri.startswith("chrome://browser/content/"):
        rest = uri[len("chrome://browser/content/"):]
        for base in ("browser/components", "browser/base/content"):
            for cand in (w / base / rest, w / base / rest.replace("/", "/content/", 1)):
                if cand.is_file():
                    return cand
        return None
    if uri.startswith("resource:///modules/"):
        p = w / "browser/modules" / uri[len("resource:///modules/"):]
        return p if p.is_file() else None
    if uri.startswith("resource://gre/modules/"):
        name = uri[len("resource://gre/modules/"):]
        for base in ("toolkit/modules", "toolkit/components", "dom", "netwerk", "security/manager/ssl"):
            hits = list((w / base).rglob(Path(name).name)) if (w / base).is_dir() else []
            if len(hits) == 1:
                return hits[0]
        return None
    return None


def imports(text):
    """-> {local name: uri} for the import shapes this check follows."""
    out = {}
    for names, uri in IMPORT_NAMED.findall(text) + IMPORT_ESM.findall(text):
        for n in names.split(","):
            n = n.strip().split(" as ")[-1].strip()
            if n:
                out[n] = uri
    for block in LAZY_BLOCK.findall(text):
        for n, uri in LAZY_ENTRY.findall(block):
            out[n] = uri
    return out


def defines(module_text, member):
    m = re.escape(member)
    if re.search(r"(?:^|[\s{,;])(?:export\s+const\s+|static\s+|get\s+)?" + m + r"\s*[:=(]", module_text):
        return True
    # ChromeUtils.defineLazyGetter(Obj, "M", ...), Object.defineProperty(Obj, "M", ...), Obj.M = ... (UpdateUtils.ABI, 2026-10-02)
    return re.search(r"(?:defineLazyGetter|defineProperty|defineLazyPreferenceGetter)\(\s*[\w.]+\s*,\s*[\"']" + m + r"[\"']"
                     r"|\b\w+\." + m + r"\s*=[^=]", module_text) is not None


def missing_members(workdir, rel, _cache=None):
    """-> ["X.MEMBER (module.sys.mjs)"] for members the imported module does not define."""
    cache = _cache if _cache is not None else {}
    p = Path(workdir) / rel
    text = COMMENT.sub("", p.read_text(encoding="utf-8", errors="replace"))
    imp = imports(text)
    out = []
    for ident, member in sorted(set(MEMBER.findall(text))):
        uri = imp.get(ident)
        if not uri:
            continue
        mp = module_path(workdir, uri)
        if mp is None:
            continue
        if mp not in cache:
            cache[mp] = COMMENT.sub("", mp.read_text(encoding="utf-8", errors="replace"))
        if not defines(cache[mp], member):
            out.append(f"{ident}.{member} ({mp.name})")
    return out


LAZY_USE = re.compile(r"\blazy\.([A-Za-z_]\w*)")
# every way a module puts a name on its `lazy` object
LAZY_DECL = (
    re.compile(r"define(?:Lazy)?\w*Getters?\(\s*lazy\s*,\s*\{(.*?)\}\s*[,)]", re.S),        # block forms: names are keys
    re.compile(r"define\w*Getter\(\s*lazy\s*,\s*[\"'](\w+)[\"']"),                          # defineLazyGetter(lazy, "x")
    re.compile(r"\blazy\.(\w+)\s*=[^=]"),                                                   # lazy.x = ...
    re.compile(r"Object\.defineProperty\(\s*lazy\s*,\s*[\"'](\w+)[\"']"),
)
_KEY = re.compile(r"(?:^|[,{\s])([A-Za-z_]\w*)\s*:", re.M)


def undeclared_lazy(text):
    """-> sorted names used as `lazy.X` that the same module never declares. 2026-10-16: UITour.sys.mjs kept two
    `lazy.AIWindow` uses after the 158 rebase removed the getter with the Smart Window; the gate's member check only
    looked at UPPER_CASE members and passed, and the owner's preflight found it at build time."""
    if not re.search(r"\b(?:const|let|var)\s+lazy\s*=", text):
        return []
    declared = set()
    for rx in LAZY_DECL:
        for m in rx.findall(text):
            declared |= set(_KEY.findall(m)) if rx is LAZY_DECL[0] else {m}
    return sorted(set(LAZY_USE.findall(text)) - declared)


def problems(workdir, files, pristine=None):
    """-> ["rel: X.MEMBER (module)" | "rel: lazy.X is never declared"] over the changed .mjs/.js files.
    `pristine(rel)` -> upstream text or None: a lazy name counts only when the port made it undeclared (upstream
    declares lazies in shapes this module does not model, e.g. XPCOMUtils.declareLazy; those look the same in
    pristine, so the delta is the port's own excision leftovers). Without `pristine` lazy names are not judged."""
    out, cache = [], {}
    for rel in files:
        if not rel.endswith((".mjs", ".js")) or "/test" in rel or not (Path(workdir) / rel).is_file():
            continue
        for m in missing_members(workdir, rel, cache):
            out.append(f"{rel}: {m}")
        if pristine is None:
            continue
        text = COMMENT.sub("", (Path(workdir) / rel).read_text(encoding="utf-8", errors="replace"))
        before = pristine(rel)
        was = set(undeclared_lazy(COMMENT.sub("", before))) if before else set()
        for n in undeclared_lazy(text):
            if n not in was:
                out.append(f"{rel}: lazy.{n} is never declared in this module (undefined when the line runs)")
    return out
