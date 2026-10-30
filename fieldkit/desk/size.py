# Version: 1.0.0 · updated 26-10-16-12-10
"""fieldkit size - Gorilla Size Calculation (gorilla-size) inside the harness.

How big folders are, where the space went, what can go: every core, read-only, no network. The program is the
standalone gorilla-size (Rust, AGPL, its own repository); Fieldkit gathers its source into toolbox/gorilla-size and
builds it here, offline, from the crates vendored in that source. Nothing is downloaded by the build.

    fieldkit size [ARGS ...]          run gorilla-size (fieldkit size --help for its options)
    fieldkit size doctor [--install]  is the program here and working? --install builds it (cargo, offline)
    fieldkit size where               the binary that would run

Born 2026-10-16: a model measured four Firefox trees with `du` (stopped after 25 minutes on one folder of 480,000
files), then with a hand-typed robocopy line, because the harness had no tool for "how big are these folders".
Use this, never an improvised one-liner.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import settings

EXE = "gorilla-size.exe" if os.name == "nt" else "gorilla-size"


def source_dir():
    """The gathered source (toolbox/gorilla-size), or FIELDKIT_GORILLA_SIZE_SRC."""
    env = os.environ.get("FIELDKIT_GORILLA_SIZE_SRC")
    return Path(env) if env else settings.ROOT / "toolbox" / "gorilla-size"


def bin_dir():
    return settings.state_root() / "state" / "bin"


def locate():
    """The binary that runs: FIELDKIT_GORILLA_SIZE, the one doctor --install built, the source's own build, PATH."""
    env = os.environ.get("FIELDKIT_GORILLA_SIZE")
    cands = [Path(env)] if env else []
    cands += [bin_dir() / EXE, source_dir() / "target" / "release" / EXE]
    for c in cands:
        if c.is_file():
            return c
    w = shutil.which("gorilla-size")
    return Path(w) if w else None


def version(exe):
    try:
        r = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except OSError as e:
        return None, str(e)
    out = (r.stdout or "").strip()
    return (out if r.returncode == 0 and out.startswith("gorilla-size ") else None), (r.stderr or out).strip()


