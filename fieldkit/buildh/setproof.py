# Version: 1.0.0 · updated 26-10-16-18-40
"""The patch set proven per operating system BEFORE anyone ports it, and two kinds of patch that are never ported.

2026-10-16, the first Windows port of 158 (the shared set had been proven on Linux only):
- 21.PORT.FIXES.158/008 repairs code that 01.MEDIA brings in; 01.MEDIA is Linux-only, so on Windows 008 had nothing
  to repair and the port turned it into a hand job. Nobody had declared it in exclude_on.windows, and the Windows
  selection had never been replayed.
- 22.EGRESS.LOCKDOWN.157/098 is the D-157-42 refresh of the security lists (CT logs, key pins, HSTS preload): DATA
  that lists-refresh takes fresh at every build. The port tried to carry the 157 lists onto 158 hunk by hunk.

So, deterministic:
  prove(...)            pristine + the selection of a platform, applied the way the port applies it; every failure
                        is named, and one whose missing lines come from a patch excluded on that platform is an
                        EXCLUDED DEPENDENCY (with the evidence), not a port job
  strip_refreshed(...)  a patch without its sections for the refreshed lists (timebombs.LIST_FILES)
  dependency(...)       the excluded patch that brings in a hunk's context, if any

    fieldkit build-harness set-proof [patchset=DIR] [version=158.0] [platform=windows|linux|all]
"""
import re
import shutil
import tempfile
from pathlib import Path

from . import firefox

PLATFORMS = ("windows", "linux")


def refreshed_files():
    from .timebombs import LIST_FILES
    return set(LIST_FILES)


_SECTION = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$", re.M)


def strip_refreshed(text, files=None):
    """-> (patch text without the sections for refreshed files, [files removed]). A patch with no `diff --git`
    sections is returned unchanged."""
    files = refreshed_files() if files is None else set(files)
    starts = [(m.start(), m.group(2)) for m in _SECTION.finditer(text)]
    if not starts:
        return text, []
    keep, removed = [text[:starts[0][0]]], []
    for i, (pos, path) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        if path in files:
            removed.append(path)
        else:
            keep.append(text[pos:end])
    return "".join(keep), removed


def has_hunks(text):
    return bool(re.search(r"^@@ ", text, re.M)) or "GIT binary patch" in text


def excluded_here(raw_groups, platform):
    """{group/patch name: why} every patch the policy leaves out on `platform` (a whole group by `platforms`,
    single patches by `exclude_on`)."""
    out = {}
    for g, spec in raw_groups.items():
        plats = spec.get("platforms")
        if plats and platform not in plats:
            out[f"{g}/*"] = f"group {g} is platforms: {plats}"
        for p in (spec.get("exclude_on") or {}).get(platform, []):
            out[f"{g}/{p}"] = f"exclude_on.{platform} in group {g}"
    return out


def _plus_lines(pset, raw_groups, platform):
    """{line: group/patch} the '+' lines of every patch excluded on `platform` (stripped, distinctive only)."""
    ex = excluded_here(raw_groups, platform)
    lines = {}
    for key in ex:
        g, p = key.split("/", 1)
        pfs = sorted((pset / g).rglob("*.patch")) if p == "*" else [pset / g / p]
        for pf in pfs:
            if not pf.is_file():
                continue
            for l in pf.read_text(encoding="utf-8", errors="replace").splitlines():
                if l.startswith("+") and not l.startswith("+++"):
                    s = l[1:].strip()
                    if len(s) >= 12:
                        lines.setdefault(s, f"{g}/{pf.name}")
    return lines


_IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{5,})\b")
_CAMEL = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])[A-Za-z]\w+$")
_DECL = re.compile(r"\b(?:enum\s+class|class|struct|enum)\s+([A-Za-z_]\w{3,})|\b([A-Za-z_]\w{5,})\s*\(|\b(m[A-Z]\w{3,})\b")


def _names_added(text):
    """Names a patch brings into C/C++ code: declared types, called/defined functions, m-members, on '+' lines only."""
    out = set()
    for l in text.splitlines():
        if l.startswith("+") and not l.startswith("+++") and not l[1:].lstrip().startswith(("//", "*", "/*")):
            for m in _DECL.finditer(l):
                out.update(g for g in m.groups() if g)
    return out


