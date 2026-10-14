"""Set the voice up, in the order that works, without the trial and error.

Everything here was learned by doing it wrong first. The ordering matters:
reuse onnxruntime BEFORE building the venv, fetch exactly ONE voice, and clean
only at the end. See the numbered notes in __init__.py.

plan() changes nothing and is what runs without --apply.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import PIPER_ARGS, PITCH_SEMITONES, SAMPLE_RATE, SOX_CHAIN, VOICE
from .doctor import PIPER_DIR, SD_USER_DIR, _onnxruntime_present, _run, doctor

WRAPPER = PIPER_DIR / "piper-speak.sh"
SD_MODULE = SD_USER_DIR / "modules/piper-generic.conf"
SD_CONF = SD_USER_DIR / "speechd.conf"

WRAPPER_BODY = f"""#!/bin/sh
# Written by `fieldkit voice install`. Speak text given on stdin.
#
# Wired into speech-dispatcher as a generic module, so EVERY program on this
# machine that speaks uses it: the herald, the laptop alerts, desktop
# notifications, Orca and Claude Code's own speechSynthesis.
#
# Why these numbers and not the model's defaults: see notes 11 and 14 in
# fieldkit/voice/__init__.py. Short version: slightly slower and a little lower
# because the voice otherwise reads as a child, which is a product problem;
# and the pitch shift is RUBBERBAND with -F, which preserves formants. The sox
# one did not, and moving formants is how an accent change is synthesised.
#
# RUBBERBAND CANNOT READ A PIPE. It makes two passes and seeks back to the
# start; through a real pipe it silently truncated 70700 bytes to 4184. Tested
# as `rubberband - - < in.wav` it looks fine, because a shell redirect gives
# SEEKABLE stdin and a pipe does not. Hence the temporary file. Do not tidy
# this back into one elegant pipeline.
#
# AN ALERT MUST NEVER BE SILENCED BY THE AUDIO CHAIN. With no rubberband, or
# no writable temporary file, the plain voice plays instead. The watchdog
# exists to speak when the laptop is offline; a higher voice beats no voice.
#
# paplay, NOT aplay: aplay bypasses PipeWire and skips the loudness filters.
P="$HOME/.local/share/piper"
VOICE="${{PIPER_VOICE:-{VOICE}}}"
RATE={SAMPLE_RATE}
PITCH="${{PIPER_PITCH:-{PITCH_SEMITONES}}}"

play_raw() {{ paplay --raw --rate=$RATE --format=s16le --channels=1; }}
synth() {{ "$P/venv/bin/python" -m piper -m "$P/voices/$VOICE.onnx" {PIPER_ARGS} --output-raw 2>/dev/null; }}

TMP="$(mktemp "${{TMPDIR:-/tmp}}/piper-XXXXXX" 2>/dev/null)"
if [ -z "$TMP" ] || ! command -v rubberband >/dev/null 2>&1; then
    synth | sox -t raw -r $RATE -e signed -b 16 -c 1 - \\
                -t raw -r $RATE -e signed -b 16 -c 1 - {SOX_CHAIN} 2>/dev/null | play_raw
    [ -n "$TMP" ] && rm -f "$TMP"
    exit 0
fi

synth | sox -t raw -r $RATE -e signed -b 16 -c 1 - "$TMP.wav" 2>/dev/null
if rubberband -p "$PITCH" -F --fine "$TMP.wav" "$TMP.rb.wav" 2>/dev/null && [ -s "$TMP.rb.wav" ]; then
    sox "$TMP.rb.wav" -t raw -r $RATE -e signed -b 16 -c 1 - {SOX_CHAIN} 2>/dev/null | play_raw
else
    sox "$TMP.wav" -t raw -r $RATE -e signed -b 16 -c 1 - {SOX_CHAIN} 2>/dev/null | play_raw
fi
rm -f "$TMP" "$TMP.wav" "$TMP.rb.wav"
"""

SD_MODULE_BODY = """# Written by `fieldkit voice install`. piper as a speech-dispatcher module.
#
# Wiring it in HERE rather than inside one tool is the whole point: every
# program that speaks inherits it, including the herald, the laptop alerts,
# desktop notifications, Orca and Claude Code's own speechSynthesis.
Debug 0

