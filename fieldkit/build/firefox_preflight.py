# Version: 0.1.0 · updated 26-10-14-19-40
"""Firefox preflight checks for Linux: the accumulated list of things that have broken a build, checked before
mach runs, each with the fix and the incident that made it a check.

Where they come from. The Windows owner repo (Gorilla.firefox, harness/lib/preflight.py, 64 checks, read from
commit 6f2d835) taught them first: every build failure there was diagnosed once and became a check. Of the 64,
16 touch only Windows (clang-cl, MozillaBuild, NSIS, Defender, MAX_PATH, ...). The rest are about the source tree,
the mozconfig and the machine, and are ported here one batch at a time, keeping the Windows id, severity and
`why` (the incident), with the Linux adaptation stated in `linux`. A check with no Windows ancestor says so.

This is meant to become the ONE home of these checks for both operating systems (one patch set, one register,
one preflight); until the Windows harness imports it, each ported check names its Windows original.

Severity: BLOCKER, the build cannot succeed and the stage refuses; WARN, it will work but something is wrong.
A check that raises is reported as failed, never as passed.
"""
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BLOCKER, WARN = "BLOCKER", "WARN"
WINDOWS_ORIGIN = "Gorilla.firefox harness/lib/preflight.py @ 6f2d835"
AGENT_MARKERS = ("CLAUDECODE", "CODEX_SANDBOX", "GEMINI_CLI", "OPENCODE")
LINUX_TRIPLE = "x86_64-pc-linux-gnu"


@dataclass
class Check:
    cid: str
    title: str
    severity: str
    fn: object
    remedy: str
    why: str
    linux: str = ""                      # what changed from the Windows original, or why it is Linux-only
    origin: str = WINDOWS_ORIGIN
    platforms: tuple = ("linux",)

    def run(self, ctx):
        try:
            return self.fn(ctx)
        except Exception as exc:          # a check must never crash the run, and never pass by crashing
            return False, f"check raised {type(exc).__name__}: {exc}"


@dataclass
class Ctx:
    root: Path                            # the harness folder: gorilla-firefox (mozconfig, patches/, decisions/)
    src: Path                             # the patched Firefox source tree
    objdir: Path = None                   # None: derived from the mozconfig
    env: dict = field(default_factory=lambda: dict(os.environ))
    which: object = shutil.which
    free_gb: object = None                # injectable for tests
    temps: object = None                  # injectable for tests


def mozconfig_lines(path):
    if not Path(path).is_file():
        return []
    out = []
    for l in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        l = l.split("#", 1)[0].strip()
        if l:
            out.append(l)
    return out


def objdir(ctx):
    """MOZ_OBJDIR from the mozconfig, else mach's default obj-<triple> inside the source tree."""
    if ctx.objdir:
        return Path(ctx.objdir)
    for l in mozconfig_lines(Path(ctx.root) / "mozconfig"):
        m = re.match(r"mk_add_options\s+MOZ_OBJDIR=(\S+)", l)
        if m:
            return Path(m.group(1).strip("\"'").replace("@TOPSRCDIR@", str(ctx.src)))
    return Path(ctx.src) / f"obj-{LINUX_TRIPLE}"


# ---------------------------------------------------------------------------------------------- the checks

def _c_source(ctx):
    v = Path(ctx.src) / "browser" / "config" / "version.txt"
    if not v.is_file():
        return False, f"no source tree at {ctx.src}"
    return True, "Firefox " + v.read_text(encoding="utf-8").strip()


def _c_mozconfig(ctx):
    m = Path(ctx.root) / "mozconfig"
    return m.is_file(), str(m) if m.is_file() else f"missing {m}"


def _c_branding(ctx):
    want = None
    for l in mozconfig_lines(Path(ctx.root) / "mozconfig"):
        if "--with-branding=" in l:
            want = l.split("--with-branding=", 1)[1].strip()
    if not want:
        return True, "no --with-branding set (upstream default)"
    d = Path(ctx.src) / want
    if not d.is_dir():
        return False, f"{want} MISSING"
    return True, f"{want} ({sum(1 for _ in d.rglob('*'))} files)"


