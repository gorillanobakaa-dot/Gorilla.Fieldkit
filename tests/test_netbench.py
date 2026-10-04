"""netbench: link arithmetic, fixtures, fail-closed summaries, log parsing, memory maths, compare, file safety."""
import json
import struct
import zlib
from pathlib import Path

import pytest

from fieldkit.netbench import benches, browser, fixtures, links, report


# --- links -------------------------------------------------------------------------------------------------------
def test_direction_serialises_at_the_link_rate_and_adds_half_the_rtt():
    d = links.Direction(8_000_000, 0.100)          # 1 MB/s, 100 ms round trip
    a1 = d.schedule(100_000, now=0.0)
    a2 = d.schedule(100_000, now=0.0)              # queued behind the first
    assert a1 == pytest.approx(0.1 + 0.05)
    assert a2 == pytest.approx(0.2 + 0.05)
    assert d.backlog(0.0) == pytest.approx(0.2)
    assert d.schedule(1000, now=10.0) == pytest.approx(10.001 + 0.05)   # an idle link starts at once


def test_loss_is_deterministic_for_a_seed_and_costs_one_round_trip():
    def run(seed):
        d = links.Direction(80e6, 0.6, loss=0.05, seed=seed)
        return [d.schedule(14480, now=i * 1.0) for i in range(200)], d.losses
    a, la = run(3)
    b, lb = run(3)
    assert a == b and la == lb and la > 0
    clean = [i * 1.0 + 14480 / 1e7 + 0.3 for i in range(200)]
    stalls = [x - y for x, y in zip(a, clean) if x - y > 1e-9]
    assert len(stalls) == la and all(s == pytest.approx(0.6) for s in stalls)


def test_no_loss_never_stalls():
    d = links.Direction(1e6, 0.04, loss=0.0)
    for i in range(100):
        d.schedule(1448, now=float(i))
    assert d.losses == 0


def test_link_profiles_match_the_brief():
    L = links.LINKS
    assert (L["broadband"].down_bps, L["broadband"].rtt_ms) == (50e6, 20)
    assert (L["starlink"].down_bps, L["starlink"].up_bps, L["starlink"].rtt_ms, L["starlink"].loss) == (100e6, 15e6, 40, 0.005)
    assert (L["geo"].down_bps, L["geo"].rtt_ms, L["geo"].loss) == (10e6, 600, 0.01)
    assert (L["austere"].down_bps, L["austere"].rtt_ms) == (40e3, 700)       # 5 KB/s


def test_pick_links_and_modes_refuse_unknown_names():
    assert [l.name for l in links.pick_links("geo, austere")] == ["geo", "austere"]
    assert len(links.pick_links(None)) == 4
    with pytest.raises(ValueError):
        links.pick_links("moon")
    assert links.mode_prefs("normal") == {}
    # the real switch, not a copy; JavaScript on so the bench page can report its timings
    assert links.mode_prefs("slow") == {"gorilla.linkmode": 2, "gorilla.linkmode.no_javascript": False}
    assert links.mode_prefs("slow-emulated")["gfx.downloadable_fonts.enabled"] is False
    assert links.mode_prefs("slow-emulated")["media.autoplay.default"] == 5  # includes satellite
    assert links.mode_prefs("upstream-memcache") == {"browser.cache.memory.capacity": -1}
    with pytest.raises(ValueError):
        links.mode_prefs("turbo")


def test_transfer_size_is_clamped():
    assert links.transfer_size(40e3) == 40_000
    assert links.transfer_size(1e3) == 32 * 1024
    assert links.transfer_size(10e9) == 64 * 1024 * 1024


# --- fixtures ----------------------------------------------------------------------------------------------------
def test_fixtures_are_the_same_every_run_and_wikipedia_sized():
    a, b = fixtures.manifest(fixtures.build()), fixtures.manifest(fixtures.build())
    assert a["digest"] == b["digest"]
    art = a["files"]["/wiki/Gorilla"]
    assert 95_000 <= art["gzip_bytes"] <= 110_000                       # the 2026-09-26 capture: 103,572 bytes
    assert 350_000 <= a["article_set_bytes_compressed"] <= 600_000


def test_png_noise_is_a_valid_png():
    png = fixtures.png_noise(10, 7, 1)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (10, 7)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert len(raw) == 7 * (1 + 30)