GenericExecuteSynth \\
"printf %s \\'$DATA\\' | $HOME/.local/share/piper/piper-speak.sh"

GenericCmdDependency "paplay"
GenericCmdDependency "sox"

GenericPunctNone ""
GenericPunctSome ""
GenericPunctMost ""
GenericPunctAll ""

GenericLanguage "en" "en" "utf-8"
GenericLanguage "en-gb" "en-gb" "utf-8"
GenericLanguage "en-us" "en-us" "utf-8"

AddVoice "en" "FEMALE1" "__VOICE__"
AddVoice "en-us" "FEMALE1" "__VOICE__"
""".replace("__VOICE__", VOICE)

SD_CONF_TAIL = """
# Written by `fieldkit voice install`.
# espeak-ng stays registered as a fallback: if piper or its model ever goes
# missing, `spd-say -o espeak-ng` still works and the machine is not mute.
AddModule "piper-generic" "sd_generic"   "piper-generic.conf"
AddModule "espeak-ng"     "sd_espeak-ng" "espeak-ng.conf"
DefaultModule piper-generic
"""


def _apt_missing():
    need = []
    # rubberband is the pitch shifter. The wrapper degrades to the plain voice
    # without it, so it is not fatal, but a fresh install should get it: the
    # voice reads as a child at its natural pitch, which is a product problem.
    for exe, pkg in (("sox", "sox"), ("paplay", "pulseaudio-utils"),
                     ("spd-say", "speech-dispatcher"), ("rubberband", "rubberband-cli")):
        if not shutil.which(exe):
            need.append(pkg)
    return need


def plan(platform=None):
    """What install would do. Changes NOTHING."""
    if (platform or sys.platform) == "win32":
        from . import WHY_LINUX_ONLY
        return {"platform": "windows", "status": "not-this-platform", "why": WHY_LINUX_ONLY, "steps": []}

    steps = []
    apt = _apt_missing()
    if apt:
        steps.append({"step": "system packages", "detail": f"sudo apt install -y {' '.join(apt)}",
                      "needs_root": True})
    ort = _onnxruntime_present()
    steps.append({"step": "python environment",
                  "detail": f"venv at {PIPER_DIR/'venv'} with --system-site-packages, reusing the existing "
                            f"onnxruntime {ort['version']} (saves about 55 MB)" if ort["found"]
                            else f"venv at {PIPER_DIR/'venv'}, piper will bring its own onnxruntime",
                  "needs_root": False, "done": (PIPER_DIR / "venv/bin/python").exists()})
    model = PIPER_DIR / "voices" / f"{VOICE}.onnx"
    steps.append({"step": "voice model", "detail": f"{VOICE}, about 61 MB, fetched ONCE. Auditioning voices "
                                                   f"by downloading and binning them is how a gigabyte gets "
                                                   f"spent to keep 61 MB (note 2).",
                  "needs_root": False, "done": model.exists()})
    steps.append({"step": "wrapper", "detail": str(WRAPPER), "needs_root": False, "done": WRAPPER.exists()})
    steps.append({"step": "speech-dispatcher", "detail": f"{SD_MODULE} and {SD_CONF}, user-level, no root. "
                                                         f"Removing ~/.config/speech-dispatcher reverts it.",
                  "needs_root": False, "done": SD_MODULE.exists()})
    steps.append({"step": "prove it", "detail": "speak a sentence and confirm the engine is graded modern. "
                                                "A check that only finds a program is not enough (note 9).",
                  "needs_root": False, "done": False})
    return {"platform": "linux", "status": "plan", "steps": steps,
            "total_download_mb": 61 if not model.exists() else 0,
            "next": "fieldkit voice install --apply"}


def apply(platform=None, run=None):
    """Do it. Returns what was done; raises nothing on a step that was already done."""
    if (platform or sys.platform) == "win32":
        from . import WHY_LINUX_ONLY
        return {"platform": "windows", "status": "not-this-platform", "why": WHY_LINUX_ONLY, "done": []}

    run = run or _run
    done, failed = [], []

    apt = _apt_missing()
    if apt:
        failed.append({"step": "system packages",
                       "why": f"needs root. Run this yourself, then run install again:\n"
                              f"  sudo apt install -y {' '.join(apt)}"})
        return {"platform": "linux", "status": "blocked", "done": done, "failed": failed}

    (PIPER_DIR / "voices").mkdir(parents=True, exist_ok=True)

    venv_py = PIPER_DIR / "venv/bin/python"
    if not venv_py.exists():
        # --system-site-packages so an existing onnxruntime is reused (note 3).
        rc, out = run([sys.executable, "-m", "venv", "--system-site-packages", str(PIPER_DIR / "venv")], timeout=300)
        if rc != 0:
            failed.append({"step": "venv", "why": out[-400:]})
            return {"platform": "linux", "status": "failed", "done": done, "failed": failed}
        rc, out = run([str(venv_py), "-m", "pip", "install", "-q", "piper-tts"], timeout=1800)
        if rc != 0:
            failed.append({"step": "piper-tts", "why": out[-400:]})
            return {"platform": "linux", "status": "failed", "done": done, "failed": failed}
        done.append("installed piper-tts")

    model = PIPER_DIR / "voices" / f"{VOICE}.onnx"
    if not model.exists():
        rc, out = run([str(venv_py), "-m", "piper.download_voices",
                       "--download-dir", str(PIPER_DIR / "voices"), VOICE], timeout=1800)
        if rc != 0 or not model.exists():
            failed.append({"step": "voice model", "why": out[-400:]})
            return {"platform": "linux", "status": "failed", "done": done, "failed": failed}
        done.append(f"fetched {VOICE}")

    WRAPPER.write_text(WRAPPER_BODY)
    WRAPPER.chmod(0o755)
    done.append(f"wrote {WRAPPER}")

    (SD_USER_DIR / "modules").mkdir(parents=True, exist_ok=True)
    SD_MODULE.write_text(SD_MODULE_BODY)
    if not SD_CONF.exists():
        system = Path("/etc/speech-dispatcher/speechd.conf")
        SD_CONF.write_text(system.read_text() if system.exists() else "")
    if "piper-generic" not in SD_CONF.read_text():
        with SD_CONF.open("a") as fh:
            fh.write(SD_CONF_TAIL)
    done.append("wired piper into speech-dispatcher")

    # Verify the ARTEFACT, not the exit code: ask the doctor what a person hears.
    d = doctor()
    return {"platform": "linux", "status": "ready" if d["ok"] else "incomplete",
            "done": done, "failed": failed, "grade": d.get("grade"), "engine": d.get("engine")}


def clean(platform=None, apply_it=False):
    """Remove voice models that are not the chosen one.

    ONLY AT THE END. Cleaning while the owner is still choosing is how a voice
    gets downloaded twice (note 2). This refuses to run if the chosen voice is
    not working, because then the other models may still be needed.
    """
    if (platform or sys.platform) == "win32":
        from . import WHY_LINUX_ONLY
        return {"platform": "windows", "status": "not-this-platform", "why": WHY_LINUX_ONLY, "remove": []}

    voices = PIPER_DIR / "voices"
    if not voices.exists():
        return {"status": "nothing-to-clean", "remove": [], "freed_mb": 0}

    d = doctor()
    if not d["ok"]:
        return {"status": "refused", "remove": [], "freed_mb": 0,
                "why": "the chosen voice is not working yet, so the others may still be needed. "
                       "Clean only when the work is finished (note 2)."}

    extra = [p for p in sorted(voices.iterdir()) if not p.name.startswith(VOICE)]
    freed = sum(p.stat().st_size for p in extra) // 1048576
    if apply_it:
        for p in extra:
            p.unlink()
    return {"status": "cleaned" if apply_it else "plan", "remove": [p.name for p in extra],
            "freed_mb": freed, "kept": VOICE,
            "next": "" if apply_it else "fieldkit voice clean --apply"}
