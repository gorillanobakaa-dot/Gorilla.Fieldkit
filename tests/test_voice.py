"""The voice tool: platform routing, the plan, the grading, and the clean order.

Nothing here plays audio or downloads anything. The checks that matter are the
ones that encode what went wrong when this was done by hand on 2026-10-13: see
the numbered notes in fieldkit/voice/__init__.py.
"""
import pytest

from fieldkit import voice
from fieldkit.voice import install as vinstall
# NOTE: `from fieldkit.voice import doctor` gives the re-exported FUNCTION, not the
# module, because __init__.py rebinds the name. Import the symbol directly.
from fieldkit.voice.doctor import GRADE


# Windows must be untouched. The owner's instruction on 2026-10-13 was explicit:
# it already works there, leave it alone. Every entry point must refuse.
@pytest.mark.parametrize("fn", ["doctor", "plan", "apply", "clean", "speak"])
def test_windows_is_never_touched(fn):
    f = getattr(voice, fn, None) or getattr(vinstall, fn)
    out = f("hello", platform="win32") if fn == "speak" else f(platform="win32")
    if fn == "speak":
        assert out == "not-this-platform"
    else:
        assert out["status"] == "not-this-platform"
        assert out["why"]
        assert not out.get("done") and not out.get("steps") and not out.get("remove")


# An engine that merely EXISTS is not good enough. The old check returned ok for
# espeak and said nothing about quality, which is why Linux sounded like 1995
# here for months while every check passed (note 9).
def test_an_old_engine_is_graded_not_merely_found():
    assert GRADE["espeak-ng"][0] == "robotic"
    assert GRADE["piper"][0] == "modern"
    for engine, (grade, _why) in GRADE.items():
        assert grade in ("robotic", "dated", "modern"), engine
        if engine != "piper":
            assert grade != "modern", f"{engine} must not be graded modern"


# The wrapper must use paplay. aplay bypasses PipeWire and silently skips the
# owner's loudness filter chain, which reads as a volume problem and is not one.
# This was a real bug that survived an hour of looking (note 1).
def test_the_wrapper_plays_through_pipewire_not_alsa():
    # Comments mention aplay deliberately, to explain the trap. Check the lines
    # that actually RUN, not the prose about them.
    code = "\n".join(l for l in vinstall.WRAPPER_BODY.splitlines() if not l.lstrip().startswith("#"))
    assert "paplay" in code, "the wrapper must play through PipeWire"
    assert "aplay" not in code.replace("paplay", ""), \
        "aplay bypasses PipeWire and skips the loudness filter chain"


# 2026-10-13: this test used to police the LIMITS of the pitch shift and the
# pace override. Both were removed once anybody listened to a long sentence, so
# it now guards the opposite invariant: that nothing is applied at all.
#
# The owner, hearing a full sentence for the first time: "after a few words the
# time stretch, the rhythm, the cadence get out of wack", and on the candidates
# "sounds like a broken indian accent with weird halting spaces between".
#
# sox implements `pitch` as a time-stretch plus resample, so its splice
# artefacts accumulate over a passage; and any pitch shift without formant
# correction moves the vowel formants, which is how an accent change is made.
def test_sox_does_nothing_but_set_the_level():
    assert voice.SOX_CHAIN.strip() == "norm -1", (
        "only a level normalise may live in sox: a single constant gain cannot "
        "smear anything. A sox pitch shift or compander here broke long sentences.")
    for banned in ("pitch", "compand", "tempo", "speed", "stretch"):
        assert banned not in voice.SOX_CHAIN, f"{banned} is a time-domain effect"


def test_only_the_two_overrides_that_survived_a_listening_test_are_set():
    """noise_scale and noise_w stay at the model's defaults: overriding those is
    what smeared long sentences and was reverted. length_scale and
    sentence_silence are set, both SLOWER than default, which is the opposite
    direction from the change that broke it."""
    assert "--length-scale" in voice.PIPER_ARGS
    assert "--sentence-silence" in voice.PIPER_ARGS
    assert "noise" not in voice.PIPER_ARGS, (
        "noise-scale and noise-w-scale flatten the voice and its cadence; "
        "overriding them was the cause of the smearing and must not return")
    assert float(voice.LENGTH_SCALE) > 1.0, (
        "slower than default is the point: a quick delivery reads as a child")


def test_the_pitch_shift_is_rubberband_and_never_sox():
    """sox pitch is a time-stretch that splices segments AND moves formants,
    which is how an accent change is synthesised. rubberband -F preserves them."""
    assert float(voice.PITCH_SEMITONES) < 0
    assert "rubberband" in voice.NEEDS
    assert "pitch" not in voice.SOX_CHAIN, "sox must never do the pitch shift again"


def test_the_pitch_shift_stays_inside_what_was_proven():
    """Past about four semitones a female voice stops sounding lower and starts
    sounding like a badly impersonated man. -2.5 was chosen by ear."""
    assert abs(float(voice.PITCH_SEMITONES)) <= 4


