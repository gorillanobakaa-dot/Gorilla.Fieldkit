"""Every about: page Firefox can register, read from the source: where, under which build condition, with which flags.

    fieldkit build-harness about-registry <task> [--json]      the map: tree (HEAD) against pristine upstream

Born 2026-10-08. The owner found about:fingerprintingprotection, a page about:about does not list ("even recognized
developers would not be aware of this one as it does not appear in about:about"), and asked to map every hidden page,
"as deep as possible". about:about lists only the registered pages without the HIDE_FROM_ABOUTABOUT flag; the browser
can register pages in four ways, and this reads all four, in the tree and in pristine upstream:
  1. the C++ redirect tables (docshell/base/nsAboutRedirector.cpp, browser/components/about/AboutRedirector.cpp):
     name, target, nsIAboutModule flags, and the preprocessor condition around the entry (#ifdef ...);
  2. the about_pages / pages lists in components.conf that register those tables' names (with their `if` conditions);
  3. any components.conf contract '@mozilla.org/network/protocol/about;1?what=<name>' (about:blank, about:cache,
     about:newtab, about:compat, about:debugging, ...), including the `'%s' % page for page in pages` form;
  4. any other shipped source naming that contract (pages a script registers at run time).
Merged per name: {name, sources: [{file, line, how, condition, target, flags}], hidden, in_tree, in_pristine}.
`with_runtime(entries, registered)` adds what a build actually registered (probe about-pages ABOUT-REGISTERED lines).
"""
import re
import subprocess

REDIRECT_TABLES = ("docshell/base/nsAboutRedirector.cpp", "browser/components/about/AboutRedirector.cpp")
CONF_LISTS = ("docshell/build/components.conf", "browser/components/about/components.conf")
CONTRACT = re.compile(r"network/protocol/about;1\?what=([A-Za-z0-9_-]+|%s)")
ENTRY = re.compile(r'\{\s*"([^"]+)"\s*,\s*"([^"]*)"\s*,(.*?)\}\s*,', re.S)
FLAG = re.compile(r"nsIAboutModule::([A-Z_]+)")


def _git(w, *a):
    return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(w), *a], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _read(w, rel, rev=None):
    if rev:
        r = _git(w, "show", f"{rev}:{rel}")
        return r.stdout if r.returncode == 0 else None
    from pathlib import Path
    p = Path(w) / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


def redirect_entries(text, rel):
    """C++ table entries with the preprocessor condition each sits under -> [source dict]."""
    out, stack = [], []
    lines = text.splitlines()
    # map character offset -> line number and the condition stack at that line
    conds, cur = [], []
    for ln in lines:
        s = ln.strip()
        m = re.match(r"#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(.*)", s)
        if m:
            kind, expr = m.group(1), m.group(2).strip()
            if kind in ("ifdef", "ifndef", "if"):
                cur = cur + [("!" if kind == "ifndef" else "") + (f"defined({expr})" if kind in ("ifdef", "ifndef") else expr)]
            elif kind in ("elif", "else") and cur:
                cur = cur[:-1] + [("else of " + cur[-1]) if kind == "else" else expr]
            elif kind == "endif" and cur:
                cur = cur[:-1]
        conds.append(list(cur))
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    import bisect
    for m in ENTRY.finditer(text):
        name, target, rest = m.group(1), m.group(2), m.group(3)
        flags = FLAG.findall(rest)
        if not flags and "nsIAboutModule" not in rest and rest.strip() not in ("0",):
            continue                                    # not a table entry
        line = bisect.bisect_right(offsets, m.start())
        out.append({"name": name, "file": rel, "line": line, "how": "redirect table", "target": target,
                    "flags": flags, "condition": " && ".join(conds[line - 1]) or None})
    return out


def conf_list_entries(text, rel):
    """about_pages / pages lists and their conditional appends in a components.conf -> [source dict]."""
    out, cond = [], None
    in_list = False
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if re.match(r"^(about_pages|pages)\s*=\s*\[", s):
            in_list, cond = True, None
            continue
        if in_list:
            if s.startswith("]"):
                in_list = False
                continue
            m = re.match(r"^'([\w-]+)',", s)
            if m:
                out.append({"name": m.group(1), "file": rel, "line": i, "how": "components.conf list",
                            "condition": None, "target": None, "flags": []})
            continue
        m = re.match(r"^if (.+):$", s)
        if m and not ln.startswith(" "):
            cond = m.group(1)
            continue
        m = re.match(r"^(?:about_pages|pages)\.append\('([\w-]+)'\)|^pages \+= \[(.+)\]", s)
        if m:
            names = [m.group(1)] if m.group(1) else re.findall(r"'([\w-]+)'", m.group(2))
            for n in names:
                out.append({"name": n, "file": rel, "line": i, "how": "components.conf list",
                            "condition": cond, "target": None, "flags": []})
        elif not ln.startswith(" ") and s and not s.startswith("#"):
            cond = None
    return out


