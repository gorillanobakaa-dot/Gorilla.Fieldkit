# Version: 1.0.0 · updated 26-10-23-02-32
"""fieldkit doctor: what this machine still needs before the harness can work, and the exact line that installs it.

    fieldkit doctor [--for PIPELINE] [--lean] [--json]
    python -m fieldkit.core.doctor --probe [--json]     the machine itself, before anything is installed


Born 2026-10-10. Fieldkit's first run on a fresh Linux machine was set up by hand: pytest was missing, the package was
not installed, and rpmbuild was missing for the RPM stage; each was worked out by reading files. A small model would
have stopped at the first ImportError. This checks, without changing anything:

  - the Python version against pyproject.toml's requires-python;
  - every Python package pyproject.toml declares (the install, and the test extra), by its installed distribution;
  - the programs in requirements.json that "core" or the named pipeline needs, found on PATH or where Windows hides
    them (host.find_tool);
and prints one install line per missing thing for THIS system (Debian/Ubuntu, Fedora, Windows, macOS), then NEXT.

--probe (2026-10-23, the first Mac: a 2011 MacBook Air, macOS 10.13, 4 GB, cleaned of Homebrew, Xcode and git to save
memory) looks at the machine before anything else: the OS, memory, free disk, every Python 3 it can find (python.org
puts one in /usr/local/bin, which an ssh or cron PATH lacks), whether git is real or only Apple's stub that asks for
the developer tools, Homebrew, a C compiler, curl and unzip. It then picks the way in: "git" when git works, "zip"
when it does not (install.sh downloads GitHub's zip with curl, or the person downloads it in a browser), and "lean"
when memory is under 8 GB or there is no compiler (only the packages in requirements.json "lean"; every other one
serves a single tool and comes when that tool is wanted). --lean checks only those packages.

Exit 0 when nothing is missing, 3 when something is (findings). Nothing is installed by this command: a model shows
the lines, the person runs them.

It uses the standard library only, so it runs BEFORE Fieldkit is installed:  python -m fieldkit.core.doctor
"""
import glob
import json
import os
import re
import shutil
import subprocess
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
    if h.get("is_mac") or h.get("system") == "Darwin":
        return "mac"
    if is_debian_family():
        return "debian"
    if h["is_linux"] and (h.get("distro") in ("fedora", "rhel", "centos", "rocky", "almalinux")
                          or "fedora" in h.get("distro_like", []) or "rhel" in h.get("distro_like", [])):
        return "fedora"
    return "other"


