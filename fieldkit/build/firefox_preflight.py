# Version: 0.1.0 · updated 26-10-15-03-33
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
    vaio: object = None                   # injectable for tests: a sensors.gorilla snapshot (fieldkit.thermal.vaio)


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



def required_fonts(root):
    """The fonts the package gate demands, read from the gate itself (scripts/release_gate.py REQUIRED_FONTS): one
    list, so the check before the build and the gate after it can never disagree. None when the gate is absent."""
    g = Path(root) / "scripts" / "release_gate.py"
    if not g.is_file():
        return None
    m = re.search(r"REQUIRED_FONTS\s*=\s*\[(.*?)\]", g.read_text(encoding="utf-8", errors="replace"), re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else None


def _c_package_fonts(ctx):
    want = required_fonts(ctx.root)
    if want is None:
        return True, "no release gate with REQUIRED_FONTS in the harness folder: nothing to compare"
    fonts = Path(ctx.src) / "browser" / "fonts"
    mb = fonts / "moz.build"
    listed = set(re.findall(r'"([^"]+\.(?:ttf|ttc|otf))"', mb.read_text(encoding="utf-8"))) if mb.is_file() else set()
    absent = [f for f in want if not (fonts / f).is_file()]
    unlisted = [f for f in want if f not in listed]
    if absent or unlisted:
        bits = []
        if absent:
            bits.append("not in browser/fonts: " + ", ".join(absent))
        if unlisted:
            bits.append("not listed in browser/fonts/moz.build: " + ", ".join(unlisted))
        return False, "; ".join(bits)
    return True, f"all {len(want)} fonts the package gate requires are present and listed"

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
    # the owner's sensors.gorilla first (2026-10-15): it also knows the Sony thermal profile, the fan daemon and
    # throttling since boot, which plain hwmon does not
    if ctx.vaio is not None or ctx.temps is None:
        try:
            from ..thermal import vaio
            s = ctx.vaio if ctx.vaio is not None else vaio.snapshot()
        except Exception:                                    # noqa: BLE001 - fall back to hwmon below
            s = {"tool": None}
        if s.get("tool") and s.get("package_c") is not None:
            worry = []
            if s.get("daemon") not in (None, "active"):
                worry.append(f"thermal-profile-daemon is {s['daemon']} (fan profiles are not stepped)")
            if s.get("throttle_events"):
                worry.append(f"the CPU throttled {s['throttle_events']} time(s) since boot")
            text = (f"sensors.gorilla: package {s['package_c']} C, fan {s.get('fan_pct')}%, profile {s.get('profile')}, "
                    f"daemon {s.get('daemon')}, {s.get('package_w')} W")
            return (not worry), text + ("" if not worry else "; " + "; ".join(worry))
    got = (ctx.temps or hwmon_temps)()
    cpu = [x for x in got if x[0] in CPU_SENSORS]
    if cpu:
        return True, f"{cpu[0][0]} {max(t for _, t in cpu):.0f} C"
    if got:
        return False, "no CPU sensor, only " + ", ".join(sorted({n for n, _ in got}))
    return False, "no temperature source readable"


# The VAIO build: every Gorilla Linux build is compiled for the machine it runs on. The tuned mozconfig carries it, and
# SETTLED.md says "-march=native -O3 ... not up for debate". Losing one of these is silent: the browser still builds,
# only slower or untuned (Windows, 2026-09: sccache dropped from a rewritten mozconfig, eight builds from scratch).
TUNING = [
    ("-O3 -march=native for C and C++", "--enable-optimize=\"-O3 -march=native -mtune=native\""),
    ("Rust tuned for this CPU", "-C target-cpu=native"),
    ("the sccache compile cache", "--with-ccache=sccache"),
    ("ThinLTO", "--enable-lto=thin"),
    ("the lld linker", "--enable-linker=lld"),
    ("parallel jobs set (MOZ_MAKE_FLAGS)", "MOZ_MAKE_FLAGS="),
]


def _c_vaio_tuning(ctx):
    m = Path(ctx.root) / "mozconfig"
    live = "\n".join(mozconfig_lines(m))                  # active lines only: a commented-out option is a lost option
    missing = [what for what, needle in TUNING if needle not in live]
    if missing:
        return False, "the mozconfig lost: " + ", ".join(missing)
    have_sccache = bool(ctx.which("sccache"))
    return have_sccache, "native tuning, sccache, ThinLTO, lld and job count all set" + (
        "" if have_sccache else "; but the sccache program is not installed")


def _patchset_version(root):
    """The Firefox version the patch set is for, from config/patch_policy.json or the port state notes."""
    for p in sorted((Path(root) / "intents").glob("port-*/STATE.md")):
        return p.parent.name.split("port-", 1)[1]
    return None


def _c_recreate_current(ctx):
    r = Path(ctx.root) / "recreate.sh"
    if not r.is_file():
        return False, "recreate.sh is missing: other people have no way to build it themselves"
    text = r.read_text(encoding="utf-8", errors="replace")
    want = _patchset_version(ctx.root)
    if re.search(r"nightly|a1\b", text, re.I) and not (want and want in text):
        return False, (f"recreate.sh still builds a Nightly (it names 154.0a1), the patch set is for {want or 'a stable release'}: "
                       "it must clone the stable tag the patch set was ported to")
    if want and want not in text:
        return False, f"recreate.sh does not name {want}, the version the patch set is for"
    return True, f"recreate.sh builds {want}"


def _c_deb_packaging(ctx):
    root = Path(ctx.root)
    need = ["scripts/build_deb.sh", "scripts/fonts-microsoft.sh",
            "deb_template/usr/share/icons/hicolor/1024x1024/apps/gorilla-unleashed.png",
            "deb_template/usr/share/applications/gorilla-unleashed.desktop"]
    missing = [x for x in need if not (root / x).is_file()]
    return not missing, "packaging pieces present" if not missing else "missing: " + ", ".join(missing)



def _c_api_drift(ctx):
    from . import apidrift
    r = apidrift.scan(ctx.src)
    if r.get("error"):
        return True, "not a ported work tree (no pristine root commit): nothing to compare"
    if r["ok"]:
        return True, f"{r['rules']} known API change(s) apply, none left in {r['files']} ported C/C++ file(s)"
    return False, "; ".join(f"{x['file']} uses {x['rule']} x{x['count']}" for x in r["found"])

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
    Check("package-fonts", "Fonts the package gate requires", BLOCKER, _c_package_fonts,
          "Copy the fonts from an earlier build tree or run the patch set's get-microsoft-fonts.sh (FF_SRC=<tree>); "
          "the moz.build listing is patchset-155.0b4/11.FONT.SYSTEM/browser_fonts_moz.build.patch on Linux.",
          "2026-10-15 (Linux 158): the build compiled in 87 minutes and the package gate then refused FONT-001: the 157 "
          "set came from Windows, which excludes browser/fonts/moz.build, and bundled-files only checked the fonts "
          "moz.build already listed (1 of 8). The list now comes from the gate, so both ends agree.",
          linux="Fonts ship inside the .deb on Linux (settled 2026-08-03); the public repo carries only the fetch script."),
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
    Check("api-drift", "Upstream API changes in ported code", BLOCKER, _c_api_drift,
          "~/.local/bin/fieldkit build-harness api-drift TASK --apply rewrites them (table: build/api-drift/firefox.yaml).",
          "2026-10-15 (Linux 158): the second build ran 17 minutes and stopped on FindInReadable(pattern, source, "
          "comparator), an overload 158 removed. Every known rename is now caught before the compiler starts.",
          linux="Born on Linux; the table is plain data so the Windows harness can read the same rows."),
    Check("disk", "Disk space for the objdir", BLOCKER, _c_disk, "Free space where the objdir lives.",
          "A build that runs out of disk dies late, after an hour of compiling.",
          linux="Measured where the objdir is (or the source tree before the first build); the floor is "
                "recreate.sh's 20 GB."),
    Check("temp-source", "Live temperature source", WARN, _c_temp_source,
          "Load the coretemp module (sudo modprobe coretemp); start thermal-profile-daemon (systemctl start "
          "thermal-profile-daemon); a throttled CPU means a cooler room or a lower -j before compiling.",
          "Without a live sensor the build cannot be verified as thermally safe.",
          linux="Windows reads a performance counter (MSAcpi is static there); Linux reads hwmon, and only a CPU "
                "sensor (coretemp/k10temp) counts, since acpitz is a board zone."),
    Check("vaio-tuning", "Native tuning in the mozconfig", BLOCKER, _c_vaio_tuning,
          "Put the line back in the harness folder's mozconfig (see TUNING in fieldkit/build/firefox_preflight.py).",
          "Every Gorilla Linux build is compiled for the machine it runs on (-O3 -march=native, Rust target-cpu=native, "
          "sccache, ThinLTO, lld): SETTLED.md, not up for debate. A lost line builds fine and silently slower; on Windows "
          "sccache vanished from a rewritten mozconfig and eight builds recompiled from scratch.",
          origin="Linux, 2026-10-15 (owner: the build is tuned to run natively on the Sony VAIO); Windows sccache check",
          linux="the owner's build and recreate.sh share this mozconfig: native for whichever machine compiles it"),
    Check("recreate-current", "recreate.sh builds the same release", WARN, _c_recreate_current,
          "Point recreate.sh at the stable tag the patch set was ported to (it must clone that tag, never Nightly).",
          "recreate.sh is how other people compile Gorilla themselves (\"recreate the success\"). On 2026-10-15 it still "
          "cloned Nightly 154.0a1 while the patch set was for stable 158: anyone following it built the wrong thing.",
          origin="Linux, 2026-10-15 (owner)", linux="becomes a BLOCKER for publishing (S9)"),
    Check("deb-packaging", "Packaging pieces", BLOCKER, _c_deb_packaging,
          "Restore the missing file in the harness folder (scripts/ and deb_template/).",
          "The .deb carries the Gorilla name, the large crisp icon (D-157-35: one master, never drawn small), the "
          "desktop entry and the Microsoft fonts (they ship inside the .deb, decided). A missing piece fails at package "
          "time, after hours of compiling.",
          origin="Linux, 2026-10-15 (owner: naming, crisp icons, install and packaging must not be forgotten)"),
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