def _names_upstream(pristine_repo, pristine, files, _cache={}):
    """Every identifier upstream already has in `files` (a name upstream defines is not a dependency)."""
    from . import replay
    names = set()
    for f in files:
        key = (str(pristine_repo), pristine, f)
        if key not in _cache:
            try:
                _cache[key] = set(_IDENT.findall(replay._git(pristine_repo, "show", f"{pristine}:{f}").decode("utf-8", "replace")))
            except Exception:  # noqa: BLE001 - a file upstream does not have
                _cache[key] = set()
        names |= _cache[key]
    return names


def _in_upstream_headers(pristine_repo, pristine, name, _cache={}):
    """A name any upstream header has (LowerCaseEqualsLiteral) belongs to upstream, not to a patch."""
    key = (str(pristine_repo), pristine, name)
    if key not in _cache:
        import subprocess
        r = subprocess.run(["git", "-C", str(pristine_repo), "grep", "-q", "-w", "-F", name, pristine, "--", "*.h"],
                           capture_output=True)
        _cache[key] = r.returncode == 0
    return _cache[key]


def code_dependencies(pset, raw_groups, platform, selected, pristine_repo=None, pristine=None):
    """-> [{patch, needs, from}] selected C/C++ patches whose ADDED lines use a name that only a patch excluded on
    `platform` brings in. 2026-10-16: 21.PORT.FIXES.158/007 applied cleanly on Windows and declared
    PDMFactory(Purpose); the definition is in 01.MEDIA (Linux only): the compile stopped after 69 minutes. Applying
    patches cannot see this; reading names can, in seconds, before any compile."""
    owner = {}
    for key in excluded_here(raw_groups, platform):
        g, p = key.split("/", 1)
        for pf in (sorted((pset / g).rglob("*.patch")) if p == "*" else [pset / g / p]):
            if pf.is_file():
                for n in _names_added(pf.read_text(encoding="utf-8", errors="replace")):
                    owner.setdefault(n, f"{g}/{pf.name}")
    if not owner:
        return []
    out = []
    for rel, text in selected:
        files = [b for _a, b in _SECTION.findall(text) if b.endswith((".h", ".cpp", ".cc", ".c", ".mm", ".hpp"))]
        if not files:
            continue
        used = set()
        for l in text.splitlines():
            if l.startswith("+") and not l.startswith("+++") and not l[1:].lstrip().startswith(("//", "*", "/*")):
                used.update(_IDENT.findall(l))
        # both directions: this patch uses a name only the excluded patch defines (008), or declares one only the
        # excluded patch defines (007: PDMFactory(Purpose) declared here, defined in 01.MEDIA). Only CamelCase names
        # count: words (machine, switch) and ALL_CAPS macros (MOZ_LOG) are not one patch's invention.
        hits = sorted(n for n in used & set(owner) if _CAMEL.match(n))
        if hits and pristine_repo is not None:
            up = _names_upstream(pristine_repo, pristine, files)
            hits = [n for n in hits if n not in up and not _in_upstream_headers(pristine_repo, pristine, n)]
        if hits:
            out.append({"patch": rel, "needs": hits[:6], "from": sorted({owner[n] for n in hits})[:3]})
    return out


def dependency(hunk_lines, plus_lines, need=2):
    """The excluded patch whose added lines make up at least `need` of this hunk's context/removed lines, or None."""
    hits = {}
    for l in hunk_lines:
        if l[:1] in (" ", "-"):
            s = l[1:].strip()
            if len(s) >= 12 and s in plus_lines:
                hits[plus_lines[s]] = hits.get(plus_lines[s], 0) + 1
    best = max(hits.items(), key=lambda kv: kv[1], default=(None, 0))
    return best[0] if best[1] >= need else None


# Paths only one operating system builds (owner, 2026-10-16: "make sure the harness does not try to implement linux
# patches only on a windows build and vice versa"). macOS is not built: a mac-only file is neither's and neutral.
_ONLY = (
    ("linux", re.compile(r"(^|/)(gtk|gnome|wayland|x11|linux|unixproxy)(/|$)|^widget/gtk/|^browser/installer/linux/"
                         r"|\.desktop$|(^|/)\w*(Wayland|X11|Gtk|GTK)\w*\.(cpp|h|c)$|_linux\.(cc|cpp|h)$")),
    ("windows", re.compile(r"^widget/windows/|^browser/installer/windows/|^toolkit/mozapps/installer/windows/"
                           r"|(^|/)(windows|win32|win)(/|$)|/msix/|\.(nsi|nsh|rc|manifest\.in)$|_win\.(cc|cpp|h)$"
                           r"|(^|/)mozconfig\.win\w*$")),
    ("mac", re.compile(r"^widget/cocoa/|(^|/)(mac|macosx|cocoa)(/|$)|\.(mm|icns)$|(^|/)(dsstore|Assets\.car)$")),
)


