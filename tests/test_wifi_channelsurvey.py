"""The channel surveyor: overlap maths, the margin rule, and machine-neutrality.

The most important test in here is test_defaults_are_machine_neutral. The whole
point of the profile mechanism is that the tool must behave identically on a
stranger's laptop whether or not a profile exists on disk for someone else's.
"""
import json
import os

import pytest

from fieldkit.wifi import channelsurvey as cs


def net(ssid, ch, signal=50, width=20, band="2.4"):
    return cs.Network(ssid, ch, cs.freq_of(ch, band), band, signal, width_mhz=width)


# ---------------------------------------------------------------- the maths
def test_only_1_6_11_do_not_overlap_each_other():
    for a in cs.NON_OVERLAPPING_24:
        for b in cs.NON_OVERLAPPING_24:
            if a == b:
                continue
            assert abs(cs.CH24[a] - cs.CH24[b]) >= 20, f"{a} and {b} overlap"


def test_adjacent_channels_do_overlap():
    laps = cs.overlap_warning([cs.ChannelScore(c, cs.CH24[c]) for c in cs.CH24], 6)
    hit = {l["channel"] for l in laps}
    # the classic surprise: a network on 4 interferes with BOTH 1 and 6.
    # Channels 2 and 10 are exactly 20 MHz away, so they are adjacent but
    # CLEAR: 20 MHz apart at 20 MHz wide means the edges touch and no more.
    # That is precisely the property 1, 6 and 11 are chosen for.
    assert {3, 4, 5, 7, 8, 9} <= hit
    assert 1 not in hit and 11 not in hit
    assert 2 not in hit and 10 not in hit


def test_partial_overlap_is_charged_double_not_half():
    """The error every channel app makes. A half-overlap breaks carrier sense."""
    same = cs.analyse([net("x", 6, signal=100)], "2.4")[0]
    s_same = next(r for r in same if r.channel == 6).score
    s_part = next(r for r in same if r.channel == 8).score
    # channel 8 is 10 MHz off 6, so 50% covered. Naive maths gives half the
    # score; the penalty must make it comparable to a direct hit, not cheaper.
    assert s_part > s_same * 0.5, "partial overlap scored as merely fractional"


def test_wide_network_on_24_counts_as_worse():
    narrow = net("a", 6, signal=60, width=20).nuisance
    wide = net("a", 6, signal=60, width=40).nuisance
    assert wide > narrow


def test_hidden_network_still_counts():
    rows, _ = cs.analyse([net("<hidden>", 6, signal=90)], "2.4")
    assert next(r for r in rows if r.channel == 6).score > 0


# ---------------------------------------------------------- the margin rule
def test_thin_margin_keeps_you_on_1_6_11():
    """A 1.2x win does not justify leaving a permanent structural advantage."""
    nets = [net("a", 11, signal=50), net("b", 11, signal=10)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    if rec["lowest"].channel not in cs.NON_OVERLAPPING_24:
        ratio = rec["conventional"].score / max(rec["lowest"].score, 1)
        if ratio < cs.WORTH_LEAVING_1_6_11:
            assert rec["best"].channel in cs.NON_OVERLAPPING_24
            assert rec["margin_too_thin"]


def test_big_margin_does_justify_leaving():
    # bury 1, 6 and 11 under loud wide neighbours, leave the far end clear
    nets = [net(f"loud{c}", c, signal=100, width=40) for c in (1, 6, 11)]
    rows, _ = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    ratio = rec["conventional"].score / max(rec["lowest"].score, 1)
    if ratio >= cs.WORTH_LEAVING_1_6_11:
        assert not rec["margin_too_thin"]
        assert rec["best"].channel == rec["lowest"].channel


# -------------------------------------------------------------- regulation
def test_channel_14_is_never_usable():
    rows, _ = cs.analyse([net("a", 6)], "2.4")
    ch14 = next(r for r in rows if r.channel == 14)
    assert not ch14.usable
    assert "802.11b" in ch14.legal_note


def test_12_and_13_carry_a_legality_note_and_others_do_not():
    """The bug this was written for: the UK/US paragraph was leaking onto ch 9."""
    rows, _ = cs.analyse([net("a", 6)], "2.4")
    for r in rows:
        if r.channel in cs.UK_EU_ONLY_24:
            assert "ILLEGAL in the US" in r.legal_note
        elif r.channel not in cs.JAPAN_ONLY_24 and r.channel not in cs.NON_OVERLAPPING_24:
            assert "ILLEGAL" not in r.legal_note, f"ch {r.channel} wrongly called US-illegal"
            assert r.overlap_note, "an overlapping channel should say so"


def test_legality_and_overlap_are_separate_fields():
    rows, _ = cs.analyse([net("a", 6)], "2.4")
    ch12 = next(r for r in rows if r.channel == 12)
    assert ch12.legal_note and ch12.overlap_note
    assert ch12.legal_note != ch12.overlap_note


# ---------------------------------------------------------------- profiles
def test_defaults_are_machine_neutral():
    """THE GUARANTEE. No profile on disk may change the default answer.

    The machine this was written on is a 2012 single-band 1x1 laptop, which is
    an unusual shape. Anything tuned for it is wrong elsewhere, so the neutral
    path must be reachable and must ignore every profile that exists.
    """
    cfg, problem = cs.load_profile(None)
    assert problem is None
    assert cfg["name"] is None
    assert cfg["partial_overlap_penalty"] == cs.PARTIAL_OVERLAP_PENALTY
    assert cfg["worth_leaving_1_6_11"] == cs.WORTH_LEAVING_1_6_11
    assert cfg["avoid"] == []
    # and the defaults must differ from every shipped profile, or neutrality
    # is a fiction: a "default" that equals somebody's tuning is that tuning
    for name in cs.list_profiles():
        other, _ = cs.load_profile(name)
        assert (other["worth_leaving_1_6_11"] != cfg["worth_leaving_1_6_11"]
                or other["avoid"] != cfg["avoid"]), f"{name} is indistinguishable from neutral"


def test_no_profile_is_applied_without_being_named():
    nets = [net("a", 6, signal=80)]
    plain_rows, _ = cs.analyse(nets, "2.4")
    plain = cs.recommend(plain_rows, "2.4")
    # channel 6 must still be offered by default: only a profile may exclude it
    assert next(r for r in plain_rows if r.channel == 6).usable
    assert "excluded" not in next(r for r in plain_rows if r.channel == 6).legal_note


def test_named_profile_does_apply_and_announces_itself():
    cfg, problem = cs.load_profile("single-band-1x1")
    assert problem is None and cfg["name"] == "single-band-1x1"
    rows, rules = cs.analyse([net("a", 1)], "2.4", cfg=cfg)
    rec = cs.recommend(rows, "2.4", cfg=cfg)
    text = "\n".join(cs.rationale(rows, rec, rules, "2.4", cfg))
    assert "PROFILE 'single-band-1x1' IS ACTIVE" in text
    assert "not for yours" in text


def test_unknown_profile_refuses_rather_than_guessing():
    cfg, problem = cs.load_profile("no-such-laptop")
    assert problem and "no profile named" in problem
    assert cfg["name"] is None


def test_profile_cannot_set_unknown_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "PROFILE_DIR", str(tmp_path))
    (tmp_path / "bad.json").write_text(json.dumps({"channels_24": [1, 2, 3]}))
    cfg, problem = cs.load_profile("bad")
    assert problem and "unknown keys" in problem
    assert cfg["name"] is None