def contract_entries(text, rel):
    """'about;1?what=<name>' contracts in one file; the '%s' % page form expands the file's `pages` list."""
    out = []
    pages = re.findall(r"'([\w-]+)'", (re.search(r"^pages\s*=\s*\[(.*?)\]", text, re.S | re.M) or [None, ""])[1]) \
        if re.search(r"^pages\s*=\s*\[", text, re.M) else []
    for i, ln in enumerate(text.splitlines(), 1):
        for name in CONTRACT.findall(ln):
            for n in (pages if name == "%s" else [name]):
                out.append({"name": n, "file": rel, "line": i,
                            "how": "component contract" if rel.endswith(".conf") else "named in code",
                            "condition": None, "target": None, "flags": []})
    return out


def scan(workdir, rev=None):
    """-> {name: {"name", "sources": [...]}} for the tree (rev None) or a revision (pristine)."""
    found = {}

    def add(src):
        found.setdefault(src["name"], {"name": src["name"], "sources": []})["sources"].append(src)
    for rel in REDIRECT_TABLES:
        t = _read(workdir, rel, rev)
        if t:
            for s in redirect_entries(t, rel):
                add(s)
    listed = {x for x in CONF_LISTS}
    for rel in CONF_LISTS:
        t = _read(workdir, rel, rev)
        if t:
            for s in conf_list_entries(t, rel):
                add(s)
    # every other file that names an about: contract (components.conf, C++ and scripts), test code excluded
    grep = ["grep", "-l", "-I", "network/protocol/about;1?what="] + ([rev] if rev else []) + \
        ["--", "*.conf", "*.cpp", "*.h", "*.mjs", "*.js", ":!**/test/**", ":!**/tests/**", ":!testing/**"]
    for line in _git(workdir, *grep).stdout.splitlines():
        rel = line.split(":", 1)[1] if rev and ":" in line else line
        if rel in listed or rel.endswith(("E10SUtils.sys.mjs", "PoliciesHelpers.sys.mjs")):
            continue                                    # lists of pages, not registrations
        t = _read(workdir, rel, rev)
        if t:
            for s in contract_entries(t, rel):
                add(s)
    return found


def merged(workdir, pristine):
    """Tree and pristine side by side -> sorted [entry]: name, in_tree, in_pristine, hidden (HIDE_FROM_ABOUTABOUT in
    a redirect table), condition (any source's), target, flags, sources (tree's, else pristine's)."""
    now, was = scan(workdir), scan(workdir, pristine)
    out = []
    for name in sorted(set(now) | set(was)):
        src = (now.get(name) or was.get(name))["sources"]
        table = [s for s in src if s["how"] == "redirect table"]
        conds = sorted({s["condition"] for s in src if s["condition"]})
        out.append({"name": name, "in_tree": name in now, "in_pristine": name in was,
                    "hidden": any("HIDE_FROM_ABOUTABOUT" in s["flags"] for s in table),
                    "target": table[0]["target"] if table else None,
                    "flags": table[0]["flags"] if table else [],
                    "condition": "; ".join(conds) or None, "sources": src})
    return out


def with_runtime(entries, registered):
    """Add what a build registers at run time: registered = {name: "hidden"|"listed"} (probe ABOUT-REGISTERED)."""
    names = {e["name"] for e in entries}
    for e in entries:
        e["in_build"] = e["name"] in registered
        if e["name"] in registered:
            e["hidden_at_runtime"] = registered[e["name"]] == "hidden"
    for n in sorted(set(registered) - names):          # registered by a route this scanner did not read: say so
        entries.append({"name": n, "in_tree": None, "in_pristine": None, "hidden": registered[n] == "hidden",
                        "target": None, "flags": [], "condition": None, "sources": [], "in_build": True,
                        "hidden_at_runtime": registered[n] == "hidden", "unexplained": True})
    return entries
