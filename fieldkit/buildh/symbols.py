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
    return re.search(r"(?:^|[\s{,;])(?:export\s+const\s+|static\s+|get\s+)?" + re.escape(member) + r"\s*[:=(]", module_text) is not None


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


def problems(workdir, files):
    """-> ["rel: X.MEMBER (module)"] over the changed .mjs/.js files."""
    out, cache = [], {}
    for rel in files:
        if not rel.endswith((".mjs", ".js")) or "/test" in rel or not (Path(workdir) / rel).is_file():
            continue
        for m in missing_members(workdir, rel, cache):
            out.append(f"{rel}: {m}")
    return out
