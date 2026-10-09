"""Time bombs: dates compiled into the browser after which a protection switches itself off without a word.

    fieldkit build-harness timebombs <task> [margin=42] [at=YYYY-MM-DD]
    fieldkit build-harness lists-refresh <task> [ref=main] [write=1]

Born 2026-10-09, the first tool of the proactive programme (owner: tools that find "problems my limited gorilla
intellect can not see or foreseen", not tools made after a failure). Asked what in Gorilla quietly stops working with
time, the tree answered with three dates, all in effect in build 29:
  - kCTExpirationTime (security/ct/CTKnownLogs.h): Certificate Transparency, enforced in Gorilla (mode 2): from
    2026-12-01 the check is skipped ("skipping CT - built-in information has expired", CertVerifier.cpp);
  - kPreloadPKPinsExpirationTime (StaticHPKPins.h): key pinning for the big sites, off from 2026-12-25;
  - gPreloadListExpirationTime (nsSTSPreloadList.inc): HSTS preload, off from 2027-01-22.
Firefox users never meet them: their browser updates itself every four weeks with fresh lists. Gorilla does not update
itself (a closed door), so a build kept past those dates silently loses the protections. Mozilla stamps the CT list
to expire about ten weeks after it is made, so no build keeps CT checking longer than that.

timebombs: every `...Expir...Time = INT64_C(<microseconds>)` in the tree (git grep, third-party and tests excluded),
judged against `at` (default today) plus `margin` days (default 42). A date nobody has reviewed (not in KNOWN) fails,
and so does a known one that is no longer found: upstream adding or renaming a time bomb stops the release until a
person reads it. Rows join build-verify (at = the build day) and release-check (at = the publishing day).

lists-refresh (decision D-157-42, the owner's option 1: fresh lists at every build): the three files from the same
repository the harness already takes Firefox from (upstream.FIREFOX_REPO, Mozilla's own; nothing new learns anything:
the repository sees one more fetch from the build machine). A file is taken only when it is pure data: the HSTS list
must keep its header and footer and hold nothing but `host, 0|1` lines; the two headers must keep every line that is
not a data row (includes, macros, declarations, namespaces) and add no code. Its expiry must be later than the tree's.
With write=1 the files are written into the tree; record them like any change (build-harness record).
"""
import datetime
import re
import subprocess
from pathlib import Path

CONST = re.compile(r"\b([kg][A-Za-z]*Expir[A-Za-z]*Time)\s*=\s*INT64_C\((\d{13,19})\)")
GREP = r"[kg][A-Za-z]*Expir[A-Za-z]*Time *= *INT64_C\([0-9]{13,19}\)"
KNOWN = {
    "kCTExpirationTime": {"file": "security/ct/CTKnownLogs.h",
                          "what": "Certificate Transparency (certificates never publicly logged are refused)"},
    "kPreloadPKPinsExpirationTime": {"file": "security/manager/ssl/StaticHPKPins.h",
                                     "what": "key pinning (big sites accept only their own known certificates)"},
    "gPreloadListExpirationTime": {"file": "security/manager/ssl/nsSTSPreloadList.inc",
                                   "what": "HSTS preload (listed sites always open securely, even on the first visit)"},
}
LIST_FILES = tuple(v["file"] for v in KNOWN.values())
MARGIN_DAYS = 42


def when(us):
    """PRTime (microseconds since 1970, UTC) -> date."""
    return datetime.datetime.fromtimestamp(int(us) / 1e6, datetime.timezone.utc).date()


