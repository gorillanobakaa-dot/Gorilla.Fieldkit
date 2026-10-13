"""A voice worth listening to, on Linux, set up the same way every time.

    fieldkit voice doctor            what this machine has, what it needs, and how it sounds
    fieldkit voice install [--apply] fetch and wire it up (the plan, without --apply)
    fieldkit voice say TEXT          speak one sentence through whatever is configured
    fieldkit voice clean [--apply]   remove voice models that are not the chosen one

Windows is deliberately untouched. It already sounds fine through System.Speech
and nothing here runs there: every command reports `not-this-platform` and
changes nothing. See WHY_LINUX_ONLY below.

# WHY THIS EXISTS

Every text-to-speech engine Debian packages predates deep learning: espeak-ng is
formant synthesis (1980s lineage), mbrola is diphone concatenation (1996),
festival and flite are older still. That is a deliberate distribution choice
rather than neglect, because the main users of Linux speech are screen-reader
users who listen at 300 to 450 words per minute and need speed and
predictability, which neural voices are worse at.

The consequence is that every Linux machine sounds like 1995 while the same
harness on Windows sounds fine, and nobody notices because both are "working".

This module fixes it at the speech-dispatcher layer rather than inside one tool,
so every program that speaks inherits it: the herald, the laptop alerts, desktop
notifications, Orca, and Claude Code's own speechSynthesis.
"""

# ============================================================================
# READ THIS BEFORE YOU CHANGE ANYTHING. IT COST A NIGHT TO LEARN.
# ============================================================================
#
# These are real mistakes made on 2026-10-13 while working this out by hand.
# They are written down so the next model does NOT repeat them. Every one of
# them looked reasonable at the time.
#
# 1. aplay BYPASSES PipeWire. It talks straight to ALSA hardware, so it skips
#    any loudness filter chain the owner has set up, and the voice comes out
#    noticeably quieter for no visible reason. USE paplay. This was a real bug
#    that survived an hour of "why is it so quiet".
#
# 2. DO NOT DOWNLOAD A VOICE TO AUDITION IT AND THEN DELETE IT. Voice models are
#    61 MB each. Auditioning seven of them, binning six, then re-downloading two
#    of the binned ones because the requirements changed, cost about 900 MB of
#    downloads to keep 61 MB. KEEP EVERYTHING UNTIL THE WORK IS FINISHED, then
#    run `fieldkit voice clean`. Cleaning early is how you download twice.
#
# 3. CHECK FOR AN EXISTING onnxruntime BEFORE INSTALLING ONE. It is 55 MB and is
#    often already present from some other tool. A venv built with
#    --system-site-packages reuses it: 59 MB instead of 197 MB, measured.
#
# 4. piper HAS NO PITCH CONTROL AND DOES NOT SPEAK SSML. Its only knobs are
#    length-scale, noise-scale, noise-w-scale and volume. Any instruction written
#    as SSML ("lower the pitch by 3 semitones", <prosody>) was written for Azure
#    or ElevenLabs and CANNOT be passed to piper. Pitch is a post-processing step
#    through sox.
#
# 5. DO NOT PITCH-SHIFT MORE THAN ABOUT TWO SEMITONES. Past that a female voice
#    stops sounding lower and starts sounding like a badly impersonated man.
#    Four semitones was tried and rejected by ear.
#
# 6. DO NOT TOUCH length-scale AT ALL, AND DO NOT PITCH-SHIFT. Superseded by
#    the finding in note 11, which is the important one in this file.
#
# 7. edge-tts IS NOT A SOLUTION. It is an unofficial client for an undocumented
#    Microsoft endpoint using a reverse-engineered token. It returned HTTP 403 on
#    every attempt on 2026-10-13, and it writes MP3 data into files named .wav,
#    which aplay then refuses. It also sends the text off the machine, which the
#    owner's rules forbid without justification.
#
# 8. NEVER pkill BY NAME. `pkill -f speech-dispatcher` killed the agent's own
#    shell, because the shell's command line contained the search pattern. Twice
#    in one session. Stop processes by PID, and only ones you started.
#
# 9. A CHECK THAT ONLY ASKS "DOES A PROGRAM EXIST" IS NOT ENOUGH. The previous
#    voice check returned ok:true for espeak and said nothing about quality,
#    which is precisely why this went unnoticed for months. doctor() below grades
#    the engine and says plainly when it is an old one.
#
# 11. EVERY ONE OF THOSE SETTINGS WAS TUNED ON SHORT PHRASES AND BROKE ON LONG
#    ONES. This is the correction that matters, made the same evening they were
#    chosen, as soon as anybody listened to a whole sentence.
#
#    The owner, hearing one for the first time: "after a few words the time
#    stretch, the rhythm, the cadence get out of wack and it does not really
#    sound human anymore". Then, on the candidates: "sounds like a broken indian
#    accent with weird halting spaces between... I like to move it move it".
#
#    TWO SEPARATE CAUSES, both in the post-processing:
#
#    (a) sox implements `pitch` as a TIME-STRETCH plus resample: a WSOLA-style
#        algorithm that chops audio into segments and splices them back
#        together. The splice artefacts accumulate across a long passage. The
#        stuttering is the splices. Widening the segment from 82 to 120 ms
#        helped a little and did not fix it.
#
#    (b) ANY pitch shift without formant correction moves the VOWEL FORMANTS,
#        and moving formants is precisely how an accent change is synthesised.
#        That is why it stopped sounding American. rubberband -F preserves
#        them and was audibly better than sox, and still not worth its cost.
#
#    Tested rather than argued: eleven candidates on one long sentence, then
#    Frost's "Stopping by Woods on a Snowy Evening" read in full by three of
#    them, then a stanza A/B'd three times. Also tried and rejected,
#    en_US-lessac-high, a 114 MB HIGH quality model: "all over the place way
#    too fast".
#
#    THE ANSWER WAS TO APPLY NOTHING. The model's own output at the model's own
#    defaults beat every processed version. Generalisation: tuning by ear on a
#    sample shorter than the real workload is not tuning, it is guessing, and
#    each layer added hid the damage of the last.
#
# 12. AVX2 IS NOT REQUIRED. It is absent on the owner's 2012 i7-3632QM and piper
#    runs at 0.45x realtime regardless, because onnxruntime picks its instruction
#    path at runtime. Do not refuse to try on an old CPU; measure instead.
# ============================================================================

