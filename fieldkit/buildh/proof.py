"""Production proof of the INSTALLED browser against the owner's intent in the ported tree.

The build harness proves that patches applied; `build-verify` proves the package is branded and the binary says the
version; neither proves that a tweak is in force in the browser a person runs. 2026-10-02: two modules that did not
parse and one that read enums from the wrong module all built green and shipped with a dead address bar. These rows
read the shipped artefacts (omni.ja, greprefs.js, firefox.js) and the running browser's stderr, never the source.

Rows (each {check, ok, evidence}):
  prefs     every pref the port sets or changes in all.js / firefox.js (vs the pristine upstream file) is in the shipped
            defaults with the same value and lock. Prefs inside #if blocks in the source are not judged (the shipped
            file is preprocessed; a pref under an inactive #ifdef is rightly absent) and are counted separately.
  excised   nothing the owner's DELETED_FILES manifests remove, and nothing under an excised component directory,
            is packaged in omni.ja or browser/omni.ja.
  startup   the installed browser, headless, on a throwaway profile: no SyntaxError / missing actor / ReferenceError,
            no "Missing chrome or resource URL", no "Error in processing <category>" (a hook naming a removed module).
"""
import re
import subprocess
import zipfile
from pathlib import Path

PREF = re.compile(r'^\s*pref\(\s*"([^"]+)"\s*,\s*(.*?)\s*(?:,\s*(locked|sticky))?\s*\)\s*;')
PRE_IF = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
PRE_END = re.compile(r"^\s*#\s*endif\b")
PREF_SOURCES = (("modules/libpref/init/all.js", "greprefs.js", "omni.ja"),
                ("browser/app/profile/firefox.js", "defaults/preferences/firefox.js", "browser/omni.ja"))
EXCISED_DIRS = ("browser/components/aiwindow/", "browser/components/genai/", "toolkit/components/ml/")


def _norm(v):
    v = v.strip()
    return v.strip('"') if v.startswith('"') else v


def pref_lines(text, judge_conditionals=False):
    """-> {name: (value, flag, conditional)}; the last line for a name wins, as libpref does."""
    out, depth = {}, 0
    for line in text.splitlines():
        if PRE_IF.match(line):
            depth += 1
        elif PRE_END.match(line):
            depth = max(0, depth - 1)
        m = PREF.match(line)
        if m:
            out[m.group(1)] = (_norm(m.group(2)), m.group(3) or "", depth > 0)
    return out


