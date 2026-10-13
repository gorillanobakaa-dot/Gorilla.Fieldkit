# fieldkit voice: developer track

## What it is

`fieldkit/voice/` sets up a neural speech engine on a Debian-family machine and
wires it into speech-dispatcher, so every program that speaks inherits it.
Windows is out of scope by instruction and every entry point returns
`not-this-platform` there.

```
fieldkit voice doctor            probe, and GRADE the engine
fieldkit voice install [--apply] fetch and wire (plan without --apply)
fieldkit voice say TEXT          speak through whatever is configured
fieldkit voice clean [--apply]   remove models that are not the chosen one
```

## The settled configuration

Chosen by ear on 2026-10-13 after comparing espeak-ng, mbrola, four British and
four American neural voices.

```
VOICE          en_US-amy-medium    American female
LENGTH_SCALE   0.90                pace; lower is faster
NOISE_SCALE    0.6                 variation in the voice
NOISE_W_SCALE  0.7                 variation in cadence; lower is steadier
PITCH_CENTS    -100                one semitone, applied by sox AFTER piper
SAMPLE_RATE    22050               from the model's own .onnx.json
SOX_CHAIN      norm -1        (nothing else: see 'What was removed and why')
```

These are preferences, not defaults. Do not change them without the owner
listening.

## Architecture, and why it is at this layer

```
any program
    |
    v
speech-dispatcher          <- the fix goes HERE
    |
    +-- piper-generic (sd_generic) -> piper-speak.sh -> piper | sox norm | paplay
    |
    +-- espeak-ng (fallback, still registered)
```

Wiring at speech-dispatcher rather than inside one tool means the herald, the
laptop alerts, desktop notifications, Orca and Claude Code's own
`speechSynthesis` all inherit it without knowing piper exists. Teaching each
tool to find a good voice is the same work repeated once per tool.

Everything is user-level: `~/.local/share/piper` and
`~/.config/speech-dispatcher`. No root. Removing the latter reverts the machine.

## Measurements

| What | Value | How |
|---|---|---|
| synthesis speed, i7-3632QM (2012, no AVX2) | **0.45x realtime** | 3.12 s of work for 6.97 s of audio |
| `en_GB-cori-high` on the same chip | **1.12x realtime** | slower than realtime; rejected as a default |
| model size, medium | 61 MB | `low` variants are also 61 MB; no saving |
| model size, high | 109 MB | |
| venv with `--system-site-packages` | **59 MB** | reuses an existing onnxruntime |
| venv without | 197 MB | 138 MB wasted |
| total kept | **115 MB** | venv + one model + 18 KB of config |
| total downloaded getting there | **~1.06 GB** | 902 MB models, 156 MB wheels |

Claim sourcing: every figure above was measured on the owner's machine on
2026-10-13. None is quoted from documentation.

## The ten mistakes, encoded

The module docstring carries ten numbered notes, each a real mistake made that
night. Two tests exist specifically to stop them being tidied away:
`test_the_wrapper_plays_through_pipewire_not_alsa` and
`test_the_hard_won_notes_survive`. The ones most likely to be repeated:

**`aplay` bypasses PipeWire.** It talks straight to ALSA, so it skips any
loudness filter chain the user has configured. It presents as a volume problem.
This cost an hour. `paplay` is mandatory and is asserted by a test.

**Do not audition by downloading and deleting.** 900 MB spent to keep 61 MB.
`voice clean` refuses to run while the chosen voice is not working, so the
ordering is enforced in code rather than left to judgement.

**Check for an existing `onnxruntime` before installing one.** 55 MB, often
already present.

**piper has no pitch control and does not speak SSML.** Its parameters are
`length-scale`, `noise-scale`, `noise-w-scale`, `volume`. Any instruction
written as `<prosody>` or "lower by N semitones" was written for Azure or
ElevenLabs. Pitch is a `sox` stage.

**Limits found by ear:** pitch beyond about 2 semitones turns a female voice
into a poorly impersonated man; `length-scale` below 0.80 smears the prosody.
Both are asserted by `test_pitch_and_pace_stay_inside_what_was_proven`.

**`edge-tts` is not viable.** Unofficial client for an undocumented Microsoft
endpoint with a reverse-engineered token. HTTP 403 on every attempt, and it
writes MP3 data into files named `.wav`.

**Never `pkill` by name.** `pkill -f speech-dispatcher` killed the agent's own
shell twice, because the shell's command line contained the pattern.

**AVX2 is not required.** onnxruntime selects its instruction path at runtime.

## The grading, which is the point

`doctor()` does not report `ok: true` because a speech program exists. It maps
the configured engine through a `GRADE` table and says plainly when it is an old
one:

```
espeak-ng   robotic   formant synthesis, 1980s lineage
mbrola      dated     diphone concatenation, 1996
piper       modern    neural
```

The previous check returned `ok: true` for espeak and said nothing about
quality. That is exactly why this went unnoticed for months while every check
passed. A check that confirms existence is not a check that confirms fitness.

## Related change outside this module

`fieldkit/buildh/herald.py` had its preference order reversed:

```python
# was
for exe in ("espeak-ng", "espeak", "spd-say"):
# now
for exe in ("spd-say", "espeak-ng", "espeak"):
```

`spd-say` means "use what this machine is configured to use". With espeak first,
Fieldkit silently overrode the user's system voice on every Linux machine. The
owner caught it by hearing two different voices in one session. No test would
have found it, because both voices are "working".

## What is not done

The Windows half is untouched by instruction. `voice_check()` in `herald.py`
still reports Windows voices without grading them, which is the same gap this
module closes on Linux; it is left alone deliberately.

## What was removed and why, 2026-10-13

The settings below were chosen by ear the same day, on phrases of a few words,
and every one of them was reverted that evening as soon as a long sentence was
played:

```
pitch -100                 a semitone down, through sox
compand 0.02,0.20 ...      peak squashing for weak laptop speakers
--length-scale 0.90        10% faster than the model intends
--noise-scale 0.6          the model ships 0.667
--noise-w-scale 0.7        the model ships 0.8; this one flattens cadence
```

The maintainer, hearing a full sentence for the first time: *"after a few words
the time stretch, the rhythm, the cadence get out of wack and it does not really
sound human anymore"*, and on the candidates, *"sounds like a broken indian
accent with weird halting spaces between"*.

**Two separate causes, both in post-processing.**

`sox pitch` is implemented as a time-stretch plus resample: a WSOLA-style
algorithm that chops audio into segments and splices them back together. Those
splice artefacts accumulate across a passage, which is the stuttering. Widening
the segment from the default 82 ms to 120 ms helped slightly and did not fix it.

Separately, **any pitch shift without formant correction moves the vowel
formants**, and moving formants is precisely how an accent change is
synthesised. That is why it stopped sounding American. `rubberband -p -1 -F`
preserves them and was audibly better than sox, and still not worth its cost.

**Tested, not argued.** Eleven candidates on one long sentence; then Frost's
*Stopping by Woods on a Snowy Evening* read in full by three of them; then one
stanza A/B'd three times. `en_US-lessac-high`, a 114 MB **high** quality model,
was downloaded and rejected: *"all over the place way too fast"*.

**The answer was to apply nothing.** The model's own output at the model's own
defaults beat every processed version.

Generalisation worth carrying: **tuning by ear on a sample shorter than the real
workload is not tuning, it is guessing**, and each layer added hid the damage of
the one before it. The old chain is quarantined with its full reasoning in
`Agents.Work.Trash/piper-speak-sox-chain-26-10-13/`.
