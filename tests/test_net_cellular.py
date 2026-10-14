"""The cellular module: band maths, antenna fit, and what to actually buy.

Written after the module itself, which is the wrong order and is why
antenna_fit() once reported "covers every carrier in use" having parsed zero
carriers. Every function in here that can fail must be shown REFUSING, not just
succeeding.
"""
import pytest

from fieldkit.net import cellular


# --------------------------------------------------------- bands and grading
def test_every_band_entry_is_well_formed():
    for name, (lo, hi, desc) in cellular.BANDS.items():
        assert lo < hi, name
        assert desc, name
        assert 600 < lo < 4300 and 600 < hi < 4300, name


def test_grade_reports_not_reported_rather_than_inventing():
    assert cellular.grade(None, cellular.RSRP_SCALE) == "not reported"


def test_rsrp_grading_matches_the_measured_readings():
    # the two real readings taken on 2026-10-13, antennas on then off
    assert cellular.grade(-92, cellular.RSRP_SCALE) == "fair"
    assert cellular.grade(-87, cellular.RSRP_SCALE) == "good"


# -------------------------------------------------------------- antenna fit
def test_antenna_fit_refuses_when_nothing_was_parsed():
    """The vacuous pass this module actually shipped once: zero carriers read,
    and it reported covering every carrier in use."""
    d = cellular.antenna_fit([], 2400, 2500)
    assert d["total"] == 0
    assert "CANNOT SAY" in d["summary"]


def test_wifi_antenna_cannot_help_the_measured_carriers():
    """Proven by measurement: removing these gained 5 dB of RSRP."""
    d = cellular.antenna_fit(["B3", "B3", "B7", "n78", "n78"], 2400, 2500)
    assert d["usable"] == 0
    assert "NOT ONE" in d["summary"]


def test_matching_antenna_is_recognised():
    d = cellular.antenna_fit(["n78"], 3300, 3800)
    assert d["usable"] == 1




# ------------------------------------------------- what antenna to actually buy
def test_antenna_spec_derives_the_range_from_the_measured_bands():
    d = cellular.antenna_spec(["B3", "B3", "B7", "n78", "n78"])
    assert d["must_cover_mhz"] == "1805-3800"
    # B3+B3 is two carriers on ONE band and must not be double counted
    assert len(d["distinct_bands"]) == 3
    assert d["how_many"] == 2


def test_antenna_spec_always_says_buy_a_pair():
    """One antenna unfeeds a MIMO chain, which costs more than any gain adds."""
    d = cellular.antenna_spec(["B3"])
    assert d["how_many"] == 2
    assert "MIMO" in d["why_two"]
    assert "cross-polarised" in d["mounting"]


def test_antenna_spec_refuses_the_supplied_thin_cable_at_5m():
    """The number nobody checks: 10 dB of coax loss beats any affordable gain."""
    d = cellular.antenna_spec(["n78"], cable_m=5.0)
    assert d["cable"]["loss_db_at_3500"] == 10.0
    assert "REFUSE" in d["cable"]["verdict"]


def test_antenna_spec_accepts_a_short_low_loss_run():
    d = cellular.antenna_spec(["n78"], cable_m=2.0, cable="LMR400 / RG213 (thick)")
    assert d["cable"]["loss_db_at_3500"] < 1.0
    assert "acceptable" in d["cable"]["verdict"]


def test_antenna_spec_refuses_rather_than_guessing_with_no_bands():
    """A plausible shopping list derived from nothing is the expensive wrong."""
    d = cellular.antenna_spec(["ZZ99"])
    assert "error" in d and "CANNOT SPEC" in d["error"]
    assert "ZZ99" in d["unknown"]
    assert "buy_this" not in d


def test_wall_loss_rises_with_frequency():
    """The whole reason the fast band dies indoors first."""
    assert cellular.wall_loss(3500) > cellular.wall_loss(1800) > cellular.wall_loss(900)


def test_antenna_spec_says_position_beats_gain():
    d = cellular.antenna_spec(["B3", "n78"])
    notes = " ".join(d["notes"])
    assert "POSITION BEATS GAIN" in notes
    assert "bearing" in notes.lower()
    assert "RP-SMA" in notes


def test_antenna_spec_flags_unknown_carriers_rather_than_silently_dropping():
    d = cellular.antenna_spec(["B3", "nWHAT"])
    assert d["unknown_carriers"] == ["nWHAT"]
    assert any("nWHAT" in n for n in d["notes"])


def test_antenna_spec_marks_its_inferred_numbers_as_inferred():
    d = cellular.antenna_spec(["B3"])
    assert "model inference" in d["provenance"]


def test_spec_lines_renders_without_crashing():
    for carriers in (["B3"], ["B3", "B7", "n78"], ["B20", "n78"]):
        out = "\n".join(cellular.spec_lines(cellular.antenna_spec(carriers)))
        assert "MUST COVER" in out and "BUY:" in out
    assert "CANNOT SPEC" in "\n".join(cellular.spec_lines(cellular.antenna_spec([])))
