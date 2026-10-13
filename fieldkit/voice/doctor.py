"""What this machine has, what it needs, and crucially HOW IT SOUNDS.

The grading is the point. A check that only confirms a speech program exists
returns ok for espeak, which is why Linux sounded like 1995 here for months
while the check said everything was fine. See note 9 in __init__.py.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Engines ranked by what a person actually hears, worst first. "modern" is the
# only grade that does not warrant a warning.
GRADE = {
    "espeak-ng": ("robotic", "formant synthesis, 1980s lineage. Works, but metallic."),
    "espeak": ("robotic", "formant synthesis, older still."),
    "flite": ("robotic", "festival-lite, 2001."),
    "festival": ("dated", "diphone concatenation, 1996."),
    "mbrola": ("dated", "diphone concatenation, 1996. Better than espeak, still metallic."),
    "pico": ("dated", "SVOX Pico, from early Android."),
    "piper": ("modern", "neural. This is the one worth having."),
}

HOME = Path.home()
PIPER_DIR = HOME / ".local/share/piper"
SD_USER_DIR = HOME / ".config/speech-dispatcher"


def _run(args, timeout=20):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:                                    # fail closed: unknown is not ok
        return 1, str(e)


def _onnxruntime_present():
    """Is onnxruntime already installed anywhere we could reuse? 55 MB saved if so (note 3)."""
    for py in (sys.executable, shutil.which("python3"), shutil.which("python")):
        if not py:
            continue
        # stdout ONLY: onnxruntime prints a GPU-probe warning to stderr on machines
        # with no discrete graphics, and _run merges the streams, so reading both
        # put that warning in the version field.
        try:
            r = subprocess.run([py, "-c", "import onnxruntime;print(onnxruntime.__version__)"],
                               capture_output=True, text=True, timeout=20)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout.strip():
            return {"found": True, "python": py, "version": r.stdout.strip().splitlines()[-1]}
    return {"found": False}


def doctor(platform=None):
    """-> a report. Never raises: a check that cannot run is a finding, not a crash."""
    plat = platform or sys.platform
    if plat == "win32":
        from . import WHY_LINUX_ONLY
        return {"platform": "windows", "status": "not-this-platform", "why": WHY_LINUX_ONLY,
                "checks": [], "missing": [], "ok": True}

    from . import VOICE
    checks, missing = [], []

    def check(name, ok, detail, fix=None, fatal=True):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok and fatal:
            missing.append({"name": name, "fix": fix or ""})
        return ok

    # --- the machine ------------------------------------------------------
    check("python", sys.version_info >= (3, 10), f"{sys.version.split()[0]}, need >= 3.10")
    check("debian-family", Path("/etc/debian_version").exists(),
          "apt is available" if Path("/etc/debian_version").exists() else "not a Debian-family system",
          "this installer uses apt; on another distro install sox and speech-dispatcher by hand", fatal=False)

    # --- players: the aplay/paplay trap (note 1) ---------------------------
    has_paplay, has_aplay = bool(shutil.which("paplay")), bool(shutil.which("aplay"))
    check("paplay", has_paplay,
          "present: audio goes THROUGH PipeWire, so the owner's loudness filter applies"
          if has_paplay else "MISSING: without it audio must use aplay, which bypasses PipeWire and is quieter",
          "sudo apt install pulseaudio-utils")
    if has_aplay and not has_paplay:
        checks.append({"name": "player-warning", "ok": False,
                       "detail": "only aplay is present. It talks straight to ALSA and SKIPS any loudness "
                                 "filter chain, which sounds like a volume problem and is not one."})

    # --- the loudness chain the owner may already have ---------------------
    rc, sinks = _run(["pactl", "list", "sinks", "short"])
    loud = "loudness" in sinks.lower()
    checks.append({"name": "loudness-filter", "ok": True,
                   "detail": "a loudness filter chain is present; play through paplay so it is used"
                             if loud else "no loudness filter chain found (optional)"})

    # --- sox, for pitch and loudness (note 4) ------------------------------
    check("sox", shutil.which("sox"),
          "present" if shutil.which("sox") else "MISSING: piper has no pitch control, sox does it",
          "sudo apt install sox")

    # --- onnxruntime: reuse before downloading (note 3) --------------------
    ort = _onnxruntime_present()
    checks.append({"name": "onnxruntime", "ok": True,
                   "detail": f"already present ({ort['version']}), reuse it with --system-site-packages "
                             f"and save 55 MB" if ort["found"] else "not present; piper will bring its own"})

    # --- piper and its voice ----------------------------------------------
    piper_py = PIPER_DIR / "venv/bin/python"
    check("piper", piper_py.exists(),
          f"installed at {piper_py}" if piper_py.exists() else "MISSING",
          "fieldkit voice install --apply")
    model = PIPER_DIR / "voices" / f"{VOICE}.onnx"
    check("voice-model", model.exists(),
          f"{VOICE} present ({model.stat().st_size // 1048576} MB)" if model.exists()
          else f"MISSING: {VOICE}", "fieldkit voice install --apply")

    # --- speech-dispatcher: the layer that makes it system-wide ------------
    rc, mods = _run(["spd-say", "-O"])
    module_list = [m.strip() for m in mods.splitlines()[1:] if m.strip()] if rc == 0 else []
    check("speech-dispatcher", bool(module_list),
          f"modules: {', '.join(module_list)}" if module_list else "MISSING or no modules",
          "sudo apt install speech-dispatcher")
    wired = "piper-generic" in module_list
    check("piper-wired", wired,
          "piper is registered as a speech-dispatcher module" if wired
          else "piper is not wired in, so other programs will not use it",
          "fieldkit voice install --apply")

    # --- the grade: what will a person actually HEAR? (note 9) -------------
    engine = "piper" if wired else (module_list[0].split("-")[0] if module_list else None)
    grade, why = GRADE.get(engine, ("unknown", "could not tell which engine is configured"))
    checks.append({"name": "sound-quality", "ok": grade == "modern",
                   "detail": f"{engine or 'none'}: {grade}. {why}"})
    if grade != "modern":
        missing.append({"name": "sound-quality",
                        "fix": "fieldkit voice install --apply   (the voice will be metallic until then)"})

    ok = not missing
    return {"platform": "linux", "status": "ready" if ok else "needs-setup", "engine": engine,
            "grade": grade, "checks": checks, "missing": missing, "ok": ok}


def speak(text, platform=None):
    """Say one sentence through whatever is configured. -> 'spoken' | 'not-this-platform' | 'no-voice'."""
    if (platform or sys.platform) == "win32":
        return "not-this-platform"
    if not shutil.which("spd-say"):
        return "no-voice"
    rc, _ = _run(["spd-say", "--wait", text], timeout=120)
    return "spoken" if rc == 0 else "no-voice"


def lines(d):
    out = [f"voice: {d['status']}"]
    if d["status"] == "not-this-platform":
        return out + ["  " + d["why"]]
    for c in d["checks"]:
        out.append(f"  {'ok  ' if c['ok'] else 'MISS'} {c['name']:<18} {c['detail']}")
    if d["missing"]:
        out.append("")
        out.append(f"{len(d['missing'])} thing(s) to fix:")
        for m in d["missing"]:
            out.append(f"  {m['name']}: {m['fix']}")
        out.append("NEXT: fieldkit voice install --apply")
    else:
        out.append("NEXT: nothing. fieldkit voice say \"hello\" to hear it.")
    return out


def exit_code(d):
    return 0 if d.get("ok") else 3
