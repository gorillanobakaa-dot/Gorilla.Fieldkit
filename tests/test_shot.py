"""Reading numbers off a screenshot, and refusing to invent the ones that are not there."""
import pytest

from fieldkit.shot import read as R


OOKLA_DONE = """stare @ OOO nesuttiD:15768756856 © resurs i sermInes
@ DOWNLOAD Mbps) = =@ UPLOAD Mbps
44.31 21.89
Pingms @15 @O100 @90
31.94.7258
"""

OOKLA_RUNNING = """@ DOWNLOAD Mbps) = =@ UPLOAD Mbps.
Pingms ©19 © T0
403.65
Mbps
"""


def _patch(monkeypatch, text):
    monkeypatch.setattr(R, "read_text", lambda p, timeout=120: (text, None))


# OCR of a dark UI is noisy and that is fine. Find the LABELS, then the numbers
# near them, rather than trying to clean the whole page.
def test_it_reads_the_numbers_through_the_ocr_noise(monkeypatch):
    _patch(monkeypatch, OOKLA_DONE)
    r = R.speedtest("x.png")
    assert r["download"] == 44.31
    assert r["upload"] == 21.89
    assert r["ping"] == 15, "the FIRST number on the ping line is the idle ping"
    assert r["complete"] is True


# THE important one. A screenshot of a test in progress has no result: the
# download sits on a dial and the upload is a dash. Returning a number for it is
# inventing data, which is the same class of error as a check that reports ok
# because it found a program.
def test_a_test_still_running_is_reported_incomplete_not_guessed(monkeypatch):
    _patch(monkeypatch, OOKLA_RUNNING)
    r = R.speedtest("x.png")
    assert r["complete"] is False
    assert r["download"] is None and r["upload"] is None, \
        "403.65 is on a dial mid-test; reporting it as a result is invention"
    assert "still running" in r["error"]


def test_a_missing_file_is_an_error_not_a_zero():
    r = R.speedtest("/definitely/not/here.png")
    assert r["complete"] is False
    assert r["error"]


# The inference that actually cracked the real case: a bottleneck in the link
# throttles BOTH directions. If download swings and upload does not, the
# download met a ceiling the upload never reached.
#
# UPDATED 2026-10-13: verdict() now REQUIRES wan ("fixed" or "cellular"),
# because that rule is only valid on a fixed line. On cellular a 10x downlink
# swing with a flat upload is the NORMAL shape of mobile broadband, not a fault,
# and the rule was measured giving a confident wrong answer on an EE 5G hub.
# These tests therefore state which kind of line they describe. The refusal when
# wan is omitted is itself tested below.
def test_download_swinging_while_upload_holds_blames_the_link_ON_A_FIXED_LINE():
    results = [
        {"file": "a", "complete": True, "download": 44.31, "upload": 21.89},
        {"file": "b", "complete": True, "download": 44.25, "upload": 16.07},
        {"file": "c", "complete": True, "download": 417.32, "upload": 18.81},
    ]
    v = R.verdict(results, wan="fixed")
    assert "LINK IS THE LIMIT" in v["verdict"]
    assert v["download_spread"] > 9
    assert v["upload_spread"] < 1.5
    assert v["next"] == "fieldkit wifi doctor"


def test_both_steady_means_the_connection_is_simply_that_speed():
    results = [
        {"file": "a", "complete": True, "download": 44.0, "upload": 20.0},
        {"file": "b", "complete": True, "download": 45.0, "upload": 21.0},
    ]
    v = R.verdict(results, wan="fixed")
    assert "CONSISTENT" in v["verdict"]
    assert "provider" in v["next"] or "paying" in v["next"]


def test_both_varying_points_at_the_line_not_the_hardware():
    results = [
        {"file": "a", "complete": True, "download": 40.0, "upload": 5.0},
        {"file": "b", "complete": True, "download": 400.0, "upload": 50.0},
    ]
    v = R.verdict(results, wan="fixed")
    assert "BOTH DIRECTIONS VARY" in v["verdict"]


# Incomplete shots must be excluded from the comparison AND named, so nobody
# wonders why four pictures produced three results.
def test_incomplete_shots_are_excluded_and_listed():
    results = [
        {"file": "a", "complete": True, "download": 44.0, "upload": 20.0},
        {"file": "mid.png", "complete": False, "error": "still running"},
        {"file": "c", "complete": True, "download": 417.0, "upload": 19.0},
    ]
    v = R.verdict(results)
    assert v["usable"] == 2
    assert v["ignored"] == ["mid.png"]


def test_one_usable_result_refuses_to_conclude():
    v = R.verdict([{"file": "a", "complete": True, "download": 44.0, "upload": 20.0}])
    assert "at least two" in v["verdict"], "a single run cannot identify a layer"


# The correction itself, kept as tests so it cannot regress. These are the
# cases that made verdict() demand `wan` in the first place.
def test_verdict_refuses_to_conclude_without_knowing_the_line_kind():
    """A confident wrong answer sends somebody to change router settings for an
    evening. Refusing costs one command: fieldkit net kind."""
    results = [
        {"file": "a", "complete": True, "download": 44.31, "upload": 21.89},
        {"file": "c", "complete": True, "download": 417.32, "upload": 18.81},
    ]
    v = R.verdict(results)
    assert "CANNOT SAY YET" in v["verdict"]
    assert "cellular" in v["verdict"]
    assert "LINK IS THE LIMIT" not in v["verdict"]


def test_the_same_numbers_are_normal_on_cellular():
    """The measured evening: 25.7 to 417 Mbps on one unchanged cable, no fault.
    The downlink is shared and rescheduled; the uplink is a separate, smaller
    allocation that stays flat anyway. So the shape proves nothing."""
    results = [
        {"file": "a", "complete": True, "download": 25.7, "upload": 21.89},
        {"file": "b", "complete": True, "download": 417.32, "upload": 18.81},
    ]
    fixed = R.verdict(results, wan="fixed")
    cell = R.verdict(results, wan="cellular")
    assert fixed["verdict"] != cell["verdict"], \
        "the same numbers must NOT produce the same verdict on both line kinds"
    assert "LINK IS THE LIMIT" not in cell["verdict"]