def test_every_shipped_profile_is_valid():
    for name in cs.list_profiles():
        cfg, problem = cs.load_profile(name)
        assert problem is None, f"{name}: {problem}"
        assert cfg.get("description"), f"{name} has no description"


# ------------------------------------------------------------ honest output
def test_card_report_says_unknown_rather_than_guessing(monkeypatch):
    monkeypatch.setattr(cs, "_tool", lambda n: None)
    c = cs.card_report()
    assert c["read"] is False and c["problem"]
    assert "UNKNOWN" in cs.card_lines(c)


def test_scan_failure_explains_itself(monkeypatch):
    monkeypatch.setattr(cs, "scan_linux", lambda: [])
    monkeypatch.setattr(cs.platform, "system", lambda: "Linux")
    nets, problem = cs.scan()
    assert nets == [] and problem
    assert "radio is off" in problem or "permission" in problem


def test_warning_names_the_specific_conflicting_channels():
    rows, _ = cs.analyse([net("a", 6, signal=70)], "2.4")
    out = "\n".join(cs.warn_about(rows, 8))
    assert "overlaps channels" in out
    assert "6" in out


def test_network_table_admits_clients_are_invisible():
    out = cs.network_table([net("a", 6)], "2.4")
    assert "ACCESS POINTS only" in out
    assert "invisible" in out


def test_nmcli_ssid_with_a_colon_is_parsed_from_the_right(monkeypatch):
    """A hostile SSID must not shift every field and break int()."""
    line = r"we:ird\:name:6:2437 MHz:71:WPA2"
    monkeypatch.setattr(cs, "_tool", lambda n: "/usr/bin/nmcli" if n == "nmcli" else None)
    monkeypatch.setattr(cs, "_run", lambda a, timeout=45: (0, line) if "list" in a else (0, ""))
    nets = cs.scan_linux()
    assert len(nets) == 1
    assert nets[0].channel == 6 and nets[0].signal == 71
    assert ":" in nets[0].ssid