# Cleaning early is how a 61 MB voice costs a gigabyte: the models get binned
# while the owner is still choosing, then fetched again (note 2).
def test_clean_refuses_while_the_voice_is_not_working(monkeypatch, tmp_path):
    """The refusal is only reachable when there is something to clean.

    Without a voices folder clean() answers "nothing-to-clean" first and the
    refusal is never tested. That is the state of any machine that has not
    installed the voice, which is every CI runner (2026-10-13).
    """
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "en_GB-someone-else.onnx").write_bytes(b"x" * 16)
    monkeypatch.setattr(vinstall, "PIPER_DIR", tmp_path)
    monkeypatch.setattr(vinstall, "doctor", lambda *a, **k: {"ok": False})
    out = vinstall.clean(platform="linux", apply_it=True)
    assert out["status"] == "refused"
    assert out["remove"] == []
    assert "finished" in out["why"]


# The plan must change nothing and must say what it will cost to download.
def test_the_plan_changes_nothing_and_states_the_download():
    out = vinstall.plan(platform="linux")
    assert out["status"] == "plan"
    assert out["next"].endswith("--apply")
    assert "total_download_mb" in out
    assert any("prove it" == s["step"] for s in out["steps"]), \
        "the last step must be proving a person can hear it, not just that files exist"


# The lessons are the most valuable thing in the module and must not be dropped
# in a tidy-up: they are what stops the next model repeating a night of this.
def test_the_hard_won_notes_survive():
    src = (voice.__doc__ or "") + open(voice.__file__, encoding="utf-8").read()
    for must in ("aplay", "onnxruntime", "SSML", "pkill", "audition", "AVX2"):
        assert must.lower() in src.lower(), f"the note about {must} has been lost"


def test_a_fresh_install_cannot_bring_the_old_chain_back():
    """The wrapper is generated from these constants, so `fieldkit voice install`
    would happily overwrite a hand-fixed script. Check the EXECUTABLE lines: the
    comments mention pitch and compand deliberately, to explain why they are gone."""
    run = "\n".join(l for l in vinstall.WRAPPER_BODY.splitlines()
                    if not l.lstrip().startswith("#"))
    for banned in ("compand", "tempo", "speed", "noise-scale", "noise-w-scale"):
        assert banned not in run, (
            f"{banned!r} is back in the generated wrapper; it was removed on 2026-10-13 "
            f"after a listening test and must not return without another one")
    assert "sox" in run and "pitch" not in run.split("rubberband")[0], \
        "sox must not do the pitch shift; rubberband does, because it keeps formants"
    assert "norm -1" in run, "the level normalise is the one effect that stays"
    assert "paplay" in run


def test_a_two_pass_tool_is_never_put_in_a_pipe():
    """2026-10-13: `rubberband - -` was tested with `< file`, which gives
    SEEKABLE stdin, and declared pipe-safe. Through a real pipe it silently
    truncated 70700 bytes to 4184: no error, no non-zero exit, just a short
    file. It does a study pass and seeks back; nothing rewinds a pipe.

    So any audio stage needing more than one pass must be given a real file.
    This test guards the wrapper against someone 'tidying' it back into a
    single elegant pipeline.
    """
    run = "\n".join(l for l in vinstall.WRAPPER_BODY.splitlines()
                    if not l.lstrip().startswith("#"))
    if "rubberband" in run:
        assert "- -" not in run, (
            "rubberband makes two passes over its input and cannot read a pipe. "
            "Give it a real file, or it truncates silently.")


def test_the_wrapper_gives_rubberband_a_real_file_not_a_pipe():
    """rubberband makes two passes and seeks backwards. Through a real pipe it
    silently truncated 70700 bytes to 4184: no error, no non-zero exit, just a
    short file. See note 12."""
    run = "\n".join(l for l in vinstall.WRAPPER_BODY.splitlines()
                    if not l.lstrip().startswith("#"))
    assert "mktemp" in run, "it needs a real file"
    assert "rubberband -p" in run
    assert "rubberband -p \"$PITCH\" -F --fine - -" not in run, "that silently truncates"


def test_an_alert_is_never_silenced_by_a_missing_pitch_shifter():
    """The watchdog's whole purpose is to speak when the laptop is offline. A
    slightly higher voice beats no voice."""
    run = "\n".join(l for l in vinstall.WRAPPER_BODY.splitlines()
                    if not l.lstrip().startswith("#"))
    assert "command -v rubberband" in run, "it must check before relying on it"
    assert run.count("play_raw") >= 3, "there must be a fallback path that still plays"


def test_rubberband_is_in_the_install_list():
    import shutil as _sh
    real = _sh.which
    try:
        _sh.which = lambda n: None
        assert "rubberband-cli" in vinstall._apt_missing()
    finally:
        _sh.which = real