def _c_bundled_files(ctx):
    mb = Path(ctx.src) / "browser" / "fonts" / "moz.build"
    if not mb.is_file():
        return True, "no browser/fonts/moz.build"
    listed = re.findall(r'"([^"]+\.(?:ttf|ttc|otf))"', mb.read_text(encoding="utf-8"))
    missing = [f for f in listed if not (mb.parent / f).exists()]
    if missing:
        return False, "browser/fonts missing: " + ", ".join(missing[:4])
    return True, f"{len(listed)} bundled font(s), all present"


def _c_clobber(ctx):
    src_c = Path(ctx.src) / "CLOBBER"
    if not src_c.is_file():
        return True, "no CLOBBER file in tree"
    od = objdir(ctx)
    if not od.is_dir():
        return True, "no objdir yet (clean build)"
    obj_c = od / "CLOBBER"
    if not obj_c.is_file():
        return True, "objdir has no CLOBBER record yet"
    # mtimes, not contents: the objdir CLOBBER is a zero-byte marker (the Windows check's first outing)
    d = src_c.stat().st_mtime - obj_c.stat().st_mtime
    if d > 0:
        return False, f"tree CLOBBER is newer than the objdir marker by {d:.0f}s - a clobber is required"
    return True, "objdir marker is current"


def _c_stale_objdir(ctx):
    real = objdir(ctx).resolve()
    stale = [d for d in sorted(Path(ctx.src).glob("obj-*")) if d.is_dir() and d.resolve() != real]
    if not stale:
        return True, "no abandoned objdir beside the source tree"
    return False, "abandoned objdir(s) present: " + ", ".join(
        d.name + (" (configured)" if (d / "config.status").exists() else "") for d in stale)


def _c_base_tools(ctx):
    missing = [t for t in ("git", "rustc", "cargo", "node", "python3") if not ctx.which(t)]
    return not missing, "all present" if not missing else "missing: " + ", ".join(missing)


def _c_agent_env(ctx):
    present = [v for v in AGENT_MARKERS if ctx.env.get(v)]
    if not present:
        return True, "no agent markers set"
    return False, ", ".join(present) + " set - the build stage strips these so mach reports errors"


def _c_disk(ctx):
    from .firefox_linux import MIN_GB, _free_gb
    od = objdir(ctx)
    at = od if od.exists() else Path(ctx.src) if Path(ctx.src).exists() else Path.home()
    gb = (ctx.free_gb or _free_gb)(str(at))
    return gb >= MIN_GB, f"{gb:.0f} GB free at {at} (need {MIN_GB})"


def hwmon_temps(base=Path("/sys/class/hwmon")):
    """-> [(sensor name, degrees C)] from every readable hwmon temp*_input."""
    out = []
    for h in sorted(Path(base).glob("hwmon*")):
        name = (h / "name").read_text().strip() if (h / "name").is_file() else h.name
        for t in sorted(h.glob("temp*_input")):
            try:
                out.append((name, int(t.read_text().strip()) / 1000))
            except (OSError, ValueError):
                continue
    return out


CPU_SENSORS = ("coretemp", "k10temp", "zenpower")


def _c_temp_source(ctx):
    got = (ctx.temps or hwmon_temps)()
    cpu = [x for x in got if x[0] in CPU_SENSORS]
    if cpu:
        return True, f"{cpu[0][0]} {max(t for _, t in cpu):.0f} C"
    if got:
        return False, "no CPU sensor, only " + ", ".join(sorted({n for n, _ in got}))
    return False, "no temperature source readable"