def check(pipeline=None, pyproject=None, requirements=None, have_dist=None, which=None, h=None, lean=False,
          way=None):
    """-> {"ok", "family", "rows": [{kind, name, ok, detail, install}], "next"}.
    lean: only the packages of requirements.json "lean" are needed. way: "zip" makes git optional."""
    h = h or host()
    fam = _family(h)
    have_dist = have_dist or _installed
    which = which or find_tool
    rows = []
    pp = _pyproject(pyproject)
    proj = pp.get("project", {})
    req = json.loads(Path(requirements or REQUIREMENTS).read_text(encoding="utf-8")) or {}
    lean_specs = (req.get("lean") or {}).get("packages") or []

    need = proj.get("requires-python", "")
    m = re.match(r">=\s*(\d+)\.(\d+)", need)
    if m:
        got = tuple(int(x) for x in h["python"].split(".")[:2])
        want = (int(m.group(1)), int(m.group(2)))
        rows.append({"kind": "python", "name": "python", "ok": got >= want,
                     "detail": f"{h['python']}, need {need}",
                     "install": "" if got >= want else f"install Python {want[0]}.{want[1]} or later"})

    py = sys.executable
    if lean:
        specs = [(s, "lean") for s in lean_specs]
        lean_line = f'{py} -m pip install {" ".join(chr(34) + s + chr(34) for s in lean_specs)} && ' \
                    f'{py} -m pip install --no-deps -e "{ROOT}"'
    else:
        specs = [(s, "install") for s in proj.get("dependencies", [])]
        specs += [(s, "test") for s in proj.get("optional-dependencies", {}).get("test", [])]
    for spec, group in specs:
        name = _dist_name(spec)
        ver = have_dist(name)
        rows.append({"kind": "python-package", "name": name, "ok": ver is not None,
                     "detail": f"{ver} installed" if ver else f"missing ({spec}, {group})",
                     # one line installs the package and its test extra: the same line for every missing one
                     "install": "" if ver else (lean_line if lean else f'{py} -m pip install -e "{ROOT}[test]"')})

    for name, t in (req.get("tools") or {}).items():
        users = t.get("for") or []
        if "core" not in users and pipeline not in users:
            continue
        if t.get("only") and t["only"] != ("windows" if h["is_windows"] else "linux"):
            continue                                       # a tool of the other system: not asked for here
        path = which(name)
        # Apple's stub lives only on a Mac; on another family the check would run the REAL machine's git
        # (2026-10-23: three Linux-shaped tests failed when run on the Air, which has the stub)
        if path and name == "git" and fam == "mac" and git_state(path, system="darwin") == "stub":
            path = None                                    # Apple's stand-in that only asks for the developer tools
        if not path and way and t.get("optional_when") == way:
            rows.append({"kind": "tool", "name": name, "ok": True,
                         "detail": f"not needed when Fieldkit came as a {way} ({t.get('why', '')})", "install": ""})
            continue
        rows.append({"kind": "tool", "name": name, "ok": bool(path),
                     "detail": path or f"not found ({t.get('why', '')})",
                     "install": "" if path else (t.get("install") or {}).get(fam, f"install {name} for this system")})

    if pipeline:                                       # a Go app pipeline: its goapps.<name> entry (2026-10-27)
        from ..build import goapp
        row = goapp.doctor_row(pipeline)
        if row:
            rows.append(row)

    missing = [r for r in rows if not r["ok"]]
    # one line per command, in order; "a && b" becomes two lines: PowerShell 5.1 refuses && (the Windows side,
    # 2026-10-23, running the lean line from install.ps1)
    lines = list(dict.fromkeys(part.strip() for r in missing if r["install"] for part in r["install"].split(" && ")))
    nxt = ("run these, then python -m fieldkit doctor again: " + " ; ".join(lines)) if missing else \
          (("fieldkit pipeline run {} --only deps" if (ROOT / "fieldkit/build/pipelines" / f"{pipeline}.yaml").exists()
            else "fieldkit {}").format(pipeline) if pipeline else "fieldkit next PIPELINE")   # audio is a command
    return {"ok": not missing, "family": fam, "pipeline": pipeline, "rows": rows, "install": lines, "next": nxt}


# ---- the machine itself (--probe), before anything is installed ---------------------------------------------------
LEAN_BELOW_GB = 8           # less memory than this: the lean set (the 2011 Air has 4 GB and idles at 3 GB)
PY_PLACES = ["/usr/local/bin/python3*", "/Library/Frameworks/Python.framework/Versions/*/bin/python3",
             "/opt/homebrew/bin/python3*", "/usr/bin/python3*", os.path.expanduser("~/.local/bin/python3*"),
             os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe")]


def _run(argv, timeout=20):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)


def git_state(path=None, run=_run, system=None):
    """"real" | "stub" | "missing". On macOS /usr/bin/git exists even with no git: it only asks for Apple's developer
    tools ("no developer tools were found", seen on the 2011 Air 2026-10-23)."""
    path = path or shutil.which("git")
    if not path:
        return "missing"
    rc, out = run([path, "--version"])
    if rc == 0 and out.startswith("git version"):
        return "real"
    if "developer tools" in out or "xcode-select" in out or (system or sys.platform) == "darwin":
        return "stub"
    return "missing"


