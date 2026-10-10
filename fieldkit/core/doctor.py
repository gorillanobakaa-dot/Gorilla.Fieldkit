"""fieldkit doctor: what this machine still needs before the harness can work, and the exact line that installs it.

    fieldkit doctor [--for PIPELINE] [--json]

Born 2026-10-10. Fieldkit's first run on a fresh Linux machine was set up by hand: pytest was missing, the package was
not installed, and rpmbuild was missing for the RPM stage; each was worked out by reading files. A small model would
have stopped at the first ImportError. This checks, without changing anything:

  - the Python version against pyproject.toml's requires-python;
  - every Python package pyproject.toml declares (the install, and the test extra), by its installed distribution;
  - the programs in requirements.json that "core" or the named pipeline needs, found on PATH or where Windows hides
    them (host.find_tool);
and prints one install line per missing thing for THIS system (Debian/Ubuntu, Fedora, Windows), then NEXT.

Exit 0 when nothing is missing, 3 when something is (findings). Nothing is installed by this command: a model shows
the lines, the person runs them.

It uses the standard library only, so it runs BEFORE Fieldkit is installed:  python -m fieldkit.core.doctor
"""
import json
import re
import sys
from importlib import metadata
from pathlib import Path

from .host import find_tool, host, is_debian_family

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = Path(__file__).with_name("requirements.json")   # JSON: doctor must run before PyYAML is installed


def _pyproject(path=None):
    import tomllib
    with open(path or ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


def _dist_name(spec):
    """'pyyaml>=6' -> 'pyyaml'; 'fluent.syntax>=0.19' -> 'fluent.syntax'."""
    return re.split(r"[<>=!~;\[ ]", spec.strip(), maxsplit=1)[0]


def _family(h=None):
    h = h or host()
    if h["is_windows"]:
        return "windows"
    if is_debian_family():
        return "debian"
    if h["is_linux"] and (h.get("distro") in ("fedora", "rhel", "centos", "rocky", "almalinux")
                          or "fedora" in h.get("distro_like", []) or "rhel" in h.get("distro_like", [])):
        return "fedora"
    return "other"


def check(pipeline=None, pyproject=None, requirements=None, have_dist=None, which=None, h=None):
    """-> {"ok", "family", "rows": [{kind, name, ok, detail, install}], "next"}."""
    h = h or host()
    fam = _family(h)
    have_dist = have_dist or _installed
    which = which or find_tool
    rows = []
    pp = _pyproject(pyproject)
    proj = pp.get("project", {})

    need = proj.get("requires-python", "")
    m = re.match(r">=\s*(\d+)\.(\d+)", need)
    if m:
        got = tuple(int(x) for x in h["python"].split(".")[:2])
        want = (int(m.group(1)), int(m.group(2)))
        rows.append({"kind": "python", "name": "python", "ok": got >= want,
                     "detail": f"{h['python']}, need {need}",
                     "install": "" if got >= want else f"install Python {want[0]}.{want[1]} or later"})

    py = sys.executable
    specs = [(s, "install") for s in proj.get("dependencies", [])]
    specs += [(s, "test") for s in proj.get("optional-dependencies", {}).get("test", [])]
    for spec, group in specs:
        name = _dist_name(spec)
        ver = have_dist(name)
        rows.append({"kind": "python-package", "name": name, "ok": ver is not None,
                     "detail": f"{ver} installed" if ver else f"missing ({spec}, {group})",
                     # one line installs the package and its test extra: the same line for every missing one
                     "install": "" if ver else f'{py} -m pip install -e "{ROOT}[test]"'})

    req = json.loads(Path(requirements or REQUIREMENTS).read_text(encoding="utf-8")) or {}
    for name, t in (req.get("tools") or {}).items():
        users = t.get("for") or []
        if "core" not in users and pipeline not in users:
            continue
        if t.get("only") and t["only"] != ("windows" if h["is_windows"] else "linux"):
            continue                                       # a tool of the other system: not asked for here
        path = which(name)
        rows.append({"kind": "tool", "name": name, "ok": bool(path),
                     "detail": path or f"not found ({t.get('why', '')})",
                     "install": "" if path else (t.get("install") or {}).get(fam, f"install {name} for this system")})

    missing = [r for r in rows if not r["ok"]]
    lines = list(dict.fromkeys(r["install"] for r in missing if r["install"]))   # one line per command, in order
    nxt = ("run these, then python -m fieldkit doctor again: " + " ; ".join(lines)) if missing else \
          (("fieldkit pipeline run {} --only deps" if (ROOT / "fieldkit/build/pipelines" / f"{pipeline}.yaml").exists()
            else "fieldkit {}").format(pipeline) if pipeline else "fieldkit next PIPELINE")   # audio is a command
    return {"ok": not missing, "family": fam, "pipeline": pipeline, "rows": rows, "install": lines, "next": nxt}


def _installed(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def lines(r):
    out = [f"doctor: {r['family']} machine" + (f", for pipeline {r['pipeline']}" if r["pipeline"] else "")]
    for row in r["rows"]:
        out.append(f"  {'ok  ' if row['ok'] else 'MISS'} {row['kind']:<15} {row['name']:<16} {row['detail']}")
    if r["install"]:
        out.append("TO INSTALL (the person runs these; doctor installs nothing):")
        out += [f"  {l}" for l in r["install"]]
    out.append(("READY" if r["ok"] else f"NOT READY - {sum(not x['ok'] for x in r['rows'])} missing"))
    out.append(f"NEXT: {r['next']}")
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fieldkit.core.doctor")
    ap.add_argument("--for", dest="pipeline")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check(a.pipeline)
    print(json.dumps(r, indent=1) if a.json else "\n".join(lines(r)))
    return 0 if r["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