def pref_intent(workdir):
    """{name: (value, flag, conditional, source)}: prefs whose line the port added or changed against pristine upstream."""
    w = Path(workdir)
    root = subprocess.run(["git", "-C", str(w), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    intent = {}
    for src, _, _ in PREF_SOURCES:
        now = pref_lines((w / src).read_text(encoding="utf-8", errors="replace"))
        before = {}
        if root:
            r = subprocess.run(["git", "-C", str(w), "show", f"{root[0]}:{src}"], capture_output=True)
            if r.returncode == 0:
                before = pref_lines(r.stdout.decode("utf-8", "replace"))
        for name, (val, flag, cond) in now.items():
            if before.get(name, (None, None, None))[:2] != (val, flag):
                intent[name] = (val, flag, cond, src)
    return intent


def shipped_prefs(install_dir):
    """{name: (value, flag)} as the installed browser's defaults have them (firefox.js wins over greprefs.js)."""
    out = {}
    for _, member, ja in PREF_SOURCES:
        try:
            with zipfile.ZipFile(Path(install_dir) / ja) as z:
                text = z.read(member).decode("utf-8", "replace")
        except (OSError, KeyError):
            continue
        for name, (val, flag, _) in pref_lines(text).items():
            out[name] = (val, flag)
    return out


def prefs_row(workdir, install_dir):
    intent = pref_intent(workdir)
    have = shipped_prefs(install_dir)
    bad, unjudged, ok = [], 0, 0
    later = {}                                            # prefs firefox.js also sets: the later file wins at runtime
    try:
        with zipfile.ZipFile(Path(install_dir) / "browser/omni.ja") as z:
            later = pref_lines(z.read("defaults/preferences/firefox.js").decode("utf-8", "replace"))
    except (OSError, KeyError):
        pass
    for name, (val, flag, cond, src) in sorted(intent.items()):
        got = have.get(name)
        if got == (val, flag):
            ok += 1
        elif cond:
            unjudged += 1                                  # the source line sits under #if: the shipped file decided
        else:
            shipped = (got[0] + (" " + got[1] if got[1] else "")) if got else "ABSENT"
            why = " (overridden: firefox.js loads after greprefs.js and sets it again)" if src.endswith("all.js") and name in later else ""
            bad.append(f"{name}: want {val}{' ' + flag if flag else ''}, shipped {shipped}{why}")
    return {"check": "prefs: every pref the port sets is in the shipped defaults with that value", "ok": not bad,
            "evidence": f"{ok} in force, {unjudged} under #if not judged" + (f"; {len(bad)} wrong: {bad[:3]}" if bad else ""),
            "bad": bad}


def excised_row(install_dir, deleted_paths):
    names = {Path(p).name for p in deleted_paths if p.endswith((".mjs", ".js", ".jsm", ".ftl", ".css", ".html", ".xhtml"))}
    hits = []
    for ja in ("omni.ja", "browser/omni.ja"):
        try:
            with zipfile.ZipFile(Path(install_dir) / ja) as z:
                for n in z.namelist():
                    if Path(n).name in names or any(d.split("/")[-2] + "/" in n for d in EXCISED_DIRS):
                        hits.append(f"{ja}:{n}")
        except OSError:
            hits.append(f"{ja}: unreadable")
    return {"check": "excised: nothing the port removes is packaged", "ok": not hits,
            "evidence": f"{len(names)} removed module names and {len(EXCISED_DIRS)} directories checked" + (f"; packaged: {hits[:3]}" if hits else ""),
            "bad": hits}


STARTUP_BAD = re.compile(r"JavaScript error:.*(SyntaxError|No such JSWindowActor|ReferenceError|is not defined)"
                         r"|Missing chrome or resource URL|Error in processing [\w-]+ for ")


def startup_row(install_dir, seconds=25):
    """Headless start on a throwaway profile; stderr+stdout read for the three classes above."""
    import tempfile, time
    exe = Path(install_dir) / "firefox.exe"
    prof = Path(tempfile.mkdtemp(prefix="gproof_"))
    (prof / "user.js").write_text("\n".join([
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("browser.aboutwelcome.enabled", false);',
        'user_pref("devtools.console.stdout.chrome", true);', ""]), encoding="utf-8")
    with open(prof / "stderr.txt", "wb") as err, open(prof / "stdout.txt", "wb") as out:
        proc = subprocess.Popen([str(exe), "-headless", "-no-remote", "-profile", str(prof), "about:blank"], stdout=out, stderr=err)
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    text = (prof / "stderr.txt").read_text(encoding="utf-8", errors="replace") + (prof / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    lines = sorted({l.strip()[:200] for l in text.splitlines() if STARTUP_BAD.search(l)})
    return {"check": "startup: headless, no module/actor errors, no missing URLs, no dead category hooks", "ok": not lines,
            "evidence": f"{seconds} s headless; {len(lines)} bad line(s)" + (f": {lines[0]}" if lines else ""), "bad": lines, "log": str(prof)}


def rows(workdir, install_dir, deleted_paths=(), which=("prefs", "excised", "startup")):
    out = []
    if "prefs" in which:
        out.append(prefs_row(workdir, install_dir))
    if "excised" in which:
        out.append(excised_row(install_dir, deleted_paths))
    if "startup" in which:
        out.append(startup_row(install_dir))
    return out
