# Version: 1.0.0 · updated 26-10-19
"""Every place a project states its version, held to one answer (docs/METHODS.md #19).

    fieldkit version-sync PATH [--expect 0.3.0] [--binary EXE] [--json]

Reads the version wherever a project of the owner's states it: Cargo.toml (package or workspace.package) and the
package's entry in Cargo.lock, pyproject.toml, package.json, PKGBUILD pkgver, packaging/changelog.in (its first
entry), a VERSION file, an Inno Setup script (AppVersion / #define ...Version), the top entry of CHANGELOG.md, and,
with --binary, what the built program itself answers to --version. Disagreement is a finding (exit 3); --expect makes
every place answer that one version. A place that does not exist is not a finding; a place that exists and cannot be
read is reported, never guessed.

Born 2026-10-19 from the audit: GPNH's packaging/changelog.in still carried 0.10.0 after the version moved on; Gorilla
OpenCode once shipped a binary whose embedded stamp was not the release's (a VCS pseudo-version overrode -ldflags);
TPFanControl states its version in the installer and the program. Each was found by hand.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SEMVER = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)?(?:[-+.][0-9A-Za-z.-]+)?)\b")


def _read(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return e


def _toml_value(text, section, key):
    """key = "value" inside [section] of a TOML text (no TOML library needed for this one lookup)."""
    m = re.search(r"(?ms)^\[" + re.escape(section) + r"\]\s*$(.*?)(?=^\[|\Z)", text)
    if not m:
        return None
    k = re.search(r'(?m)^\s*' + re.escape(key) + r'\s*=\s*"([^"]+)"', m.group(1))
    return k.group(1) if k else None


def sources(root):
    """-> [{"place", "version" | None, "why"}] for every version statement found under root (top level and
    packaging/)."""
    root = Path(root)
    out = []

    def add(place, version, why=""):
        out.append({"place": place, "version": version, "why": why})

    cargo = root / "Cargo.toml"
    if cargo.is_file():
        t = _read(cargo)
        name = _toml_value(t, "package", "name")
        v = _toml_value(t, "package", "version") or _toml_value(t, "workspace.package", "version")
        if v is None and re.search(r'(?m)^\s*version\.workspace\s*=\s*true', t):
            v = None
        add("Cargo.toml", v, "" if v else "no [package] or [workspace.package] version")
        lock = root / "Cargo.lock"
        if lock.is_file() and name:
            m = re.search(r'(?ms)^\[\[package\]\]\s*\nname = "' + re.escape(name) + r'"\s*\nversion = "([^"]+)"',
                          _read(lock))
            add("Cargo.lock", m.group(1) if m else None, "" if m else f"no entry for package {name}")
    py = root / "pyproject.toml"
    if py.is_file():
        v = _toml_value(_read(py), "project", "version")
        add("pyproject.toml", v, "" if v else "no [project] version (dynamic?)")
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            v = json.loads(_read(pkg)).get("version")
        except (ValueError, AttributeError):
            v = None
        add("package.json", v, "" if v else "no version field")
    for pk in (root / "PKGBUILD", root / "packaging" / "PKGBUILD"):
        if pk.is_file():
            m = re.search(r"(?m)^pkgver=([^\s#]+)", _read(pk))
            add(str(pk.relative_to(root)), m.group(1) if m else None, "" if m else "no pkgver=")
    for cl in (root / "packaging" / "changelog.in", root / "debian" / "changelog"):
        if cl.is_file():
            m = re.search(r"^\S+ \(([^)\s]+?)(?:-\d+)?\)", _read(cl), re.M)
            add(str(cl.relative_to(root)), m.group(1) if m else None, "" if m else "no first entry")
    vf = root / "VERSION"
    if vf.is_file():
        m = SEMVER.search(_read(vf))
        add("VERSION", m.group(1) if m else None, "" if m else "no version in the file")
    for iss in sorted(list(root.glob("*.iss")) + list((root / "installer").glob("*.iss"))):
        t = _read(iss)
        # the #define first: `AppVersion={#AppVersion}` only refers to it (2026-10-19: read as the version)
        m = re.search(r'(?mi)^\s*#define\s+\w*Version\s+"([^"]+)"', t) or \
            re.search(r'(?mi)^\s*AppVersion\s*=\s*([^\s;]+)', t)
        add(str(iss.relative_to(root)), m.group(1) if m else None, "" if m else "no AppVersion or #define ...Version")
    # C/C++: a version constant in a header, or a Windows version resource (2026-10-19: TPFanControl's program said
    # 2.5.1-gorilla.4 in fancontrol.h while its installer script still said gorilla.3)
    for src in sorted(list(root.glob("*.h")) + list(root.glob("*/*.h")) + list(root.glob("*.rc")) +
                      list(root.glob("*/*.rc"))):
        t = _read(src)
        if isinstance(t, Exception):
            continue
        for m in re.finditer(r'(?m)^\s*(?:constexpr\s+[\w:<>\s*]+?|#define\s+)(\w*VERSION\w*)\s*=?\s*"(\d+\.\d+[^"\s]*)',
                             t):
            add(f"{src.relative_to(root).as_posix()} {m.group(1)}", m.group(2))
        for m in re.finditer(r'(?m)^\s*VALUE\s+"(?:FileVersion|ProductVersion)",\s*"(\d+[.,]\d+[^"]*)"', t):
            add(f"{src.relative_to(root).as_posix()} version resource", m.group(1).replace(",", ".").replace(" ", ""))
    ch = root / "CHANGELOG.md"
    if ch.is_file():
        m = re.search(r"(?m)^#{1,3}\s*\[?v?(\d+\.\d+(?:\.\d+)?[^\]\s]*)\]?", _read(ch))
        add("CHANGELOG.md", m.group(1) if m else None, "" if m else "no version heading")
    return out


def binary_version(exe):
    """-> (version or None, why): what the built program answers to --version."""
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    m = SEMVER.search(r.stdout + r.stderr)
    return (m.group(1), "") if m else (None, f"no version in its --version output: {(r.stdout + r.stderr).strip()[:80]}")


#: a placeholder a build fills in (@VERSION@, {#AppVersion}, ${pkgver}): not a version, and not a disagreement
TEMPLATE = re.compile(r"@\w+@|\{#\w+\}|\$\{?\w+\}?")


def check(root, expect=None, binary=None):
    rows = sources(root)
    for r in rows:
        if r["version"] and TEMPLATE.search(r["version"]):
            r.update(template=r["version"], version=None, why=f"filled at build time ({r['version']})")
    templated = [r for r in rows if r.get("template")]
    rows = [r for r in rows if not r.get("template")] + templated
    if binary:
        v, why = binary_version(binary)
        rows.append({"place": f"{Path(binary).name} --version", "version": v, "why": why})
    stated = {r["version"] for r in rows if r["version"]}
    unreadable = [r for r in rows if not r["version"] and not r.get("template")]
    want = expect or (stated.pop() if len(stated) == 1 else None)
    if expect:
        wrong = [r for r in rows if r["version"] and r["version"].lstrip("v") != expect.lstrip("v")]
    else:
        wrong = rows if len({r["version"] for r in rows if r["version"]}) > 1 else []
    # only templates (or nothing) is not a pass: no place states a version a person could check
    ok = bool(stated or want) and not wrong and not unreadable
    return {"ok": ok, "version": want, "places": rows, "disagree": [r["place"] for r in wrong],
            "unreadable": [r["place"] for r in unreadable]}


def lines(r):
    out = [f"  {row['place']:<34} {row['version'] or '?':<14} {row['why']}" for row in r["places"]]
    if not r["places"]:
        out.append("  no version statement found here")
    if r["ok"]:
        out.append(f"VERSION-SYNC OK: {len(r['places'])} place(s) say {r['version']}")
    else:
        out.append("VERSION-SYNC: " + "; ".join(
            ([f"disagree: {', '.join(r['disagree'])}"] if r["disagree"] else []) +
            ([f"unreadable: {', '.join(r['unreadable'])}"] if r["unreadable"] else []) +
            (["nothing found"] if not r["places"] else [])))
    return out


def main(argv=None, prog="fieldkit version-sync"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--expect", help="the version every place must state (e.g. the release being made)")
    ap.add_argument("--binary", help="also ask the built program: EXE --version")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not Path(a.path).is_dir():
        print(f"REFUSED: {a.path} is not a folder", file=sys.stderr)
        return 2
    r = check(a.path, a.expect, a.binary)
    print(json.dumps(r, indent=1) if a.json else "\n".join(lines(r)))
    return 0 if r["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