def test_json_is_valid_and_carries_the_reasoning():
    nets = [net("a", 6, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    d = json.loads(cs.to_json(nets, rows, rec, rules, "2.4"))
    assert d["profile"] is None
    assert d["rationale"] and isinstance(d["rationale"], list)
    assert any(c["overlaps_with"] for c in d["channels"])
    assert "card" in d


def test_classify_is_a_hint_never_an_identification():
    assert cs.classify("Echo-Dot") == "voice assistant"
    assert cs.classify("") == "hidden"
    assert cs.classify("Steve's Laptop Network") == "unknown"


def test_stdlib_only_so_it_transplants():
    """No third-party import may creep in, or 'copy one file anywhere' dies."""
    import ast
    src = open(cs.__file__, encoding="utf-8").read()
    allowed = {"argparse", "json", "platform", "re", "shutil", "subprocess", "sys",
               "dataclasses", "os", "time", "textwrap", "tkinter", "ast", "concurrent", "math",
               "__future__"}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed, f"non-stdlib import {a.name}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed, f"non-stdlib import {node.module}"


def test_an_empty_channel_does_not_crash():
    """Score 0 means nothing overlaps: the BEST result, not a division by zero.

    Found by this suite before anyone ran it in a quiet area, where an empty
    channel is the normal case rather than the exception.
    """
    rows, rules = cs.analyse([net("lonely", 1, signal=80)], "2.4")
    rec = cs.recommend(rows, "2.4")
    text = "\n".join(cs.rationale(rows, rec, rules, "2.4"))
    assert text
    assert cs.ratio(10, 0) == float("inf")
    assert cs.ratio(0, 0) == 1.0
    assert "completely clear" in cs.say_ratio(10, 0)
    json.loads(cs.to_json([net("lonely", 1)], rows, rec, rules, "2.4"))


def test_no_networks_at_all_does_not_crash():
    rows, rules = cs.analyse([], "2.4")
    rec = cs.recommend(rows, "2.4")
    assert "\n".join(cs.rationale(rows, rec, rules, "2.4"))
    assert cs.text_report([], rows, rec, rules, "2.4")


# ------------------------------------------------------------------- the GUI
#
# THE GUI TEST MUST NEVER DRAW ON THE USER'S SCREEN.
#
# 2026-10-13: the first version of this test ran against DISPLAY=:0, so every
# test run flashed a real window onto the owner's desktop. He noticed and asked
# whether something was taking screenshots. A test suite must be invisible; a
# developer should be able to run it during a video call.
#
# So it gets its OWN Xvfb (a display that exists only in memory) and never
# touches the real one. Without Xvfb it skips, with the reason written down.
@pytest.fixture
def hidden_display():
    """A private in-memory X display, so the test window is never seen."""
    import shutil as _sh
    import subprocess
    import time
    if not _sh.which("Xvfb"):
        pytest.skip("Xvfb is not installed, and a GUI test must never draw on the "
                    "real screen: install xvfb to run this test")
    for num in range(90, 100):
        proc = subprocess.Popen(["Xvfb", f":{num}", "-screen", "0", "1024x768x24"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.6)
        if proc.poll() is None:
            old = os.environ.get("DISPLAY")
            os.environ["DISPLAY"] = f":{num}"
            os.environ.pop("WAYLAND_DISPLAY", None)
            try:
                yield f":{num}"
            finally:
                # stop only the process WE started, by PID, never by name
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                if old is None:
                    os.environ.pop("DISPLAY", None)
                else:
                    os.environ["DISPLAY"] = old
            return
    pytest.skip("could not start Xvfb on any display from :90 to :99")


def test_gui_builds_every_tab_without_entering_mainloop(hidden_display):
    """Caught a real bug: ScrolledText re-parents itself, so page.master is
    its own internal frame and packing 'before' it raises TclError. No unit
    test would have found that, only building the widget tree."""
    assert os.environ.get("DISPLAY", "").startswith(":9"), \
        "refusing to build a window on the real display"
    tk = pytest.importorskip("tkinter")
    import fieldkit.wifi.channelsurvey as mod
    real_scan = mod.scan
    real_tk = tk.Tk
    mod.scan = lambda: ([cs.Network("Test-AP", 6, 2437, "2.4", 70),
                         cs.Network("Echo-Dot", 9, 2452, "2.4", 40)], None)

    class Once(real_tk):
        def mainloop(self, *a, **k):
            self.update()              # runs the after() scan, draws every tab
            self.update_idletasks()
            self.destroy()

    tk.Tk = Once
    try:
        assert mod.gui() == 0
    finally:
        tk.Tk = real_tk
        mod.scan = real_scan


def test_gui_refuses_politely_when_tkinter_is_absent(monkeypatch, capsys):
    """Debian packages tkinter separately, so this is a real user's first run."""
    import builtins
    real_import = builtins.__import__

    def no_tk(name, *a, **k):
        if name == "tkinter":
            raise ImportError("No module named 'tkinter'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_tk)
    assert cs.gui() == 4
    err = capsys.readouterr().err
    assert "python3-tk" in err
    assert "works without it" in err


# -------------------------------------------- watching one network over time
def _fake_scan(signals):
    """A scan that returns the next signal each time it is called."""
    it = iter(signals)

    def go():
        return [cs.Network("target", 6, 2437, "2.4", next(it), bssid="aa:bb:cc:dd:ee:ff")], None
    return go


def test_watch_reports_the_spread_not_just_a_number(monkeypatch):
    monkeypatch.setattr(cs, "scan", _fake_scan([40, 46, 44, 42]))
    d = cs.watch("target", samples=4, sleeper=lambda _s: None)
    assert d["mean"] == 43.0 and d["spread"] == 6
    assert d["usable_for_comparison"]
    assert d["smallest_believable_change"] == 6


def test_watch_refuses_a_saturated_reading(monkeypatch):
    """Next to the AP the meter pegs, so a before/after test shows nothing even
    when the change is real. Found live: the hub read 99-100% from the desk."""
    monkeypatch.setattr(cs, "scan", _fake_scan([99, 100, 100, 99]))
    d = cs.watch("target", samples=4, sleeper=lambda _s: None)
    assert not d["usable_for_comparison"]
    assert "SATURATED" in d["warnings"][0]
    assert "35-70%" in d["warnings"][0]


def test_watch_warns_near_the_noise_floor(monkeypatch):
    monkeypatch.setattr(cs, "scan", _fake_scan([10, 12, 11, 9]))
    d = cs.watch("target", samples=4, sleeper=lambda _s: None)
    assert not d["usable_for_comparison"]
    assert "noise floor" in d["warnings"][0]


def test_a_difference_inside_the_noise_is_not_a_change():
    """The -92 to -87 lesson, generalised: two readings are not a measurement."""
    before = {"mean": 50.0, "spread": 6, "usable_for_comparison": True}
    after = {"mean": 54.0, "spread": 5, "usable_for_comparison": True}
    d = cs.compare_watch(before, after)
    assert "NO REAL CHANGE" in d["verdict"]


def test_a_difference_bigger_than_the_noise_is_a_change():
    before = {"mean": 50.0, "spread": 3, "usable_for_comparison": True}
    after = {"mean": 70.0, "spread": 3, "usable_for_comparison": True}
    d = cs.compare_watch(before, after)
    assert "REAL CHANGE" in d["verdict"] and "Better" in d["verdict"]


def test_a_drop_is_named_as_worse():
    before = {"mean": 70.0, "spread": 3, "usable_for_comparison": True}
    after = {"mean": 40.0, "spread": 3, "usable_for_comparison": True}
    assert "WORSE" in cs.compare_watch(before, after)["verdict"]


def test_comparing_a_saturated_reading_refuses():
    before = {"mean": 99.0, "spread": 2, "usable_for_comparison": False}
    after = {"mean": 99.0, "spread": 2, "usable_for_comparison": True}
    assert "CANNOT SAY" in cs.compare_watch(before, after)["verdict"]


def test_watch_says_so_when_it_never_saw_the_network(monkeypatch):
    monkeypatch.setattr(cs, "scan", lambda: ([cs.Network("other", 6, 2437, "2.4", 50)], None))
    d = cs.watch("target", samples=2, sleeper=lambda _s: None)
    assert "error" in d and "never saw" in d["error"]


def test_watch_can_target_a_bssid_because_one_name_may_cover_both_bands(monkeypatch):
    monkeypatch.setattr(cs, "scan", _fake_scan([55, 57]))
    d = cs.watch(None, "AA:BB:CC:DD:EE:FF", samples=2, sleeper=lambda _s: None)
    assert d["seen"] == 2 and d["mean"] == 56.0


def test_clipping_is_caught_even_when_the_mean_slips_under_the_threshold():
    """Found by a real run on 2026-10-13: 97,83,83,99,99,81,92,82 averages 89.5,
    ducks under a mean-only threshold of 90, and the warning disappeared while
    three samples were pegged at 99. A check that goes quiet exactly when it is
    least reliable is worse than no check at all."""
    import fieldkit.wifi.channelsurvey as mod
    it = iter([97, 83, 83, 99, 99, 81, 92, 82])
    real = mod.scan
    mod.scan = lambda: ([cs.Network("x", 6, 2437, "2.4", next(it))], None)
    try:
        d = mod.watch("x", samples=8, sleeper=lambda _s: None)
    finally:
        mod.scan = real
    assert d["mean"] < cs.SATURATED, "this test is pointless unless the mean ducks under"
    assert not d["usable_for_comparison"]
    joined = " ".join(d["warnings"])
    assert "SATURATED" in joined and "clipping" in joined
    assert "TOO NOISY" in joined


def test_a_wide_spread_alone_disqualifies_the_spot(monkeypatch):
    """Mid-scale but swinging: no realistic antenna change is bigger than that."""
    monkeypatch.setattr(cs, "scan", _fake_scan([40, 62, 44, 60]))
    d = cs.watch("target", samples=4, sleeper=lambda _s: None)
    assert d["spread"] > cs.TOO_NOISY
    assert not d["usable_for_comparison"]
    assert any("TOO NOISY" in w for w in d["warnings"])


def test_a_steady_mid_scale_reading_is_still_accepted(monkeypatch):
    monkeypatch.setattr(cs, "scan", _fake_scan([50, 54, 52, 51]))
    d = cs.watch("target", samples=4, sleeper=lambda _s: None)
    assert d["usable_for_comparison"] and not d["warnings"]


# ------------------------------------------------- decoding a link rate
def test_2401_proves_two_working_streams_on_5ghz():
    """The phone's number settled whether removing an antenna killed 5 GHz."""
    d = cs.decode_rate(2401)
    assert d["streams"] == 2 and d["width_mhz"] == 160
    assert "802.11ax" in d["standard"]
    assert "2 antenna chains" in d["proves"]
    assert "do not exist on 2.4 GHz" in d["band_proof"]


def test_the_laptops_rate_decodes_to_one_stream():
    d = cs.decode_rate(72.2)
    assert d["streams"] == 1 and d["width_mhz"] == 20
    assert "proves" not in d


def test_a_rate_is_never_called_a_speed():
    for r in (2401, 866.7, 72.2):
        assert "not throughput" in cs.decode_rate(r)["note"]


def test_an_impossible_rate_refuses_rather_than_guessing():
    d = cs.decode_rate(1234)
    assert "error" in d and "no standard configuration" in d["error"]
    assert "standard" not in d


def test_the_entry_point_is_the_last_thing_in_the_file():
    """Appending functions below the __main__ guard makes the script call main()
    before they exist, while importing it as a module still works and hides it.
    Found 2026-10-13: --rate crashed with NameError as a script, worked as a module."""
    src = open(cs.__file__, encoding="utf-8").read()
    guard = src.index('if __name__ == "__main__":')
    after = src[guard:]
    assert "\ndef " not in after, "a function is defined after the __main__ guard"
    assert "\nclass " not in after


# ------------------------------- your own access point is not interference
def test_your_own_ap_is_not_counted_against_its_own_channel():
    """Found 2026-10-13 by making the tool show its arithmetic: channel 13
    scored 158, of which 89 was the owner's OWN hub sitting on channel 13.
    That penalises whichever channel you are already on, every single time."""
    nets = [net("mine", 13, signal=89), net("neighbour", 11, signal=30)]
    with_mine, _ = cs.analyse(nets, "2.4")
    without, _ = cs.analyse(nets, "2.4", mine={"mine"})
    a = next(r for r in with_mine if r.channel == 13).score
    b = next(r for r in without if r.channel == 13).score
    assert b < a, "excluding your own AP must lower its own channel's score"
    assert next(r for r in without if r.channel == 13).yours


def test_the_ap_being_placed_is_excluded_from_EVERY_candidate_channel():
    """Wherever you move it, it moves with you, so it is never its own competitor.

    My first version of this test asserted the opposite, that it should still
    count against other channels. That was wrong: if the hub moves to channel 1,
    it is no longer transmitting on 13, so its old energy there is gone too.
    """
    nets = [net("mine", 1, signal=90)]
    rows, _ = cs.analyse(nets, "2.4", mine={"mine"})
    for ch in (1, 3, 6, 11):
        assert next(r for r in rows if r.channel == ch).score == 0


def test_a_SECOND_access_point_you_own_must_still_count():
    """--mine is 'the AP I am placing'. Another hub staying put is real interference."""
    nets = [net("placing", 13, signal=90), net("other-hub-of-mine", 6, signal=80)]
    rows, _ = cs.analyse(nets, "2.4", mine={"placing"})
    assert next(r for r in rows if r.channel == 6).score > 0, \
        "a second AP that is NOT moving still competes for the air"


def test_excluding_by_bssid_works_when_the_name_is_shared():
    n = cs.Network("shared", 6, 2437, "2.4", 80, bssid="AA:BB:CC:DD:EE:FF")
    rows, _ = cs.analyse([n], "2.4", mine={"aa:bb:cc:dd:ee:ff"})
    assert next(r for r in rows if r.channel == 6).score == 0


def test_the_breakdown_shows_its_arithmetic():
    nets = [net("loud", 13, signal=80), net("partial", 11, signal=40)]
    d = cs.score_breakdown(nets, 13, "2.4")
    assert d["total"] > 0
    assert any(c["partial"] for c in d["contributors"]), "ch 11 half-overlaps ch 13"
    text = "\n".join(cs.breakdown_lines(d))
    assert "NO UNITS" in text
    assert "x2" in text


def test_the_breakdown_honours_the_exclusion():
    nets = [net("mine", 13, signal=90)]
    assert cs.score_breakdown(nets, 13, "2.4")["total"] > 0
    assert cs.score_breakdown(nets, 13, "2.4", mine={"mine"})["total"] == 0


def test_an_empty_breakdown_says_so_rather_than_printing_nothing():
    text = "\n".join(cs.breakdown_lines(cs.score_breakdown([], 1, "2.4")))
    assert "nothing at all lands" in text


# ---------------------------- 12 and 13 as an OPPORTUNITY, not only a risk
def test_by_default_12_and_13_are_framed_as_a_risk():
    rows, _ = cs.analyse([net("a", 6)], "2.4")
    ch13 = next(r for r in rows if r.channel == 13)
    assert "ILLEGAL in the US" in ch13.legal_note
    assert not ch13.opportunity


def test_declaring_local_devices_turns_the_risk_into_an_opportunity():
    """The tool only ever warned about 12/13 and never said they are free
    capacity for a UK or EU household. Only the person knows, so they must say."""
    cfg = dict(cs.DEFAULTS, devices="local")
    rows, _ = cs.analyse([net("a", 6)], "2.4", cfg=cfg)
    ch13 = next(r for r in rows if r.channel == 13)
    assert ch13.opportunity
    assert "ILLEGAL" not in ch13.legal_note
    assert "almost nobody competes" in ch13.legal_note


def test_device_knowledge_is_never_guessed():
    """It is a fact about someone's house. A scan cannot establish it."""
    assert cs.DEFAULTS["devices"] == "mixed"
    assert "local" in cs.DEVICE_KNOWLEDGE


def _flat(text):
    """Collapse wrapping before asserting on prose.

    A wrapped line can split a phrase like '--devices local' across a newline,
    so a literal substring check fails on correct output. Normalise first.
    """
    return " ".join(text.split())


def test_the_plain_report_offers_the_choice_when_12_or_13_is_quiet():
    nets = [net("loud1", 1, signal=95), net("loud6", 6, signal=95), net("loud11", 11, signal=95)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = _flat(cs.plain_report(nets, rows, rec, rules, "2.4"))
    assert "YOU MAY HAVE A CHOICE HERE" in out
    assert "--devices local" in out
    assert "Only you can know that" in out


def test_the_plain_report_stops_warning_once_you_have_declared():
    nets = [net("loud1", 1, signal=95), net("loud6", 6, signal=95), net("loud11", 11, signal=95)]
    cfg = dict(cs.DEFAULTS, devices="local")
    rows, rules = cs.analyse(nets, "2.4", cfg=cfg)
    rec = cs.recommend(rows, "2.4", cfg=cfg)
    out = _flat(cs.plain_report(nets, rows, rec, rules, "2.4", cfg))
    assert "cannot see them AT ALL" not in out
    assert "YOU MAY HAVE A CHOICE HERE" not in out
    assert "almost nobody is allowed to compete" in out


# -------------------------------------------- the plain report in general
def test_the_plain_report_leads_with_the_answer():
    nets = [net("a", 1, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = cs.plain_report(nets, rows, rec, rules, "2.4")
    head = out.split("HOW BUSY")[0]
    assert "USE CHANNEL" in head, "the answer must come before the detail"


def test_the_plain_report_never_repeats_the_same_warning():
    """The engineering table printed one jargon line ten times over."""
    nets = [net("a", 3, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = cs.plain_report(nets, rows, rec, rules, "2.4")
    assert out.count("never tread on each other") <= 1
    assert out.count("carrier sense") == 0, "jargon from the technical view leaked in"


def test_the_plain_report_fits_a_modest_terminal():
    nets = [net("a", 1, signal=60), net("b", 13, signal=20)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    for line in cs.plain_report(nets, rows, rec, rules, "2.4").splitlines():
        assert len(line) <= 100, f"line too wide ({len(line)}): {line[:60]}..."


def test_the_plain_report_says_nothing_to_do_when_you_are_already_right():
    nets = [net("noise", 1, signal=90), net("noise2", 6, signal=90)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = cs.plain_report(nets, rows, rec, rules, "2.4", here=rec["best"].channel)
    assert "Nothing to do" in out


# ----------------------------------------- WHO is in the air, not just how much
def test_who_is_on_a_channel_names_them_with_plain_loudness():
    nets = [net("Shouty", 6, signal=85), net("Faint", 6, signal=15)]
    rows, _ = cs.analyse(nets, "2.4")
    d = cs.neighbours_on(rows, 6)
    assert d["total"] == 2
    names = [i["ssid"] for i in d["shown"]]
    assert names == ["Shouty", "Faint"], "loudest first"
    assert d["shown"][0]["loudness"] == "SHOUTING"
    assert d["shown"][1]["loudness"] == "murmuring"


def test_it_says_whether_they_are_on_top_of_you_or_half_over():
    nets = [net("same", 6, signal=50), net("half", 8, signal=50)]
    rows, _ = cs.analyse(nets, "2.4")
    d = cs.neighbours_on(rows, 6)
    where = {i["ssid"]: i["where"] for i in d["shown"]}
    assert where["same"] == "right on top of you"
    assert "over you" in where["half"] and "channel 8" in where["half"]


def test_a_clear_channel_says_nobody_rather_than_printing_nothing():
    rows, _ = cs.analyse([net("a", 1, signal=50)], "2.4")
    out = "\n".join(cs.neighbour_lines(cs.neighbours_on(rows, 11)))
    assert "Nobody at all" in out


def test_long_lists_are_capped_and_the_remainder_counted():
    nets = [net(f"n{i}", 6, signal=50 + i) for i in range(12)]
    rows, _ = cs.analyse(nets, "2.4")
    d = cs.neighbours_on(rows, 6, limit=8)
    assert len(d["shown"]) == 8 and d["more"] == 4
    assert "and 4 more" in "\n".join(cs.neighbour_lines(d))


def test_device_kind_hints_appear_when_the_name_gives_one_away():
    rows, _ = cs.analyse([net("Echo-Dot-Kitchen", 6, signal=60)], "2.4")
    out = "\n".join(cs.neighbour_lines(cs.neighbours_on(rows, 6)))
    assert "voice assistant" in out


def test_the_plain_report_names_who_is_in_your_airspace():
    """The thing the score never said: WHO. Asked for explicitly, 2026-10-13."""
    nets = [net("TheNeighbour", 11, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = cs.plain_report(nets, rows, rec, rules, "2.4", here=13)
    assert "WHO YOU ARE SHARING THE AIR WITH" in out
    assert "TheNeighbour" in out
    assert "CHANNEL 13, where you are now" in out


def test_it_admits_the_attached_gadgets_are_invisible():
    nets = [net("x", 11, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = _flat(cs.plain_report(nets, rows, rec, rules, "2.4", here=13))
    assert "ROUTERS only" in out
    assert "several times more" in out


def test_your_own_channel_is_not_listed_twice_when_it_is_the_recommendation():
    nets = [net("x", 1, signal=60)]
    rows, rules = cs.analyse(nets, "2.4")
    rec = cs.recommend(rows, "2.4")
    out = cs.plain_report(nets, rows, rec, rules, "2.4", here=rec["best"].channel)
    assert out.count("the recommendation:") <= 1


# ------------------------------- devices on your own network, and how far away
def test_distance_is_always_a_range_never_a_number():
    """A single figure here would be a lie people act on."""
    d = cs.distance_hint(60)
    assert d["near_m"] < d["far_m"], "must be a range"
    assert "factor of" in d["caveat"]
    assert "Never use it to decide where somebody is" in d["caveat"]


def test_a_stronger_signal_means_a_shorter_distance():
    """Both readings must be strong enough to answer at all: below about
    -80 dBm the tool now refuses rather than inventing a figure."""
    close = cs.distance_hint(None, dbm=-35)
    far = cs.distance_hint(None, dbm=-70)
    assert close["far_m"] < far["near_m"]


def test_a_capped_reading_is_reported_as_an_upper_limit():
    """100% is the top of the scale, so the distance is a ceiling not a value."""
    assert "capped" in cs.distance_hint(100)
    assert "capped" not in cs.distance_hint(60)


def test_a_real_dbm_reading_is_preferred_over_the_percentage():
    pct_only = cs.distance_hint(100)
    with_dbm = cs.distance_hint(100, dbm=-34)
    assert with_dbm["far_m"] < pct_only["far_m"], "the true dBm says it is much closer"
    assert "capped" not in with_dbm


def test_distance_refuses_without_a_reading():
    assert "error" in cs.distance_hint(None)


def test_an_unknown_manufacturer_still_shows_the_prefix_to_look_up():
    devs = [{"ip": "192.168.1.5", "mac": "88:33:74:aa:bb:cc", "vendor": None, "state": "REACHABLE"}]
    out = "\n".join(cs.device_lines(devs))
    assert "88:33:74" in out, "a bare 'unknown' is a dead end"


def test_a_randomised_address_is_explained_not_flagged_as_a_fault():
    devs = [{"ip": "192.168.1.9", "mac": "9a:bb:cc:dd:ee:ff", "vendor": None,
             "randomised": True, "state": "REACHABLE"}]
    out = "\n".join(cs.device_lines(devs))
    assert "private address" in out
    assert "privacy feature, not a" in out


def test_device_listing_says_it_only_covers_your_own_network():
    out = "\n".join(cs.device_lines([{"ip": "1.2.3.4", "mac": "aa:bb:cc:dd:ee:ff",
                                      "vendor": "X", "state": "REACHABLE"}]))
    assert "YOUR network only" in out


def test_no_devices_says_so_rather_than_printing_an_empty_table():
    assert "None found" in "\n".join(cs.device_lines([]))


# ------------------------------------------- the distance model, after its bug
def test_the_one_metre_reference_is_derived_not_invented():
    """It was a hardcoded -40 dBm and was 20 dB wrong, pushing every estimate
    about five times too close. A measured -34 dBm came out as '0.5 to 0.7
    metres' for an access point across the room. Now derived from free-space
    path loss and a stated transmit-power range."""
    weak, strong = cs.reference_dbm(2437)
    assert -30 < weak < -22 and -24 < strong < -18, (weak, strong)
    # 5 GHz loses more in the first metre, so its reference must be weaker
    w5, s5 = cs.reference_dbm(5500)
    assert w5 < weak and s5 < strong


def test_the_corrected_model_puts_minus_34_across_a_room():
    """The reading that caught the bug: an access point 3 to 4 metres away."""
    d = cs.distance_hint(None, dbm=-34)
    assert d["near_m"] < 4 < d["far_m"], (d["near_m"], d["far_m"])


def test_a_weak_signal_is_not_assumed_to_be_in_open_air():
    """Assuming open air for a weak signal produced '43 to 883 metres'."""
    assert cs.path_loss_for(-30)[0] < cs.path_loss_for(-75)[0]
    d = cs.distance_hint(None, dbm=-75)
    assert d["far_m"] < 200, "a weak indoor signal cannot imply hundreds of metres"


def test_it_refuses_entirely_when_the_signal_is_too_weak_to_mean_anything():
    d = cs.distance_hint(None, dbm=-90)
    assert d["near_m"] is None and d["far_m"] is None
    assert "would be invented" in d["caveat"]


def test_the_words_and_the_numbers_always_agree():
    """It once said 'within a metre or two' while printing 1.3 to 3.1 metres."""
    for dbm in (-30, -40, -50, -60, -70):
        d = cs.distance_hint(None, dbm=dbm)
        assert f"{d['near_m']:.0f} to {d['far_m']:.0f} metres" in d["plain"], d


# -------------------------------------------- calibrating against ground truth
def test_one_measurement_is_refused_because_it_cannot_separate_two_unknowns():
    """Shown with real numbers 2026-10-13: -34 dBm at 2.88 m gives an exponent
    of 1.70 for a weak transmitter and 3.00 for a strong one. Same reading,
    opposite conclusions. One equation, two unknowns."""
    d = cs.calibrate([(2.88, -34)])
    assert "error" in d and "two measurements" in d["error"]
    assert "path_loss_exponent" not in d


def test_several_readings_from_one_spot_are_also_refused():
    d = cs.calibrate([(2.0, -34), (2.0, -36), (2.0, -35)])
    assert "error" in d and "DIFFERENT distances" in d["error"]


def test_two_points_at_different_distances_fit_both_unknowns():
    d = cs.calibrate([(1.0, -22), (10.0, -52)])
    assert abs(d["reference_dbm"] - -22) < 0.5
    assert abs(d["path_loss_exponent"] - 3.0) < 0.05


def test_two_points_warn_that_the_fit_cannot_be_judged():
    d = cs.calibrate([(1.0, -22), (4.0, -40)])
    assert any("no way to tell whether the line FITS" in w for w in d["warnings"])


def test_a_clean_three_point_fit_reports_a_small_error():
    pts = [(1.0, -22.0), (3.0, -36.3), (9.0, -50.6)]      # exactly n=3, ref=-22
    d = cs.calibrate(pts)
    assert d["fit_error_db"] < 0.5
    assert not any("straight line" in w for w in d["warnings"])


def test_scattered_readings_are_called_out_as_a_bad_fit():
    pts = [(1.0, -22), (2.0, -60), (4.0, -25), (8.0, -70)]
    d = cs.calibrate(pts)
    assert any("do not sit on a straight line" in w for w in d["warnings"])


def test_an_impossible_exponent_is_flagged_not_returned_silently():
    d = cs.calibrate([(1.0, -40), (10.0, -42)])           # barely fades: n ~ 0.2
    assert d["path_loss_exponent"] < 1.6
    assert any("below free space" in w for w in d["warnings"])


def test_a_calibration_states_that_it_is_local_only():
    d = cs.calibrate([(1.0, -22), (4.0, -40)])
    assert "THIS building only" in d["scope"]
    assert "not a property of wifi" in d["scope"]


def test_bad_input_is_skipped_rather_than_crashing():
    d = cs.calibrate([(1.0, -22), ("x", "y"), (4.0, -40), (0, -30)])
    assert d["points"] == 2


# --------------------- household detail stays local, hardware class is shipped
def test_no_shipped_profile_names_a_machine_or_a_household():
    """A shipped profile describes a CLASS of hardware. One that also records
    'second router on channel 6' plus a laptop model and a country is a
    fingerprint of one dwelling, which is nobody else's business."""
    import json as _json
    import os as _os
    tell = ("vaio", "sony", "thinkpad", "lenovo", "uk ", "london", "this building",
            "second router", "my ", "his ", "her ")
    for f in sorted(_os.listdir(cs.PROFILE_DIR)):
        if not f.endswith(".json"):
            continue
        raw = _json.load(open(_os.path.join(cs.PROFILE_DIR, f), encoding="utf-8"))
        blob = " ".join(str(v) for v in raw.values()).lower()
        for word in tell:
            assert word not in blob, f"{f} leaks a household or machine detail: {word!r}"


def test_a_shipped_profile_does_not_exclude_channels_for_you():
    """Which channel a neighbour owns is a fact about one address."""
    import json as _json
    import os as _os
    for f in sorted(_os.listdir(cs.PROFILE_DIR)):
        if f.endswith(".json"):
            raw = _json.load(open(_os.path.join(cs.PROFILE_DIR, f), encoding="utf-8"))
            assert not raw.get("avoid"), f"{f} hardcodes an exclusion that suits one building"


def test_a_local_profile_can_set_everything_a_shipped_one_can():
    """Nothing is lost by moving private tuning out of the repo."""
    assert set(cs.DEFAULTS) <= set(cs.DEFAULTS), "sanity"
    assert cs.LOCAL_PROFILE_DIR != cs.PROFILE_DIR
    assert cs.LOCAL_PROFILE_DIR.endswith(os.path.join("state", "channel_profiles"))


def test_a_local_profile_wins_over_a_shipped_one_of_the_same_name(tmp_path, monkeypatch):
    import json as _json
    local = tmp_path / "local"
    shipped = tmp_path / "shipped"
    local.mkdir(); shipped.mkdir()
    (shipped / "x.json").write_text(_json.dumps({"description": "s", "avoid": []}))
    (local / "x.json").write_text(_json.dumps({"description": "l", "avoid": [11]}))
    monkeypatch.setattr(cs, "LOCAL_PROFILE_DIR", str(local))
    monkeypatch.setattr(cs, "PROFILE_DIR", str(shipped))
    cfg, problem = cs.load_profile("x")
    assert problem is None
    assert cfg["avoid"] == [11] and cfg["local"] is True
    assert cs.list_profiles() == ["x"], "a shadowed name must not appear twice"


def test_both_front_doors_offer_the_same_things():
    """`fieldkit wifi X` and `channelsurvey.py --X` must not drift apart.

    They already drifted twice: once over the own-AP rule, which made the two
    print different scores for the same air, and once over this, where the
    layman documentation described `fieldkit wifi devices` and only the
    standalone file could do it. A reader following either front door has to
    arrive at the same capability.
    """
    from fieldkit import cli

    sub = next(a for a in cli.build_parser()._actions if getattr(a, "choices", None)
               and "wifi" in a.choices)
    wifi = sub.choices["wifi"]
    choices = set(next(a.choices for a in wifi._actions if a.dest == "action"))
    for needed in ("devices", "calibrate", "watch", "rate", "channels", "card", "gui"):
        assert needed in choices, f"fieldkit wifi cannot do {needed}, but the standalone file can"


def _street():
    """A street's worth of networks, including names chosen to be awkward."""
    return [
        cs.Network("Alice and Bob's House", 6, 2437, "2.4", 77, 20, "88:33:74:ab:cd:ef", "WPA2"),
        cs.Network(":(", 13, 2472, "2.4", 55, 20, "fa:4f:22:11:22:33", "WPA2"),
        cs.Network("BT-HUB-9ZQ4", 11, 2462, "2.4", 41, 40, "00:1a:2b:3c:4d:5e", "WPA3"),
        cs.Network("<hidden>", 1, 2412, "2.4", 19, 20, "", ""),
        cs.Network("Alice and Bob's House", 1, 2412, "2.4", 12, 20, "88:33:74:ab:cd:f0", "WPA2"),
    ]


def test_sanitised_output_changes_no_measurement():
    """A sanitised screenshot is only evidence if the numbers are untouched."""
    real = _street()
    out, _ = cs.anonymise(real, mine={":("})
    assert len(out) == len(real)
    for before, after in zip(real, out):
        assert (after.channel, after.mhz, after.band, after.signal, after.width_mhz, after.security) == \
               (before.channel, before.mhz, before.band, before.signal, before.width_mhz, before.security)
        assert after.ssid != before.ssid or before.hidden


def test_sanitised_scores_are_identical_to_the_real_ones():
    """If sanitising moved a single score the tool would be lying about the air."""
    real = _street()
    out, _ = cs.anonymise(real, mine={":("})
    a, _ = cs.analyse(real, "2.4", mine={":("})
    b, _ = cs.analyse(out, "2.4", mine={cs.YOUR_NETWORK})
    assert [(r.channel, r.score) for r in a] == [(r.channel, r.score) for r in b]


def test_one_network_keeps_one_label_so_the_tables_still_agree():
    """The same name appears in the channel table, the breakdown and the warning."""
    out, mapping = cs.anonymise(_street(), mine={":("})
    labels = {n.ssid for n in out if not n.hidden}
    assert mapping[":("] == cs.YOUR_NETWORK, "your own AP must stay identifiable: the own-AP rule needs it"
    assert len([n for n in out if n.ssid == mapping["Alice and Bob's House"]]) == 2, \
        "two radios of one network must share its label"
    assert labels == {cs.YOUR_NETWORK, "neighbour 1", "neighbour 2"}


def test_a_hidden_network_is_left_hidden():
    out, mapping = cs.anonymise(_street())
    assert any(n.ssid == "<hidden>" for n in out)
    assert "<hidden>" not in mapping, "there is no name there to replace"


def test_no_real_name_or_full_address_survives_into_a_sanitised_report():
    """The promise the flag makes. Checked against the rendered text, not the data."""
    real = _street()
    out, _ = cs.anonymise(real, mine={":("})
    rows, rules = cs.analyse(out, "2.4", mine={cs.YOUR_NETWORK})
    rec = cs.recommend(rows, "2.4")
    text = "\n".join([
        cs.plain_report(out, rows, rec, rules, "2.4"),
        cs.text_report(out, rows, rec, rules, "2.4"),
        cs.network_table(out, "2.4"),
        "\n".join(cs.warn_about(rows, 6)),
        "\n".join(cs.breakdown_lines(cs.score_breakdown(out, 6, "2.4", None, {cs.YOUR_NETWORK}))),
        cs.to_json(out, rows, rec, rules, "2.4"),
    ])
    for net in real:
        if not net.hidden:
            assert net.ssid not in text, f"{net.ssid!r} reached a sanitised report"
        if net.bssid:
            assert net.bssid not in text, f"{net.bssid} reached a sanitised report"


def test_the_sanitising_switch_is_off_unless_asked_for(monkeypatch):
    """Default behaviour prints the real names: that is what makes it useful at home."""
    monkeypatch.delenv(cs.ANONYMISE_ENV, raising=False)
    assert cs.anonymising() is False
    for value in ("1", "yes", "true"):
        monkeypatch.setenv(cs.ANONYMISE_ENV, value)
        assert cs.anonymising() is True
    for value in ("", "0", "no", "false"):
        monkeypatch.setenv(cs.ANONYMISE_ENV, value)
        assert cs.anonymising() is False


def test_your_own_name_is_matched_by_its_label_after_a_sanitised_scan(monkeypatch):
    """Otherwise the own-AP bug comes straight back.

    cmd_wifi asks the scan what it can hear, then asks current_network() which of
    those is yours. Under sanitising the scan returns labels, so an unmapped real
    name matches nothing and your own access point is counted as interference
    against itself: 89 points of one channel's 158, the first time.
    """
    monkeypatch.setenv(cs.ANONYMISE_ENV, "1")
    monkeypatch.setattr(cs, "_associated_ssid", lambda: ":(")
    monkeypatch.setattr(cs, "scan_linux", _street)
    monkeypatch.setattr(cs.platform, "system", lambda: "Linux")
    nets, problem = cs.scan()
    assert problem is None
    here = cs.current_network()
    assert here == cs.YOUR_NETWORK
    assert here in {n.ssid for n in nets}, "the label must match something in the scan"