def test_blob_chunks_are_exact_and_deterministic():
    a = b"".join(fixtures.blob_chunks(3 * 1024 * 1024 + 17, chunk=100_000))
    b = b"".join(fixtures.blob_chunks(3 * 1024 * 1024 + 17))
    assert len(a) == 3 * 1024 * 1024 + 17 and a == b


def test_every_bench_page_reports_to_the_report_host_only():
    files = fixtures.build()
    for p in ("/wiki/Gorilla", "/b2.html", "/b3.html", "/b4.html", "/b5.html"):
        html = files[p]["body"].decode()
        assert "https://report.netbench.test/" in html
        assert "http://" not in html.replace("http://www.w3.org/2000/svg", "")


# --- fail closed -------------------------------------------------------------------------------------------------
def test_summary_is_measured_only_when_every_repetition_measured():
    ok = benches.summary([1.0, 3.0, 2.0], "s", 3)
    assert ok["status"] == "MEASURED" and ok["median"] == 2.0 and ok["spread"] == 2.0
    bad = benches.summary([1.0, None, 2.0], "s", 3, ["rep 2 timed out"])
    assert bad["status"] == "UNMEASURED" and "median" not in bad and "timed out" in bad["why"]
    short = benches.summary([1.0], "s", 3)
    assert short["status"] == "UNMEASURED" and "2 of 3" in short["why"]


def test_collect_carries_the_reason():
    rows = [{"x": 1}, {"error": "the browser exited early"}, {"x": 2}]
    s = benches.collect(rows, [("x", "s")], 3)["x"]
    assert s["status"] == "UNMEASURED" and "exited early" in s["why"]
    rows = [{"x": None, "x_why": "no paint timing"}] * 2
    assert "no paint timing" in benches.collect(rows, [("x", "ms")], 2)["x"]["why"]


# --- log parsing -------------------------------------------------------------------------------------------------
KA_LOG = """
2026-10-03 10:00:00.1 - [Parent 1: Socket Thread]: D/nsHttp nsHttpConnection::StartShortLivedTCPKeepalives[0000AB] idle time[10s].
2026-10-03 10:00:00.1 - [Parent 1: Socket Thread]: D/nsSocketTransport nsSocketTransport::SetKeepaliveVals [00FF01] keepalive enabled, idle time[600s] retry interval[1s] packet count[10]
2026-10-03 10:00:00.2 - [Parent 1: Socket Thread]: D/nsSocketTransport nsSocketTransport::SetKeepaliveVals [00FF02] keepalive disabled, idle time[10s] retry interval[1s] packet count[10]
2026-10-03 10:01:12.2 - [Parent 1: Socket Thread]: D/nsHttp nsHttpConnection::StartLongLivedTCPKeepalives[0000AB] idle time[900s]
2026-10-03 10:01:12.2 - [Parent 1: Socket Thread]: D/nsSocketTransport nsSocketTransport::SetKeepaliveVals [00FF02] keepalive enabled, idle time[900s] retry interval[1s] packet count[10]
"""


def test_keepalive_values_come_from_the_http11_connection():
    r = benches.keepalive_metrics(KA_LOG)
    assert r["short_lived_idle_s"] == 10 and r["long_lived_idle_s"] == 900
    assert r["retry_interval_s"] == 1 and r["probe_count"] == 10
    assert r["keepalive_failures"] == 0 and r["idle_values_seen"] == [10, 600, 900]


def test_keepalive_without_lines_is_unmeasured():
    r = benches.keepalive_metrics("")
    assert r["short_lived_idle_s"] is None and r["short_lived_idle_s_why"]
    assert r["retry_interval_s"] is None and r["retry_interval_s_why"]


def test_h3_buffer_verdicts_fail_closed():
    log = "D/nsHttp HttpConnectionUDP::Init ...\n"
    assert benches.h3_metrics(log, 3, 1)["recv_buffer_granted"] == 1
    fail = log + "D/nsHttp HttpConnectionUDP::InitCommon SetRecvBufferSize failed 2147500037 [this=0]\n"
    r = benches.h3_metrics(fail, 3, 1)
    assert r["recv_buffer_granted"] == 0 and r["send_buffer_granted"] == 1
    none = benches.h3_metrics(log, 0, 4)
    assert none["recv_buffer_granted"] is None and "never ran" in none["error"]
    unproven = benches.h3_metrics("D/nsHttp other\n", 3, 1)
    assert unproven["recv_buffer_granted"] is None and unproven["recv_buffer_granted_why"]