def platform_of(path):
    """'linux' | 'windows' | 'mac' for a path only that system builds; None for a shared path."""
    for plat, rx in _ONLY:
        if rx.search(path):
            return plat
    return None


def foreign_only(files, platform):
    """The other platform a patch belongs to when EVERY file it writes is only built there; else None. A patch with
    one shared file, or with only macOS files, is not judged."""
    plats = {platform_of(f) for f in files}
    if not plats or None in plats:
        return None
    plats.discard("mac")
    if not plats or platform in plats:
        return None
    return "+".join(sorted(plats))


def declare_foreign(spec, group_dir):
    """Write exclude_on.<platform> into a group's policy entry for every patch of it that only the other platform
    builds (foreign_only). Used by rebase-set, whose shared group is generated on one system from the whole tree:
    2026-10-16 Linux's 23.REBASE.158 carried 14 Windows-only patches (NSIS, msix tiles, widget/windows) selected on
    Linux. -> {platform: [patch names]} added."""
    added = {}
    for pf in sorted(Path(group_dir).glob("*.patch")):
        files = {b for _a, b in _SECTION.findall(pf.read_text(encoding="utf-8", errors="replace"))}
        for plat in PLATFORMS:
            if foreign_only(files, plat):
                added.setdefault(plat, []).append(pf.name)
    if added:
        eo = spec.setdefault("exclude_on", {})
        for plat, names in added.items():
            eo[plat] = sorted(set(eo.get(plat, [])) | set(names))
        spec["exclude_on_reason"] = ("Written by rebase-set from the paths alone (setproof.platform_of): each patch here "
                                     "writes only files the other operating system builds.")
    return added


def _hunks_of(text):
    """[(file, [hunk lines])] of a unified diff."""
    out, cur, f = [], None, None
    for l in text.splitlines():
        m = _SECTION.match(l)
        if m:
            f = m.group(2)
            continue
        if l.startswith("@@"):
            cur = []
            out.append((f, cur))
        elif cur is not None and l[:1] in (" ", "-", "+"):
            cur.append(l)
    return out


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - a file pristine does not have: nothing to compare with
        return None


