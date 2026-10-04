"""chrome:// URL -> the file in the source tree that ships at that URL, read from the tree's jar.mn manifests.

A CSS rule names its picture by chrome URL (chrome://branding/content/about-logo.png); the static layer has to
measure the file behind it. jar.mn says where every packaged file comes from:

    browser.jar:
    % content branding %content/branding/ contentaccessible=yes      <- chrome://branding/content/ -> jar path
      content/branding/icon16.png    (../default16.png)              <- jar path <- source (relative to jar.mn)
      content/branding/about.png                                     <- no source: the target's basename

`#include` is inlined (sources stay relative to the including jar.mn), `#if`/`#ifdef` are evaluated for a Windows
desktop build (unknown symbols count as true), `(dir/*.svg)` wildcards are expanded, `% override` is applied.
`% resource <name> %<jar path>/` lines map resource://<name>/... through the same file map (2026-10-04: before that
every resource: URL was reported "external" and never measured, so a missing file behind one was never found).
A resource: package that no jar.mn registers (registered at run time, or by the GRE) stays "external".
Only the active branding directory and the Windows themes are indexed: the tree also carries Mozilla's nightly,
official, aurora and unofficial branding and the Linux/macOS themes, which register the same URLs.
"""
import fnmatch
import re
from pathlib import Path

WINDOWS_TRUE = {"XP_WIN", "MOZ_WIDGET_TOOLKIT", "MOZ_UPDATER", "MOZ_CRASHREPORTER", "MOZILLA_OFFICIAL"}
FALSE = {"XP_MACOSX", "XP_LINUX", "MOZ_WIDGET_GTK", "ANDROID", "MOZ_GECKOVIEW", "XP_IOS", "ENABLE_TESTS",
         "MOZ_CODE_COVERAGE", "MOZ_LAYOUT_DEBUGGER", "NIGHTLY_BUILD", "IOS_MARKETPLACE_AB_CD",
         "ANDROID_MARKETPLACE_AB_CD", "MOZ_GLEAN_ANDROID"}
SKIP_PARTS = ("/test/", "/tests/", "mobile/", "python/", "testing/", "/themes/linux/", "/themes/osx/",
              "toolkit/themes/linux/", "toolkit/themes/osx/", "toolkit/themes/mobile/", "/gtest/", "layout/tools/")
ENTRY = re.compile(r"^\s*[*+]?\s*(\S+)(?:\s+\((\S+)\))?\s*$")
JAR = re.compile(r"^([\w.-]+\.jar):\s*$")


def _truth(expr):
    expr = expr.strip()
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expr)
    vals = {n: (False if n in FALSE else True) for n in names if n != "defined"}
    py = re.sub(r"defined\s*\(\s*(\w+)\s*\)", r"\1", expr).replace("||", " or ").replace("&&", " and ")
    py = re.sub(r"!(?!=)", " not ", py)
    try:
        return bool(eval(py, {"__builtins__": {}}, vals))   # noqa: S307 - names only, no builtins
    except Exception:
        return True


def _lines(path, seen=None):
    """jar.mn text with #include inlined and #if blocks resolved. -> [(line, base dir for sources)]"""
    seen = seen or set()
    path = Path(path)
    if path in seen or not path.is_file():
        return []
    seen.add(path)
    out, stack = [], []
    base = path.parent
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = raw.strip()
        if s.startswith("#"):
            word = s.split(None, 1)
            d, rest = word[0], (word[1] if len(word) > 1 else "")
            if d == "#ifdef":
                stack.append([_truth(rest), _truth(rest)])
            elif d == "#ifndef":
                stack.append([not _truth(rest), not _truth(rest)])
            elif d == "#if":
                stack.append([_truth(rest), _truth(rest)])
            elif d == "#elif" and stack:
                taken = stack[-1][1]
                stack[-1][0] = (not taken) and _truth(rest)
                stack[-1][1] = taken or stack[-1][0]
            elif d == "#else" and stack:
                stack[-1][0] = not stack[-1][1]
            elif d == "#endif" and stack:
                stack.pop()
            elif d == "#include" and all(x[0] for x in stack):
                inc = (base / rest.strip()).resolve()
                out += [(l, base) for l, _ in _lines(inc, seen)]     # inlined: sources stay relative to us
            continue
        if all(x[0] for x in stack) and s:
            out.append((raw, base))
    return out