def test_memory_metrics():
    mb = 2 ** 20
    samples = [(t, {1: ("parent", 300 * mb, 200 * mb + t * mb), 2: ("tab", 100 * mb, 80 * mb),
                    3: ("socket", 10 * mb, 5 * mb)}) for t in range(0, 20)]
    m = benches.memory_metrics(samples, t_article_load=2, t_begin=5, t_end=10, page_bytes=123)
    assert m["after_article_private_mb"] == pytest.approx(287)
    assert m["peak_private_mb"] == pytest.approx(295)         # 200+10 + 80 + 5 at t=10
    assert m["growth_private_mb"] == pytest.approx(5)
    assert m["peak_socket_private_mb"] == pytest.approx(5) and m["processes"] == 3
    nosock = [(t, {1: ("parent", mb, mb)}) for t in range(10)]
    m = benches.memory_metrics(nosock, 1, 2, 5, 0)
    assert m["peak_socket_private_mb"] is None and m["peak_socket_private_mb_why"]
    assert "error" in benches.memory_metrics([], 0, 0, 0, 0)


# --- report and compare ------------------------------------------------------------------------------------------
def _result(label, load, spread=0.1, unmeasured=False):
    m = ({"status": "UNMEASURED", "unit": "s", "why": "timed out"} if unmeasured else
         {"status": "MEASURED", "unit": "s", "median": load, "min": load, "max": load + spread, "spread": spread,
          "values": [load]})
    return {"schema": report.SCHEMA, "label": label, "when": "2026-10-03T12:00:00", "profile": "normal",
            "repetitions": 3, "build": {"build_id": "1", "version": "157.0", "codename": "G"},
            "links": {}, "benches": {"B1": {"title": "t", "results": {"geo": {"cold": {"load_s": m}}}}}}


def test_compare_prints_deltas_and_noise(tmp_path):
    (tmp_path / "netbench-before-1.json").write_text(json.dumps(_result("before", 10.0)), encoding="utf-8")
    (tmp_path / "netbench-after-1.json").write_text(json.dumps(_result("after", 8.0)), encoding="utf-8")
    (tmp_path / "netbench-flat-1.json").write_text(json.dumps(_result("flat", 10.05)), encoding="utf-8")
    c = report.compare("before", "after", tmp_path)
    r = c["rows"][0]
    assert r["delta"] == pytest.approx(-2.0) and r["pct"] == pytest.approx(-20.0) and r["verdict"] == "changed"
    assert report.compare("before", "flat", tmp_path)["rows"][0]["verdict"] == "within spread"
    text = "\n".join(report.compare_lines(c))
    assert "-20.0%" in text and "B1 geo / cold" in text


def test_compare_never_invents_a_delta(tmp_path):
    (tmp_path / "netbench-a-1.json").write_text(json.dumps(_result("a", 10.0)), encoding="utf-8")
    (tmp_path / "netbench-b-1.json").write_text(json.dumps(_result("b", 0, unmeasured=True)), encoding="utf-8")
    r = report.compare("a", "b", tmp_path)["rows"][0]
    assert r["delta"] is None and "UNMEASURED" in r["verdict"]
    with pytest.raises(FileNotFoundError):
        report.compare("a", "nothing", tmp_path)


def test_lines_and_yaml_show_no_number_for_unmeasured():
    res = _result("x", 0, unmeasured=True)
    text = "\n".join(report.lines(res))
    assert "UNMEASURED (timed out)" in text
    y = report.yaml_summary(res, "x.json")
    assert "UNMEASURED: timed out" in y and "NETBENCH-x-B1-geo" in y


def test_pick_benches():
    assert report.pick_benches("b5,B1") == ["B1", "B5"]
    assert report.pick_benches(None) == list(benches.BENCHES)
    with pytest.raises(ValueError):
        report.pick_benches("B9")


# --- browser side, without starting a browser --------------------------------------------------------------------
def test_role_of():
    assert browser.role_of(["firefox.exe", "-headless"]) == "parent"
    assert browser.role_of(["firefox.exe", "-contentproc", "-isForBrowser", "12", "tab"]) == "tab"
    assert browser.role_of(["firefox.exe", "-contentproc", "socket"]) == "socket"