def prove(patchset, pristine_repo, platform, say=print):
    """-> {ok, platform, patches, failures:[{patch, why, depends_on}], refreshed_skipped:[patch]}.
    Pristine files come from `pristine_repo` (a git checkout of the release, e.g. the vault copy), materialised in a
    scratch folder; nothing in either repository is touched."""
    from . import replay
    pset, groups = firefox._policy(patchset, platform)
    raw = __import__("json").loads((Path(patchset) / "config" / "patch_policy.json").read_text(encoding="utf-8"))["groups"]
    enabled = [(g, s) for g, s in groups.items() if s.get("status") == "enabled"]
    pristine = replay._git(pristine_repo, "rev-parse", "HEAD").decode().strip()
    scratch = Path(tempfile.mkdtemp(prefix="gsetproof_"))
    plus = _plus_lines(pset, raw, platform)
    copied = set()                                   # paths a group's NEW_FILES created in the scratch tree
    res = {"ok": False, "platform": platform, "patches": 0, "failures": [], "refreshed_skipped": [],
           "pristine": pristine}
    try:
        t = scratch / "t"
        t.mkdir()
        paths = replay._targets(pset, enabled)
        replay._materialise(pristine_repo, pristine, paths, t)
        for g, spec in enabled:
            ex = set(spec.get("exclude", []))
            for pf in sorted((pset / g).rglob("*.patch")):
                if pf.name in ex:
                    continue
                text = pf.read_text(encoding="utf-8", errors="replace")
                rel = pf.relative_to(pset).as_posix()
                body, removed = strip_refreshed(text)
                if removed:
                    res["refreshed_skipped"].append(rel)
                    if not has_hunks(body):
                        continue
                    pf_use = scratch / "stripped.patch"
                    pf_use.write_text(body, encoding="utf-8", newline="\n")
                else:
                    pf_use = pf
                # applies harmlessly but builds nothing here: undeclared, it is the other system's patch ported blind.
                # Still applied below, so the patches after it see the tree the port would make.
                other = foreign_only({b for _a, b in _SECTION.findall(body)}, platform)
                if other:
                    res["failures"].append({"patch": rel, "why": f"writes only {other} files", "depends_on": None,
                                            "foreign": other})
                ok, out = replay._apply_patch(pf_use, t)
                if not ok and "already exists in working directory" in out:
                    # a later group creates in final form what an earlier group's NEW_FILES put there (23.REBASE.158's
                    # logos over 08.Look's): the later one is the built tree's; only a NEW_FILES copy is replaced
                    for m in re.finditer(r"error: (\S+): already exists in working directory", out):
                        if m.group(1) in copied:
                            (t / m.group(1)).unlink()
                    ok, out = replay._apply_patch(pf_use, t)
                res["patches"] += 1
                if not ok:
                    dep = None
                    for _f, h in _hunks_of(body):
                        dep = dependency(h, plus)
                        if dep:
                            break
                    # the first error line, not the last line (2026-10-18: "patching file findbar.css" hid the hunk)
                    ls = out.strip().splitlines() or ["failed"]
                    why, at = ls[-1], ""
                    for l in ls:
                        if l.startswith("patching file "):
                            at = l[len("patching file "):]
                        elif re.search(r"error|FAILED|failed|reject|can't find|malformed|corrupt", l, re.I):
                            why = (f"{at}: " if at else "") + l
                            break
                    res["failures"].append({"patch": rel, "why": why[:300],
                                            "depends_on": dep})
            for src, relf in firefox.new_files(pset, g):
                d = t / relf
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    d.write_bytes(src.read_bytes())
                    copied.add(relf)
        # code that needs a patch this platform leaves out (names, not context: 007, 2026-10-16) - before any compile
        sel = []
        for g, spec in enabled:
            ex = set(spec.get("exclude", []))
            sel += [(pf.relative_to(pset).as_posix(), pf.read_text(encoding="utf-8", errors="replace"))
                    for pf in sorted((pset / g).rglob("*.patch")) if pf.name not in ex]
        for d in code_dependencies(pset, raw, platform, sel, pristine_repo, pristine):
            res["failures"].append({"patch": d["patch"], "depends_on": d["from"][0],
                                    "why": f"uses or declares {', '.join(d['needs'])}, which only {d['from'][0]} brings in"})
        # one pref, one value, in the tree this selection makes (prefconflicts.py; 2026-10-16: Inter vs Segoe UI)
        from . import prefconflicts
        res["pref_conflicts"] = prefconflicts.scan(t, pristine_tree=pristine_repo, pristine_rev=pristine)
        # a lazy.X the selection leaves undeclared (symbols.undeclared_lazy, as a delta against pristine; 2026-10-16:
        # UITour's lazy.AIWindow after the 158 rebase removed the Smart Window)
        from . import symbols
        js = sorted(p.relative_to(t).as_posix() for p in t.rglob("*") if p.suffix in (".mjs", ".js") and p.is_file())
        show = lambda rel: replay._git(pristine_repo, "show", f"{pristine}:{rel}").decode("utf-8", "replace")
        res["lazy"] = [p for p in symbols.problems(t, js, pristine=lambda rel: _try(show, rel)) if "lazy." in p]
        res["ok"] = not res["failures"] and not res["pref_conflicts"] and not res["lazy"]
        say(f"  set-proof {platform}: {res['patches']} patch(es), {len(res['failures'])} failure(s), "
            f"{len(res['refreshed_skipped'])} with refreshed lists left to lists-refresh")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return res


def lines(r):
    out = [f"SET-PROOF {r['platform']}: {'OK' if r['ok'] else 'FAIL'} - {r['patches']} patch(es) on pristine "
           f"{r['pristine'][:12]}"]
    for f in r["failures"]:
        if f.get("foreign"):
            out.append(f"  OTHER PLATFORM {f['patch']}: {f['why']}, yet {r['platform']} selects it -> add it to "
                       f"exclude_on.{r['platform']}, or give its group platforms: [{f['foreign']}]")
        elif f["depends_on"]:
            out.append(f"  EXCLUDED DEPENDENCY {f['patch']}: its context comes from {f['depends_on']}, which "
                       f"{r['platform']} leaves out -> add it to exclude_on.{r['platform']}")
        else:
            out.append(f"  FAILS {f['patch']}: {f['why']}")
    for l in r.get("lazy") or []:
        out.append(f"  UNDECLARED LAZY {l}")
    for c in r.get("pref_conflicts") or []:
        out.append(f"  PREF CONFLICT {c['name']}: {c['a']}  vs  {c['b']} -> keep the decided value, correct the other "
                   f"line in its patch (prefconflicts.drop_added_lines)")
    for p in r["refreshed_skipped"]:
        out.append(f"  refreshed, never ported: {p} (its security-list sections; lists-refresh takes them, D-157-42)")
    return out
