---
name: fieldkit-voice
description: Set up or repair the spoken voice on a Linux machine so alerts are worth listening to. Use when a Linux machine sounds robotic or metallic, when speech is too quiet, when a tool speaks in a different voice from the rest of the desktop, or when setting up a new Debian-family machine. Windows is deliberately out of scope.
---

# A voice worth listening to, on Linux

## Do this, in this order

```
fieldkit voice doctor            what is here, what is missing, and HOW IT SOUNDS
fieldkit voice install           the plan (changes nothing)
fieldkit voice install --apply   do it
fieldkit voice say "hello"       prove a person can hear it
fieldkit voice clean --apply     ONLY at the very end
```

If `doctor` says `ready`, there is nothing to do. Stop.

## Windows: do not touch it

It already sounds correct through System.Speech. Every command here reports
`not-this-platform` and changes nothing. **Do not "improve" it.** That is a
standing instruction from the owner, 2026-10-13.

---

# THE TEN MISTAKES. DO NOT REPEAT THEM.

These were all made, for real, in one night of working this out by hand. Each
one looked reasonable at the time. You are being told so you do not spend that
night again.

**1. `aplay` bypasses PipeWire.** It talks straight to ALSA hardware, so it
skips the user's loudness filter chain and comes out noticeably quieter. It
reads like a volume problem and is not one. **Always `paplay`.**

**2. Do not download a voice to audition it and then delete it.** Models are
61 MB each. Auditioning seven, binning six, then re-fetching two of the binned
ones when the requirements changed cost about **900 MB of downloads to keep
61 MB**. **Keep everything until the work is finished, then clean.** This is the
single most expensive mistake on the list.

**3. Check for an existing `onnxruntime` first.** It is 55 MB and is often
already installed by something else. Build the venv with
`--system-site-packages` and reuse it: **59 MB instead of 197 MB**, measured.

**4. piper has no pitch control and does not speak SSML.** Its only knobs are
`length-scale`, `noise-scale`, `noise-w-scale` and `volume`. Any instruction
written as SSML, `<prosody>`, or "lower the pitch by N semitones", was written
for Azure or ElevenLabs. **It cannot be passed to piper.** Pitch is a `sox`
stage afterwards.

**5. Do not pitch-shift more than about two semitones.** Past that a female
voice stops sounding lowered and starts sounding like a badly impersonated man.
Four semitones was tried and rejected by ear.

**6. Do not go below `length-scale` 0.80.** Neural prosody smears and it gains
the rushed clipped quality that makes espeak tiring.

**7. `edge-tts` is not a solution.** Unofficial client, undocumented Microsoft
endpoint, reverse-engineered token. Returned HTTP 403 on every attempt on
2026-10-13. It also writes **MP3 data into files named `.wav`**, which `aplay`
then refuses. And it sends the text off the machine.

**8. Never `pkill` by name.** `pkill -f speech-dispatcher` killed the agent's
own shell, twice in one session, because the shell's command line contained the
pattern. **Stop processes by PID, and only ones you started.**

**9. "A speech program exists" is not a check.** The previous check returned
`ok: true` for espeak and said nothing about quality. That is exactly why Linux
sounded like 1995 for months while every check passed. **Grade the engine. Ask
what a person will hear.**

**10. AVX2 is not required.** It is absent on a 2012 i7-3632QM and piper still
runs at 0.45x realtime, because onnxruntime picks its instruction path at
runtime. **Do not refuse to try on an old machine. Measure.**

---

# Clean up after yourself, but only at the end

The harness has had no general habit of this, and it shows: a night's work left
about 1 GB downloaded to keep 115 MB.

**The rule, in order:**

1. **While working:** keep every download. Models, wheels, test files, all of it.
   You do not yet know which you will need, and re-fetching is the expensive
   mistake, not keeping.
2. **When the owner has decided and the thing works:** clean.
3. **`fieldkit voice clean` refuses to run while the chosen voice is not
   working**, precisely so step 2 cannot happen before step 1 is done.
4. **Say what you removed and what it freed.** A silent cleanup is
   indistinguishable from a bug.

Anything you downloaded into a scratch directory is yours to remove. Anything in
the owner's own folders is not: report it and leave it.

---

# What "ready" means here

```
voice     en_US-amy-medium     American female, chosen by ear
pace      0.90                 length-scale
pitch     -1 semitone          sox, after piper
loudness  compand + norm       lifts quiet syllables for laptop speakers
player    paplay               THROUGH the loudness filter chain
route     speech-dispatcher    so every program inherits it
fallback  espeak-ng            still registered, so the machine is never mute
cost      115 MB on disk       one download, nothing recurring
```

Wiring it at the **speech-dispatcher** layer is the whole point. Every program
that speaks inherits it: the herald, the laptop alerts, desktop notifications,
Orca, and Claude Code's own `speechSynthesis`. Teaching one tool to find a good
voice is the same work repeated once per tool.

**A tool must not override the user's system voice.** If someone has configured
espeak deliberately, they get espeak. `herald.voice()` tries `spd-say` first for
this reason: it means "use what this machine is set to use".