def test_pref_values_parser():
    text = ('pref("network.buffer.cache.size", 524288);\npref("a.b", "x", locked);\n'
            'pref("network.http.http3.enable", true);\n// pref("commented.out", 1);\n')
    got = browser._pref_values(text, {"network.buffer.cache.size", "a.b", "network.http.http3.enable", "commented.out"})
    assert got == {"network.buffer.cache.size": 524288, "a.b": "x", "network.http.http3.enable": True}


def test_build_info_reads_application_ini(tmp_path):
    (tmp_path / "application.ini").write_text("[App]\nVersion=157.0\nBuildID=20261003112601\nCodeName=Gorilla\n",
                                              encoding="utf-8")
    assert browser.build_info(tmp_path)["build_id"] == "20261003112601"
    with pytest.raises(FileNotFoundError):
        browser.build_info(tmp_path / "nope")


def test_wait_not_running_waits_then_gives_up(monkeypatch, tmp_path):
    calls = iter([[42], [42], []])
    monkeypatch.setattr(browser, "running_from", lambda d: next(calls))
    slept = []
    assert browser.wait_not_running(tmp_path, max_wait=100, poll=10, say=lambda s: None, sleep=slept.append)
    assert slept == [10, 10]
    monkeypatch.setattr(browser, "running_from", lambda d: [7])
    assert not browser.wait_not_running(tmp_path, max_wait=20, poll=10, say=lambda s: None, sleep=lambda s: None)


def test_copy_install_trusts_the_ca_only_in_the_copy(tmp_path):
    inst = tmp_path / "install"
    (inst / "distribution").mkdir(parents=True)
    pol = {"policies": {"Preferences": {"app.normandy.enabled": {"Value": False}}}}
    (inst / "distribution" / "policies.json").write_text(json.dumps(pol), encoding="utf-8")
    (inst / "firefox.exe").write_bytes(b"MZ")
    ca = tmp_path / "ca.pem"
    ca.write_text("PEM", encoding="utf-8")
    root, copy = browser.copy_install(inst, ca, say=lambda s: None)
    try:
        got = json.loads((copy / "distribution" / "policies.json").read_text(encoding="utf-8"))
        assert got["policies"]["Preferences"] == pol["policies"]["Preferences"]
        assert got["policies"]["Certificates"]["Install"] == [str(root / "netbench-ca.pem")]
        assert json.loads((inst / "distribution" / "policies.json").read_text(encoding="utf-8")) == pol   # untouched
    finally:
        assert browser.discard(root)
    assert not root.exists()


def test_discard_refuses_a_folder_it_did_not_make(tmp_path):
    (tmp_path / "keep.txt").write_text("x", encoding="utf-8")
    assert not browser.discard(tmp_path)
    assert (tmp_path / "keep.txt").exists()


def test_user_js_quotes_values():
    js = browser.user_js({"a": "x\"y", "b": True, "c": 5})
    assert 'user_pref("a", "x\\"y");' in js and 'user_pref("b", true);' in js and 'user_pref("c", 5);' in js


def test_harness_prefs_keep_everything_on_the_machine():
    p = browser.HARNESS_PREFS
    assert p["network.proxy.type"] == 1 and p["network.proxy.failover_direct"] is False
    assert p["network.dns.forceResolve"] == "127.0.0.1" and p["network.proxy.http"] == "127.0.0.1"


def test_netbench_is_wired_into_the_cli():
    from fieldkit.cli import build_parser
    a = build_parser().parse_args(["build-harness", "netbench", "compare", "x", "y"])
    assert a.action == "netbench" and a.args == ["compare", "x", "y"]
    a = build_parser().parse_args(["build-harness", "netbench", "--bench", "B1,B2", "--profile", "satellite",
                                   "--label", "before", "--links", "geo", "--repeat", "2", "--install-dir", "D"])
    assert (a.bench, a.profile, a.label, a.links, a.repeat, a.install_dir) == ("B1,B2", "satellite", "before", "geo", 2, "D")


def test_compare_command_runs(tmp_path, capsys):
    from fieldkit.cli import main
    (tmp_path / "netbench-a-1.json").write_text(json.dumps(_result("a", 10.0)), encoding="utf-8")
    (tmp_path / "netbench-b-1.json").write_text(json.dumps(_result("b", 12.0)), encoding="utf-8")
    assert main(["build-harness", "netbench", "compare", "a", "b", "--out", str(tmp_path)]) == 0
    assert "+20.0%" in capsys.readouterr().out
