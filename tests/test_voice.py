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


# Pitch and pace have limits found by ear. Past them the voice stops being the
# voice (notes 5 and 6).
def test_pitch_and_pace_stay_inside_what_was_proven():
    assert abs(int(voice.PITCH_CENTS)) <= 200, \
        "beyond about two semitones a female voice sounds like a badly impersonated man"
    assert float(voice.LENGTH_SCALE) >= 0.80, \
        "below 0.80 neural prosody smears and it sounds rushed, like espeak"


# Cleaning early is how a 61 MB voice costs a gigabyte: the models get binned
# while the owner is still choosing, then fetched again (note 2).
def test_clean_refuses_while_the_voice_is_not_working(monkeypatch):
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