def _tracked(tree, name):
    """Relative paths of every file called `name` (git ls-files when the tree is a repository: a full walk of a
    Firefox tree takes minutes)."""
    import subprocess
    tree = Path(tree)
    if (tree / ".git").exists():
        r = subprocess.run(["git", "-C", str(tree), "ls-files", "-z", f"*{name}"], capture_output=True)
        if r.returncode == 0:
            return sorted(x for x in r.stdout.decode("utf-8", "replace").split(chr(0)) if x and x.split("/")[-1] == name)
    return sorted(p.relative_to(tree).as_posix() for p in tree.rglob(name))


def _all_tracked(tree):
    import subprocess
    tree = Path(tree)
    if (tree / ".git").exists():
        r = subprocess.run(["git", "-C", str(tree), "ls-files", "-z"], capture_output=True)
        if r.returncode == 0:
            return [x for x in r.stdout.decode("utf-8", "replace").split(chr(0)) if x]
    return [p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()]


class Index:
    def __init__(self, tree, branding_rel="browser/branding/gorilla", manifests=None):
        self.tree = Path(tree)
        self.files = {}          # (jar, jar path) -> source Path
        self.reg = {}            # (package, provider) -> (jar, jar path prefix)
        self.res = {}            # resource:// host -> (jar, jar path prefix)
        self.overrides = {}
        self.manifests = []
        self._names = None
        for mf in manifests if manifests is not None else self._manifests(branding_rel):
            self._read(mf)

    def _manifests(self, branding_rel):
        b = branding_rel.strip("/") + "/"
        out = []
        for rel in _tracked(self.tree, "jar.mn"):
            p = self.tree / rel
            if any(x in "/" + rel for x in SKIP_PARTS) or rel.startswith("obj"):
                continue
            if rel.startswith("browser/branding/") and not rel.startswith(b):
                continue
            out.append(p)
        return out

    def _read(self, mf):
        self.manifests.append(mf)
        jar = None
        for raw, base in _lines(mf):
            s = raw.strip()
            m = JAR.match(s)
            if m:
                jar = m.group(1)
                continue
            if s.startswith("%"):
                parts = s[1:].split()
                if len(parts) >= 3 and parts[0] in ("content", "skin", "locale"):
                    path = parts[2] if parts[0] == "content" else (parts[3] if len(parts) > 3 else "")
                    if path.startswith("%"):
                        self.reg[(parts[1], parts[0])] = (jar, path[1:])
                elif len(parts) >= 3 and parts[0] == "override":
                    self.overrides[parts[1]] = parts[2]
                elif len(parts) >= 3 and parts[0] == "resource" and parts[2].startswith("%"):
                    # `% resource name %res/name/` - a jar path; `resource://gre/...` aliases are not files here
                    self.res[parts[1]] = (jar, parts[2][1:])
                continue
            if jar is None or s.startswith("[") or s.startswith("relativesrcdir"):
                continue
            m = ENTRY.match(raw)
            if not m:
                continue
            target, src = m.group(1), m.group(2)
            if src and "*" in src:
                pat_dir = (base / src).parent
                pat = Path(src).name
                rec = "**" in src
                root = (base / src.split("*")[0]).resolve() if rec else pat_dir
                cands = root.rglob(pat) if rec else (pat_dir.glob(pat) if pat_dir.is_dir() else [])
                for f in cands:
                    sub = f.relative_to(root).as_posix() if rec else f.name
                    self.files[(jar, target.rstrip("/") + "/" + sub)] = f
                continue
            if src and src.startswith("%"):
                continue                       # locale merge sources
            self.files[(jar, target)] = (base / (src or Path(target).name)).resolve()

    def _hits(self, name):
        """Every tracked file called `name`, anywhere in the tree."""
        if self._names is None:
            self._names = {}
            for rel in _all_tracked(self.tree):
                self._names.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)
        return self._names.get(name, [])

    def by_name(self, url):
        """Fallback for a package registered at run time (built-in add-ons such as formautofill register their
        chrome from api.js, not jar.mn): the one tracked file with the URL's file name under a path that names the
        package. -> (Path or None, how many files carry that name anywhere in the tree)."""
        m = re.match(r"chrome://([\w.-]+)/(?:content|skin|locale)/(?:.*/)?([^/?#]+)", url)
        if not m:
            return None, 0
        pkg, name = m.groups()
        hits = self._hits(name)
        mine = [h for h in hits if pkg in h]
        return ((self.tree / mine[0]) if len(mine) == 1 else None), len(hits)

    def _in_jar(self, jar, path):
        p = self.files.get((jar, path))
        if p is None:
            # an entry under a directory target (`content/x/assets (assets/*)` registered without a trailing slash)
            for (j, t), src in self.files.items():
                if j == jar and fnmatch.fnmatch(path, t):
                    return src
        return p

    def resolve_resource(self, url):
        """resource:// URL -> (registered?, source Path or None). Registered means a jar.mn in the tree maps the
        host with `% resource`; only then can the URL be judged missing."""
        url = url.split("#")[0].split("?")[0]
        m = re.match(r"resource://([\w.-]+)/(.*)$", url)
        if not m or m.group(1) not in self.res:
            return False, None
        jar, prefix = self.res[m.group(1)]
        if not m.group(2):
            return True, None
        return True, self._in_jar(jar, prefix + m.group(2))

    def resolve(self, url):
        """chrome:// URL -> source Path in the tree, or None."""
        url = url.split("#")[0].split("?")[0]
        for _ in range(3):
            if url in self.overrides:
                url = self.overrides[url]
        m = re.match(r"chrome://([\w.-]+)/(content|skin|locale)/(.*)$", url)
        if not m:
            return None
        pkg, prov, rest = m.groups()
        reg = self.reg.get((pkg, prov))
        if not reg:
            return None
        jar, prefix = reg
        if prov == "content" and rest == "":
            return None
        p = self.files.get((jar, prefix + rest))
        if p is None and prov == "locale":
            return None
        if p is None:
            # an entry under a directory target (`content/x/assets (assets/*)` registered without a trailing slash)
            for (j, t), src in self.files.items():
                if j == jar and fnmatch.fnmatch(prefix + rest, t):
                    return src
        return p


def resolve(url, css_file, index):
    """Any URL found in a CSS file -> (Path or None, kind) where kind is file | data | external | unresolved."""
    u = url.strip().strip("'\"")
    if u.startswith("data:"):
        return None, "data"
    if u.startswith("chrome://"):
        p = index.resolve(u)
        if p and Path(p).is_file():
            return p, "file"
        q, anywhere = index.by_name(u)
        if q and Path(q).is_file():
            return q, "file"
        return None, "missing" if anywhere == 0 else "unresolved"
    if u.startswith("resource://"):
        registered, p = index.resolve_resource(u)
        if registered:
            if p and Path(p).is_file():
                return p, "file"
            name = u.split("#")[0].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            return None, "missing" if not index._hits(name) else "unresolved"
    if re.match(r"^[a-z][\w+.-]*:", u):
        return None, "external"         # moz-icon:, http:, an unregistered resource: ... not a tree file
    p = (Path(css_file).parent / u.split("#")[0].split("?")[0]).resolve()
    return (p, "file") if p.is_file() else (None, "missing")
