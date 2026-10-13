"""The wifi doctor: band honesty, stream counting, and the traps that bit for real."""
import pytest

# NOTE: `from fieldkit.wifi import doctor` gives the re-exported FUNCTION, not
# the module, because __init__.py rebinds the name. Import the module itself.
import importlib
W = importlib.import_module("fieldkit.wifi.doctor")


# An SSID is not a band. This is the single most common wifi misdiagnosis there
# is, and the owner hit it on 2026-10-13: "5G-Hub-MH3S" on 2437 MHz.
@pytest.mark.parametrize("mhz,expect", [
    ("2412", "2.4 GHz"), ("2437", "2.4 GHz"), ("2462", "2.4 GHz"),
    ("5180", "5 GHz"), ("5745", "5 GHz"),
    ("6115", "6 GHz"),
    ("", "unknown"), (None, "unknown"), ("abc", "unknown"),
])
def test_the_band_comes_from_the_frequency_never_the_name(mhz, expect):
    assert W.band_of(mhz) == expect


# /usr/sbin is not on a normal user's PATH on Debian. shutil.which("iw") returns
# None on a machine that HAS iw, and a doctor written that way tells the user to
# install packages they already have. This cost real time on 2026-10-13.
def test_tools_in_usr_sbin_are_found(monkeypatch, tmp_path):
    fake = tmp_path / "pretend-sbin"
    fake.mkdir()
    binary = fake / "madeuptool"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setattr(W, "SBIN_DIRS", (str(fake),))
    assert W.tool("madeuptool") == str(binary), \
        "a tool outside PATH but in an sbin directory must still be found"
    assert W.tool("definitely-not-a-real-tool") is None


# nmcli -t escapes a colon inside an SSID. Splitting left to right shifts every
# later field and you end up calling int() on "2462 MHz". The owner names a
# network ":(" precisely to catch this.
def test_an_ssid_containing_a_colon_does_not_shift_the_fields(monkeypatch):
    line = r"\:(:11:2462 MHz:82"
    monkeypatch.setattr(W, "run", lambda *a, **k: (0, line + "\n"))
    nets = W.scan()
    assert len(nets) == 1
    n = nets[0]
    assert n["channel"] == "11", f"channel came out {n['channel']!r}"
    assert n["mhz"] == "2462"
    assert n["signal"] == 82, "the signal must be an int, not '2462 MHz'"


def test_a_hidden_network_is_labelled_not_dropped(monkeypatch):
    monkeypatch.setattr(W, "run", lambda *a, **k: (0, ":6:2437 MHz:100\n"))
    nets = W.scan()
    assert nets and nets[0]["ssid"] == "<hidden>", "an unnamed network is still interference"


# 2.4 GHz channels overlap: a network on channel 4 interferes with both 1 and 6.
# Counting only exact matches understates the problem.
def test_channel_advice_counts_overlap_not_just_exact_matches():
    nets = [{"ssid": "a", "channel": "4", "mhz": "2427", "band": "2.4 GHz", "signal": 90},
            {"ssid": "b", "channel": "11", "mhz": "2462", "band": "2.4 GHz", "signal": 10}]
    adv = W.channel_advice(nets)
    assert adv["per_channel"][1]["score"] > 0, "a network on ch 4 must count against ch 1"
    assert adv["per_channel"][6]["score"] > 0, "and against ch 6"
    assert adv["clearest"] == 11, "ch 11 has only a weak neighbour and should win"


def test_five_ghz_networks_are_ignored_when_advising_a_24_channel():
    nets = [{"ssid": "x", "channel": "36", "mhz": "5180", "band": "5 GHz", "signal": 99}]
    adv = W.channel_advice(nets)
    assert all(v["score"] == 0 for v in adv["per_channel"].values()), \
        "a 5 GHz network cannot interfere with a 2.4 GHz channel"


# MCS indexes are the only way to read spatial streams, and that decides whether
# a 2x2 card upgrade can help at all.
@pytest.mark.parametrize("top,streams", [(7, 1), (15, 2), (23, 3)])
def test_mcs_indexes_give_the_stream_count(monkeypatch, top, streams):
    phy = f"\tBand 1:\n\t\tHT TX/RX MCS rate indexes supported: 0-{top}\n"
    monkeypatch.setattr(W, "tool", lambda n: "/usr/sbin/iw" if n == "iw" else None)
    monkeypatch.setattr(W, "run", lambda a, **k: (0, phy) if "iw" in a[0] else (0, ""))
    assert W.card()["streams"] == streams


# The verdict must name the CARD when the card is the limit, because that is the
# finding people chase router settings for hours to avoid hearing.
def test_a_single_band_card_on_24ghz_blames_the_card(monkeypatch):
    monkeypatch.setattr(W, "active_link", lambda: {
        "ssid": "5G-Hub-MH3S", "channel": "6", "mhz": "2437",
        "band": "2.4 GHz", "advertised_rate": "540 Mbit/s", "signal": "84"})
    monkeypatch.setattr(W, "card", lambda: {"bands": ["Band 1"], "streams": 1, "mcs": "0-7",
                                            "name": "AR9485", "driver": "ath9k",
                                            "tools_missing": []})
    monkeypatch.setattr(W, "scan", lambda: [])
    d = W.doctor()
    assert "CARD IS THE LIMIT" in d["verdict"]
    assert d["ok"] is False
    assert any("means nothing" in f for f in d["findings"]), \
        "an SSID claiming 5G while on 2.4 GHz must be called out"


def test_a_dual_band_card_on_24ghz_blames_the_choice_not_the_card(monkeypatch):
    monkeypatch.setattr(W, "active_link", lambda: {
        "ssid": "Home", "channel": "6", "mhz": "2437", "band": "2.4 GHz",
        "advertised_rate": "300 Mbit/s", "signal": "70"})
    monkeypatch.setattr(W, "card", lambda: {"bands": ["Band 1", "Band 2"], "streams": 2,
                                            "name": "x", "driver": "y", "tools_missing": []})
    monkeypatch.setattr(W, "scan", lambda: [])
    d = W.doctor()
    assert "CARD IS THE LIMIT" not in d["verdict"]
    assert "5 GHz" in d["verdict"]


# upgrade() must say what it CANNOT know. Antenna count and BIOS whitelists are
# not readable from software, and a tool that stays quiet about that invites
# somebody to buy a card that will not help.
def test_upgrade_states_what_software_cannot_determine():
    out = W.upgrade()
    blockers = " ".join(out["cannot_determine_here"]).lower()
    assert "antenna" in blockers, "antenna count is physical and must be declared unknown"
    assert "whitelist" in blockers, "a BIOS whitelist must be declared unknown"