def memory_gb():
    try:
        if sys.platform == "darwin":
            return round(int(_run(["sysctl", "-n", "hw.memsize"])[1].strip()) / 2**30, 1)
        if sys.platform.startswith("linux"):
            kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
            return round(kb / 2**20, 1)
        if os.name == "nt":
            import ctypes
            class M(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                            ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
            m = M(); m.l = ctypes.sizeof(M)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.total / 2**30, 1)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def pythons(places=None):
    """Every Python 3 found, newest first, with its version: (path, (major, minor))."""
    found = {}
    for pat in (places or PY_PLACES) + [shutil.which("python3") or "", shutil.which("python") or ""]:
        for c in (glob.glob(pat) if any(ch in pat for ch in "*?") else [pat]):
            if not c or not os.path.isfile(c) or c.endswith("-config") or c.endswith("t") and c[-2].isdigit():
                continue
            real = os.path.realpath(c)
            if real in found:
                continue
            rc, out = _run([c, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"])
            m = re.search(r"(\d+)\D+(\d+)", out or "")
            # macOS 10.13's /usr/bin/python is Python 2: its print gives "(2, 7)" (2026-10-23); kept, never chosen
            if rc == 0 and m:
                found[real] = (c, (int(m.group(1)), int(m.group(2))))
    return sorted(found.values(), key=lambda pv: (-pv[1][0], -pv[1][1], len(pv[0])))   # same version: the plain name


def probe(run=_run, h=None):
    """-> facts about this machine and the way in (git or zip, lean or full), each with the reason."""
    h = h or host()
    mac = h.get("is_mac") or h.get("system") == "Darwin"
    pys = pythons()
    good = [p for p in pys if p[1] >= (3, 11)]
    gstate = git_state(run=run)
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc and mac:                       # on a Mac without the developer tools cc is the same stub as git
        rc, out = run([cc, "--version"])
        cc = cc if rc == 0 else None
    mem = memory_gb()
    disk = round(shutil.disk_usage(os.path.expanduser("~")).free / 2**30, 1)
    f = {"os": ("macOS " + __import__("platform").mac_ver()[0]) if mac else f"{h['system']} {h.get('release', '')}",
         "machine": h.get("machine"), "memory_gb": mem, "disk_free_gb": disk,
         "python": [f"{p} ({v[0]}.{v[1]})" for p, v in pys], "python_to_use": good[0][0] if good else None,
         "python_on_path": bool(shutil.which("python3")), "git": gstate, "brew": bool(shutil.which("brew")),
         "compiler": bool(cc), "curl": bool(shutil.which("curl")), "unzip": bool(shutil.which("unzip")) or os.name == "nt"}
    if mac:                              # owner, 2026-10-23: SIP is off on the 2011 Air; a fact the harness should see
        rc, out = run(["csrutil", "status"])
        f["sip"] = "disabled" if "disabled" in out else "enabled" if "enabled" in out else "unknown"
    way = "git" if gstate == "real" else "zip"
    lean = (mem is not None and mem < LEAN_BELOW_GB) or not cc
    why = []
    why.append("git works" if way == "git" else
               ("git here is only Apple's stub (it asks for the developer tools): download the zip" if gstate == "stub"
                else "no git: download the zip"))
    if lean:
        why.append(f"lean set: {'memory ' + str(mem) + ' GB' if mem is not None and mem < LEAN_BELOW_GB else ''}"
                   f"{' and ' if (mem is not None and mem < LEAN_BELOW_GB) and not cc else ''}"
                   f"{'no C compiler (only ready-built packages)' if not cc else ''}")
    if not good:
        why.append("no Python 3.11 or newer: " + ("python.org/downloads/macos (the 3.13 installer runs on macOS 10.13)"
                                                  if mac else "install Python 3.11+ for this system"))
    elif not f["python_on_path"] or (shutil.which("python3") and os.path.realpath(shutil.which("python3")) !=
                                     os.path.realpath(good[0][0])):
        why.append(f"use {good[0][0]} by its full path (python3 on PATH is missing or older)")
    return {"facts": f, "way": way, "lean": lean, "why": why, "ready_to_install": bool(good)}


def probe_lines(r):
    f = r["facts"]
    out = ["THIS COMPUTER",
           f"  system      {f['os']} ({f['machine']})",
           f"  memory      {f['memory_gb']} GB      free disk {f['disk_free_gb']} GB",
           f"  python      {', '.join(f['python']) or 'none found'}",
           f"  git         {f['git']}      Homebrew {'yes' if f['brew'] else 'no'}      C compiler {'yes' if f['compiler'] else 'no'}",
           f"  curl        {'yes' if f['curl'] else 'no'}      unzip {'yes' if f['unzip'] else 'no'}",
           *([f"  SIP         {f['sip']} (System Integrity Protection)"] if "sip" in f else []),
           f"WAY IN: {r['way']}{' + lean' if r['lean'] else ''}   (how to get Fieldkit here: git when git works, "
           "else the zip)"]
    out += [f"  because {w}" for w in r["why"]]
    if r["ready_to_install"]:
        py = f["python_to_use"]
        out.append("NEXT: " + (f"{py} -m fieldkit.core.doctor --lean" if r["lean"] else f"{py} -m fieldkit.core.doctor"))
    else:
        out.append("NEXT: install Python first (see above), then run this again")
    return out


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
    ap.add_argument("--lean", action="store_true", help="only the lean packages (a small or old computer)")
    ap.add_argument("--probe", action="store_true", help="the machine itself and the way in (git or zip, lean)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.probe:
        r = probe()
        print(json.dumps(r, indent=1) if a.json else "\n".join(probe_lines(r)))
        return 0 if r["ready_to_install"] else 3
    way = None if git_state() == "real" else "zip"
    r = check(a.pipeline, lean=a.lean, way=way)
    print(json.dumps(r, indent=1) if a.json else "\n".join(lines(r)))
    return 0 if r["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