# THE SETTLED SETUP. Chosen by ear on 2026-10-13 after comparing espeak-ng,
# mbrola, four British and four American neural voices. Do not change these
# without the owner listening; they are preferences, not defaults.
VOICE = "en_US-amy-medium"       # American female
SAMPLE_RATE = "22050"            # from the model's own .onnx.json

# NO INFERENCE OVERRIDES. The model's .onnx.json carries its own defaults
# (noise_scale 0.667, length_scale 1.0, noise_w 0.8) and they are better than
# the ones chosen here by ear on short phrases. See note 11. Passing nothing is
# deliberate: an empty string here is the setting, not a missing value.
PIPER_ARGS = ""

# NO PITCH SHIFT AND NO COMPANDER. Only a level normalise, which cannot smear
# anything because it applies a single constant gain. See note 11 for the
# measurements and the listening test that removed the rest.
SOX_CHAIN = "norm -1"

WHY_LINUX_ONLY = ("Windows already sounds fine through System.Speech, and the owner's instruction on "
                  "2026-10-13 was to leave it exactly as it is. Nothing in this module runs there.")

from .doctor import doctor, lines, exit_code, speak          # noqa: E402
from .install import plan, apply, clean                      # noqa: E402

__all__ = ["doctor", "lines", "exit_code", "speak", "plan", "apply", "clean",
           "VOICE", "PIPER_ARGS", "SOX_CHAIN", "WHY_LINUX_ONLY"]