def source_commit():
    """The commit the source is at: the gathered source's PROVENANCE.json, else (a local checkout named by
    FIELDKIT_GORILLA_SIZE_SRC) its git HEAD, short like PROVENANCE's, with "-dirty" when tracked files have changes so
    that it never matches a stamp. None when neither can be read.
    2026-10-18: a local checkout has no PROVENANCE.json, so doctor called a 0.2 binary OK against 0.3.0 source."""
    import json
    try:
        return json.loads((source_dir() / "PROVENANCE.json").read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError):
        pass
    if not (source_dir() / ".git").exists():
        return None
    try:
        head = subprocess.run(["git", "-C", str(source_dir()), "rev-parse", "--short=7", "HEAD"], capture_output=True,
                              text=True, timeout=30)
        dirty = subprocess.run(["git", "-C", str(source_dir()), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0 or not head.stdout.strip():
        return None
    return head.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")


def stamp():
    return bin_dir() / "gorilla-size.source"


def doctor(install=False, out=print):
    """-> 0 when the program runs and was built from the gathered source; 1 when it is missing, broken or STALE
    (built from an older source: 2026-10-16 the first doctor called an old binary OK after the source changed)."""
    exe = locate()
    want = source_commit()
    if exe:
        v, err = version(exe)
        built_from = stamp().read_text(encoding="utf-8").strip() if exe == bin_dir() / EXE and stamp().is_file() else None
        if v and (want is None or exe != bin_dir() / EXE or built_from == want):
            out(f"OK      {v} at {exe}" + (f" (source {want})" if want and built_from == want else ""))
            return 0
        if v:
            out(f"STALE   {v} at {exe} was built from source {built_from or 'unknown'}; the gathered source is {want}")
        else:
            out(f"BROKEN  {exe} does not answer --version: {err}")
    else:
        out("MISSING gorilla-size is not built on this machine")
    src = source_dir()
    if not (src / "Cargo.toml").is_file():
        out(f"        no source at {src}: run `fieldkit gather --only gorilla-size` first")
        return 1
    cargo = shutil.which("cargo")
    if not cargo:
        out("        no cargo (Rust) on this machine: install Rust (rustup), then `fieldkit size doctor --install`")
        return 1
    if not install:
        out("        FIX: fieldkit size doctor --install   (builds it from the gathered source, offline, --locked)")
        return 1
    out(f"        building in {src} (offline, --locked: only the vendored crates are used)")
    r = subprocess.run([cargo, "build", "--release", "--offline", "--locked"], cwd=src)
    built = src / "target" / "release" / EXE
    if r.returncode != 0 or not built.is_file():
        out("FAILED  the build did not produce the program (see the cargo output above)")
        return 1
    bin_dir().mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, bin_dir() / EXE)
    v, err = version(bin_dir() / EXE)
    if not v:
        out(f"FAILED  built, but it does not run: {err}")
        return 1
    if want:
        stamp().write_text(want, encoding="utf-8")
    out(f"OK      {v} installed at {bin_dir() / EXE}" + (f" (source {want})" if want else ""))
    return 0


def main(argv, prog="fieldkit size"):
    if argv[:1] == ["doctor"]:
        return doctor(install="--install" in argv[1:])
    if argv[:1] == ["where"]:
        exe = locate()
        print(exe if exe else "not built: fieldkit size doctor --install")
        return 0 if exe else 1
    if argv[:1] == ["record-help"]:
        return record_help()
    exe = locate()
    if not exe:
        if "--help" in argv or "-h" in argv:
            # a fresh download has no binary yet, but its options are still known (2026-10-19: every size card failed
            # the card check on GitHub CI and for a stranger, because --help needed the program)
            sub = next((a for a in argv if not a.startswith("-")), "")
            h = HELP_DIR / f"{sub or '_main'}.txt"
            if h.is_file():
                print(h.read_text(encoding="utf-8").rstrip())
                print(f"\n(gorilla-size is not built here yet: fieldkit size doctor --install. This help was recorded "
                      f"from the built program.)")
                return 0
        print(f"{prog}: gorilla-size is not built here. Run: fieldkit size doctor --install", file=sys.stderr)
        return 2
    # pass the streams explicitly: a console-less parent (a tool's pipe) starts the child with CREATE_NO_WINDOW and, with no
    # handle named, the child's output went nowhere (2026-10-30: `fieldkit size --help` printed nothing there)
    sys.stdout.flush()
    return subprocess.run([str(exe), *argv], stdout=sys.stdout, stderr=sys.stderr).returncode


HELP_DIR = Path(__file__).resolve().parent / "size_help"
#: the subcommands whose --help is recorded ("" is the program itself)
HELP_SUBS = ("", "find", "types", "newest", "watch", "free", "compare", "hash", "dupes", "empty", "reparse", "copy",
             "move", "recycle", "undo", "trash", "apply", "bench", "profile")


def record_help():
    """Write each subcommand's --help from the built program into size_help/ (run after a gorilla-size upgrade; the
    drift test in tests/ fails when a recorded help no longer matches the program)."""
    exe = locate()
    if not exe:
        print("record-help needs the built program: fieldkit size doctor --install", file=sys.stderr)
        return 2
    HELP_DIR.mkdir(exist_ok=True)
    for sub in HELP_SUBS:
        r = subprocess.run([str(exe), *([sub] if sub else []), "--help"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        (HELP_DIR / f"{sub or '_main'}.txt").write_bytes(r.stdout.replace("\r\n", "\n").encode("utf-8"))
    print(f"recorded {len(HELP_SUBS)} help text(s) in {HELP_DIR}")
    return 0