def scan(workdir, run=subprocess.run):
    """-> [{name, file, line, expires}] for every expiry constant the tree's tracked files hold."""
    r = run(["git", "-C", str(workdir), "grep", "-nE", GREP, "--", ".", ":!third_party", ":!**/test/**",
             ":!**/tests/**"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for l in (r.stdout or "").splitlines():
        path, line, text = (l.split(":", 2) + ["", ""])[:3]
        m = CONST.search(text)
        if m:
            out.append({"name": m.group(1), "file": path, "line": int(line) if line.isdigit() else 0,
                        "expires": when(m.group(2))})
    return out


def judge(found, at, margin=MARGIN_DAYS):
    """-> rows. `at` is the day the build is made or published."""
    rows, names = [], {f["name"] for f in found}
    for f in found:
        k = KNOWN.get(f["name"])
        if not k:
            rows.append({"check": "time bombs: every expiry date in the tree is reviewed", "ok": False,
                         "evidence": f"{f['name']} in {f['file']}:{f['line']} switches something off on {f['expires']}: "
                                     "read it, then add it to timebombs.KNOWN with what it protects"})
            continue
        left = (f["expires"] - at).days
        rows.append({"check": f"time bombs: {k['what']} stays on at least {margin} days",
                     "ok": left >= margin,
                     "evidence": f"switches off {f['expires']} ({left} days after {at}; {f['name']}, {f['file']})"
                                 + ("" if left >= margin else "; refresh the lists: build-harness lists-refresh")})
    for name, k in KNOWN.items():
        if name not in names:
            rows.append({"check": "time bombs: every known expiry date is still found", "ok": False,
                         "evidence": f"{name} ({k['file']}) not found: upstream moved or renamed it; find it before release"})
    if not found:
        rows.append({"check": "time bombs: the scan found the tree's expiry dates", "ok": False,
                     "evidence": "nothing found: the scan is blind (wrong tree?)"})
    return rows


def rows(workdir, at=None, margin=MARGIN_DAYS, run=subprocess.run):
    return judge(scan(workdir, run=run), at or datetime.date.today(), margin)


# -- lists-refresh ------------------------------------------------------------------------------------------------
HSTS_ROW = re.compile(r"^[a-z0-9._-]+, [01]$")
STRUCT = re.compile(r"^\s*(#|namespace\b|struct\b|class\b|typedef\b|using\b|template\b|enum\b|extern\b|"
                    r"static\s+const\s+PRTime\b|const\s+PRTime\b|static\s+const\s+\w+\s+k\w*List\b|const\s+\w+\s+k\w*List\b|"
                    r"[}]\s*;?\s*//|[}]\s*//)")
CODE = re.compile(r"\)\s*(const\s*)?\{\s*$")


def _norm_expiry(line):
    return CONST.sub(lambda m: f"{m.group(1)} = INT64_C(<date>)", line)


def check_list(path, old, new):
    """Is `new` the same kind of file as `old`, with only its data changed? -> (ok, why)."""
    if not new.strip():
        return False, "empty"
    o, n = old.splitlines(), new.splitlines()
    if path.endswith(".inc"):
        def split(lines):
            marks = [i for i, l in enumerate(lines) if l.strip() == "%%"]
            if len(marks) != 2:
                return None
            return lines[:marks[0]], lines[marks[0] + 1:marks[1]], lines[marks[1] + 1:]
        so, sn = split(o), split(n)
        if not sn:
            return False, "no %% ... %% data block"
        if [_norm_expiry(l) for l in so[0]] != [_norm_expiry(l) for l in sn[0]] or so[2] != sn[2]:
            return False, "header or footer differs from the tree's"
        bad = [l for l in sn[1] if not HSTS_ROW.match(l)]
        if bad:
            return False, f"{len(bad)} line(s) are not `host, 0|1` rows, e.g. {bad[0][:60]!r}"
        return True, f"{len(sn[1])} host rows"
    so = sorted(_norm_expiry(l).strip() for l in o if STRUCT.match(l))
    sn = sorted(_norm_expiry(l).strip() for l in n if STRUCT.match(l))
    if path.endswith("StaticHPKPins.h"):
        # its declarations are the data (one `static const char k<Name>Fingerprint[]` per pinned key): only the fixed
        # lines (includes, macros, the expiry, struct and table heads) must be unchanged
        so = [l for l in so if "Fingerprint[]" not in l]
        sn = [l for l in sn if "Fingerprint[]" not in l]
    if so != sn:
        diff = sorted(set(sn) ^ set(so))
        return False, f"structure differs from the tree's: {diff[0][:80]!r}" if diff else "structure differs"
    if sum(bool(CODE.search(l)) for l in n) > sum(bool(CODE.search(l)) for l in o):
        return False, "adds code (a function body)"
    return True, f"{len(n)} lines, structure unchanged"


def expiry_of(text):
    m = CONST.search(text)
    return when(m.group(2)) if m else None


def fetch(ref, cache, repo=None, run=subprocess.run):
    """The three files at `ref` of the upstream repository -> ({path: text}, commit, commit date)."""
    from . import upstream
    repo = repo or upstream.FIREFOX_REPO
    cache = Path(cache)
    if not (cache / ".git").is_dir():
        cache.mkdir(parents=True, exist_ok=True)
        run(["git", "-C", str(cache), "init", "-q"], check=True)
    run(["git", "-C", str(cache), "fetch", "-q", "--depth", "1", "--filter=blob:none", repo, ref], check=True,
        capture_output=True, timeout=900)
    head = run(["git", "-C", str(cache), "log", "-1", "--format=%H %cs", "FETCH_HEAD"], capture_output=True, text=True,
               check=True).stdout.split()
    files = {}
    for p in LIST_FILES:
        files[p] = run(["git", "-C", str(cache), "show", f"FETCH_HEAD:{p}"], capture_output=True, text=True,
                       encoding="utf-8", check=True).stdout
    return files, head[0], head[1]


def refresh(workdir, ref="main", cache=None, write=False, fetched=None):
    """-> {"rows", "written"}. `fetched` (files, commit, date) stands in for the network in tests."""
    from . import task
    files, commit, date = fetched or fetch(ref, cache or (task.STATE / "lists-cache"))
    rows, take = [], {}
    for p in LIST_FILES:
        cur_path = Path(workdir) / p
        cur = cur_path.read_text(encoding="utf-8") if cur_path.is_file() else ""
        new = files.get(p, "")
        ok, why = check_list(p, cur, new)
        old_e, new_e = expiry_of(cur), expiry_of(new)
        later = bool(ok and old_e and new_e and new_e >= old_e)
        rows.append({"check": f"lists-refresh: {p} is pure data and fresher", "ok": later,
                     "evidence": f"{why}; expiry {old_e} -> {new_e} ({ref} {commit[:12]}, {date})"
                                 + ("" if later or not ok else "; not later than the tree's")})
        if later and new_e > old_e:
            take[p] = new
    written = []
    if write and all(r["ok"] for r in rows):
        for p, text in take.items():
            cur_path = Path(workdir) / p
            raw = cur_path.read_bytes().decode("utf-8")
            crlf = raw.count("\r\n") > raw.count("\n") // 2
            body = text.replace("\r\n", "\n")
            cur_path.write_bytes((body.replace("\n", "\r\n") if crlf else body).encode("utf-8"))
            written.append(p)
    return {"rows": rows, "written": written, "source": f"{ref} {commit} ({date})"}
