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
                    res["failures"].append({"patch": rel, "why": (out.strip().splitlines() or ["failed"])[-1][:200],
                                            "depends_on": dep})
            for src, relf in firefox.new_files(pset, g):
                d = t / relf
                if not d.exists():
                    d.parent.mkdir(parents=True, exist_ok=True)
                    d.write_bytes(src.read_bytes())
                    copied.add(relf)
        res["ok"] = not res["failures"]
        say(f"  set-proof {platform}: {res['patches']} patch(es), {len(res['failures'])} failure(s), "
            f"{len(res['refreshed_skipped'])} with refreshed lists left to lists-refresh")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return res


def lines(r):
    out = [f"SET-PROOF {r['platform']}: {'OK' if r['ok'] else 'FAIL'} - {r['patches']} patch(es) on pristine "
           f"{r['pristine'][:12]}"]
    for f in r["failures"]:
        if f["depends_on"]:
            out.append(f"  EXCLUDED DEPENDENCY {f['patch']}: its context comes from {f['depends_on']}, which "
                       f"{r['platform']} leaves out -> add it to exclude_on.{r['platform']}")
        else:
            out.append(f"  FAILS {f['patch']}: {f['why']}")
    for p in r["refreshed_skipped"]:
        out.append(f"  refreshed, never ported: {p} (its security-list sections; lists-refresh takes them, D-157-42)")
    return out