CHECKS = [
    Check("source", "Source tree", BLOCKER, _c_source, "Restore the working copy from the vault.",
          "Obvious, but worth failing fast rather than inside mach."),
    Check("mozconfig", "mozconfig", BLOCKER, _c_mozconfig, "Restore mozconfig in the harness folder.",
          "Without it the build silently uses upstream defaults - unbranded, untrimmed.",
          linux="Windows reads config/mozconfig.win64 in the owner repo; Linux reads the patch set's own mozconfig."),
    Check("branding", "Branding directory", BLOCKER, _c_branding,
          "Port group 08.Look: its NEW_FILES supply browser/branding/gorilla.",
          "The mozconfig names a branding dir. If the patches did not land it, configure fails on a path that "
          "does not exist."),
    Check("bundled-files", "Bundled files exist", BLOCKER, _c_bundled_files,
          "Run the patch set's get-microsoft-fonts.sh, or drop the patch that lists the missing files.",
          "2026-09-09 (Windows): configure aborted with 'File listed in FINAL_TARGET_FILES does not exist: "
          "browser/fonts/consola.ttf'. 11.FONT.SYSTEM bundles fonts that Debian fetches via get-microsoft-fonts.sh.",
          linux="On Linux the fonts ship inside the .deb (decided), so a missing one is fetched, never dropped."),
    Check("clobber", "Objdir vs CLOBBER", BLOCKER, _c_clobber, "Delete the objdir (or ./mach clobber) and rebuild.",
          "Standard Firefox behaviour: some moz.build changes invalidate the build backend and incremental builds "
          "cannot reconcile them. The first version compared contents and called every healthy objdir stale."),
    Check("stale-objdir", "No abandoned objdir", WARN, _c_stale_objdir,
          "Move it to ~/Agents.Work.Trash/: the build reads the mozconfig's objdir and nothing else.",
          "2026-09-09 (Windows): MOZ_OBJDIR moved and the old objdir was left behind; the package stage guessed "
          "the objdir, found the stale tree and recorded old binaries, with hashes, as the build's artifacts.",
          linux="The Linux mozconfig sets no MOZ_OBJDIR, so the real objdir is mach's obj-x86_64-pc-linux-gnu."),
    Check("base-tools", "git/rustc/cargo/node/python3", BLOCKER, _c_base_tools, "Install whatever is missing.",
          "Gecko's build needs all of them.", linux="python3, not python: Debian ships no `python` by default."),
    Check("agent-env", "Coding-agent env markers", WARN, _c_agent_env,
          "Nothing to fix - the build stage strips them from the child environment.",
          "2026-09-09 (Windows), and ERROR 4 of the Linux 154 build (2026-07-15): mozbuild forces quiet mode when "
          "CLAUDECODE/CODEX_SANDBOX/GEMINI_CLI/OPENCODE are set, so a failing build printed only '*** Fix above "
          "errors'."),
    Check("disk", "Disk space for the objdir", BLOCKER, _c_disk, "Free space where the objdir lives.",
          "A build that runs out of disk dies late, after an hour of compiling.",
          linux="Measured where the objdir is (or the source tree before the first build); the floor is "
                "recreate.sh's 20 GB."),
    Check("temp-source", "Live temperature source", WARN, _c_temp_source,
          "Load the coretemp module (sudo modprobe coretemp) so the CPU temperature is readable.",
          "Without a live sensor the build cannot be verified as thermally safe.",
          linux="Windows reads a performance counter (MSAcpi is static there); Linux reads hwmon, and only a CPU "
                "sensor (coretemp/k10temp) counts, since acpitz is a board zone."),
]


def run(ctx, checks=None):
    rows = []
    for c in checks or CHECKS:
        if "linux" not in c.platforms:
            continue
        ok, detail = c.run(ctx)
        rows.append({"id": c.cid, "title": c.title, "severity": c.severity, "ok": bool(ok), "detail": detail,
                     "remedy": None if ok else c.remedy})
    blockers = [r["id"] for r in rows if not r["ok"] and r["severity"] == BLOCKER]
    return {"ok": not blockers, "rows": rows, "blockers": blockers,
            "warnings": [r["id"] for r in rows if not r["ok"] and r["severity"] == WARN]}


def lines(res):
    out = []
    for r in res["rows"]:
        mark = "ok  " if r["ok"] else ("FAIL" if r["severity"] == BLOCKER else "warn")
        out.append(f"  {mark} {r['id']:<16} {r['detail']}")
        if not r["ok"]:
            out.append(f"       fix: {r['remedy']}")
    out.append("PREFLIGHT " + ("OK" if res["ok"] else "REFUSED: " + ", ".join(res["blockers"]))
               + (f" ({len(res['warnings'])} warning(s))" if res["warnings"] else ""))
    return out


def stage_preflight(ctx):
    """Pipeline stage: every BLOCKER must pass before mach runs."""
    from .firefox_linux import _v
    res = run(Ctx(root=Path(_v(ctx, "harness")), src=Path(_v(ctx, "src"))))
    return {"ok": res["ok"], "rows": [{"check": r["id"], "ok": r["ok"], "detail": r["detail"]} for r in res["rows"]],
            "detail": "\n".join(lines(res))}
