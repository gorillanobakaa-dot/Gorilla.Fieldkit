"""User-action scenarios (2026-10-02): leaks that fire only when a page or the user does something. All fakes: the
gate, the browser and the captures never run here; the only server is the scenarios' own local test server."""
import json
import urllib.request

from fieldkit.leakgate import allow as al, gate, scenarios as sc, sensors as se

NEW = ("certerror-toplevel", "download-exe", "lan-probe", "drm-request", "h264-call", "profile-idle-actions")


def test_every_new_scenario_is_in_the_release_set_with_the_common_shape():
    names = [s[0] for s in sc.SCENARIOS]
    assert len(names) == len(set(names))
    for n in NEW:
        assert n in names and n in sc.ACTION_SCENARIOS
    for s in sc.SCENARIOS:
        assert len(s) == 6
    for s in sc.SCENARIOS:
        if s[0] in NEW:
            assert s[4] == []                              # no host is pre-allowed by the test definition
    assert set(sc.REPORTING) <= set(sc.ACTION_SCENARIOS) and set(sc.VIDEO_COMPROMISE) <= set(sc.ACTION_SCENARIOS)


def test_policies_gain_lan_policy_and_stay_unique():
    assert "LAN_POLICY" in gate.POLICIES
    assert len(gate.POLICIES) == len(set(gate.POLICIES)) == 19


def test_targets_and_prefs_are_filled_in():
    ports = {"valid": 1, "expired": 2, "wronghost": 3, "selfsigned": 4, "unknownissuer": 5}
    assert sc.target_for("certerror-toplevel", "/certerror", ports, 9) == "/certerror?port=5"
    assert sc.target_for("lan-probe", "/lan", ports, 9) == "/lan?closed=9"
    assert sc.target_for("certs", "/certs", ports, 9) == "/certs?valid=1&expired=2&wronghost=3&selfsigned=4"
    assert sc.target_for("drm-request", "/drm", ports, 9) == "/drm"
    assert sc.prefs_for("lan-probe", "127.0.0.1", 4321) == {"network.lna.address_space.public.override": "127.0.0.1:4321"}
    assert sc.prefs_for("h264-call", "h", 1)["media.peerconnection.ice.loopback"] is True
    assert sc.prefs_for("startup-idle", "h", 1) == {}


def test_new_profile_writes_scenario_prefs(tmp_path):
    prof = se.new_profile(tmp_path, "x", {"network.lna.address_space.public.override": "127.0.0.1:5", "a.b": True, "c": 3})
    text = (prof / "user.js").read_text(encoding="utf-8")
    assert 'user_pref("network.lna.address_space.public.override", "127.0.0.1:5");' in text
    assert 'user_pref("a.b", true);' in text and 'user_pref("c", 3);' in text
    assert 'browser.aboutwelcome.enabled' in text                         # the common prefs are still there


def test_the_local_server_serves_every_new_page_and_the_exe():
    srv = sc.Server()
    try:
        for path, marker in (("/certerror?port=1", "certerror-toplevel"), ("/download-exe", "tiny.exe"), ("/lan?closed=2", "192.168.0.1"),
                             ("/drm", "com.widevine.alpha"), ("/h264", "setCodecPreferences")):
            body = urllib.request.urlopen(srv.url(path), timeout=10).read().decode("utf-8")
            assert marker in body, path
        r = urllib.request.urlopen(srv.url("/tiny.exe"), timeout=10)
        assert r.read()[:2] == b"MZ" and "attachment" in r.headers["Content-Disposition"]
        assert r.headers["Content-Type"] == "application/x-msdownload"
        assert any(q["path"] == "/tiny.exe" for q in srv.requests)
        req = urllib.request.Request(srv.url("/result"), data=json.dumps({"scenario": "lan-probe"}).encode(), method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        assert {"scenario": "lan-probe"} in srv.results
    finally:
        srv.close()


def test_h264_page_keeps_percent_signs_after_formatting():
    assert "% 360" in sc.H264_PAGE and "80%" in sc.H264_PAGE and "%s" not in sc.H264_PAGE
    assert "const report" in sc.LAN_PAGE and "const report" in sc.DRM_PAGE


# ---------------------------------------------------------------------------------------------- action_checks
def _cov(scen, mode, sensors):
    return [{"scenario": scen, "mode": mode, "sensor": s, "kind": "coverage", "value": "ran"} for s in sensors]


def _full(scen, packets=False):
    return (_cov(scen, "direct", gate.required_coverage("direct", packets))
            + _cov(scen, "proxied", gate.required_coverage("proxied", packets)))


def _ok_world(packets=False):
    events = [e for s in NEW for e in _full(s, packets)]
    results = [{"scenario": s} for s in sc.REPORTING]
    results[[r["scenario"] for r in results].index("lan-probe")].update(closed="5555", targets={
        "loopback_closed": {"url": "http://127.0.0.1:5555/", "result": "failed: TypeError"}})
    requests = [{"method": "GET", "path": "/tiny.exe"}]
    return events, results, requests


def test_action_checks_pass_when_every_sensor_ran_and_every_page_reported():
    events, results, requests = _ok_world()
    fail, lists = gate.action_checks(set(NEW), events, results, requests, packets=False)
    assert fail == {"NETWORK_POLICY": [], "LAN_POLICY": []} and lists == {"LAN_REQUESTS_LEFT": []}


def test_a_sensor_that_collected_nothing_in_a_scenario_fails():
    events, results, requests = _ok_world()
    events = [e for e in events if not (e["scenario"] == "drm-request" and e["mode"] == "proxied" and e["sensor"] == "mitm")]
    fail, _ = gate.action_checks(set(NEW), events, results, requests, packets=False)
    assert any("[drm-request/proxied]" in x and "mitm" in x for x in fail["NETWORK_POLICY"])
    # packets on: pktmon is required in direct mode too
    events, results, requests = _ok_world(packets=False)
    fail, _ = gate.action_checks(set(NEW), events, results, requests, packets=True)
    assert any("pktmon" in x for x in fail["NETWORK_POLICY"])


def test_a_scenario_that_ran_with_no_events_at_all_fails():
    events, results, requests = _ok_world()
    events = [e for e in events if e["scenario"] != "profile-idle-actions"]
    fail, _ = gate.action_checks(set(NEW), events, results, requests, packets=False)
    assert any("[profile-idle-actions] no sensor collected" in x for x in fail["NETWORK_POLICY"])
    # a scenario that was not selected (--only) is not judged
    fail, _ = gate.action_checks(set(NEW) - {"profile-idle-actions"}, events, results, requests, packets=False)
    assert fail["NETWORK_POLICY"] == []


def test_a_page_that_never_reported_or_a_download_never_served_fails():
    events, results, requests = _ok_world()
    results = [r for r in results if r["scenario"] != "certerror-toplevel"]
    fail, _ = gate.action_checks(set(NEW), events, results, [], packets=False)
    assert any("[certerror-toplevel] the page never reported" in x for x in fail["NETWORK_POLICY"])
    assert any("never served /tiny.exe" in x for x in fail["NETWORK_POLICY"])


def test_linux_coverage_names_its_own_sensors():
    assert gate.required_coverage("direct", True, linux=True) == {"strace", "tcpdump"}


def test_lan_probe_requests_that_left_fail_lan_policy():
    events, results, requests = _ok_world()
    lan = [{"scenario": "lan-probe", "mode": "direct", "sensor": "sockets", "kind": "dest-ip", "value": "192.168.0.1", "port": 80},
           {"scenario": "lan-probe", "mode": "direct", "sensor": "necko-socket", "kind": "dest-lan", "value": "127.0.0.1", "port": 5555},
           {"scenario": "lan-probe", "mode": "direct", "sensor": "necko-socket", "kind": "dest-lan", "value": "127.0.0.1", "port": 8000},
           {"scenario": "webrtc", "mode": "direct", "sensor": "sockets", "kind": "dest-ip", "value": "10.0.0.1", "port": 80}]
    fail, lists = gate.action_checks(set(NEW), events + lan, results, requests, packets=False)
    assert any("192.168.0.1:80" in x for x in lists["LAN_REQUESTS_LEFT"])
    assert any("127.0.0.1:5555" in x for x in lists["LAN_REQUESTS_LEFT"])        # the closed port the page probed
    assert not any(":8000" in x for x in lists["LAN_REQUESTS_LEFT"])            # the local server itself
    assert not any("10.0.0.1" in x for x in lists["LAN_REQUESTS_LEFT"])         # judged only in lan-probe
    assert len(fail["LAN_POLICY"]) == 2


def test_lan_probe_fails_closed_without_a_report_or_a_socket_sensor():
    events, results, requests = _ok_world()
    no_report = [r for r in results if r["scenario"] != "lan-probe"]
    fail, _ = gate.action_checks(set(NEW), events, no_report, requests, packets=False)
    assert "the LAN probe page never reported" in fail["LAN_POLICY"]
    no_sockets = [e for e in events if not (e["scenario"] == "lan-probe" and e["sensor"] == "sockets")]
    fail, _ = gate.action_checks(set(NEW), no_sockets, results, requests, packets=False)
    assert any("whether the requests left is unknown" in x for x in fail["LAN_POLICY"])


def test_lan_probe_page_that_reached_a_private_address_fails():
    events, results, requests = _ok_world()
    for r in results:
        if r["scenario"] == "lan-probe":
            r["targets"]["private_10"] = {"url": "http://10.0.0.1/", "result": "reached"}
    fail, _ = gate.action_checks(set(NEW), events, results, requests, packets=False)
    assert any("http://10.0.0.1/" in x for x in fail["LAN_POLICY"])


def test_lan_address():
    assert se.lan_address("192.168.0.1") and se.lan_address("10.0.0.1") and se.lan_address("127.0.0.1")
    assert se.lan_address("169.254.1.1") and se.lan_address("fe80::1") and se.lan_address("[::1]")
    assert not se.lan_address("8.8.8.8") and not se.lan_address("example.com") and not se.lan_address("224.0.0.251")
    assert not se.lan_address("0.0.0.0")


def test_coverage_events_only_for_sensors_that_ran(tmp_path):
    base = {"scenario": "s", "mode": "direct"}
    pcap = tmp_path / "p.pcapng"
    assert se.coverage_events(base, 0, "", False, None, "direct", False) == []
    pcap.write_bytes(b"x")
    names = {e["sensor"] for e in se.coverage_events(base, 3, "log", False, pcap, "direct", True)}
    assert names == {"sockets", "necko-http", "necko-dns", "pktmon"}
    names = {e["sensor"] for e in se.coverage_events({**base, "mode": "proxied"}, 3, "", True, None, "proxied", False)}
    assert names == {"sockets", "mitm"}


def test_coverage_events_do_not_satisfy_the_global_sensor_check():
    ev = [{"scenario": "startup-idle", "mode": "direct", "sensor": s, "kind": "coverage", "value": "ran"}
          for s in ("necko-http", "necko-dns", "mitm", "sockets", "process-tree", "filesystem")]
    fail, _ = gate.judge(ev, {"entries": []}, {}, ["C:/b"], [], 3, False, False)
    assert any("required sensor(s) did not collect" in x for x in fail["NETWORK_POLICY"])


# ---------------------------------------------------------------------------------------------- the video compromise
def _entry(**kw):
    e = {f: "x" for f in al.FIELDS}
    e.update({"id": "e1", "kind": "dest", "values": ["x"], "scenarios": "*", "approval": {"by": "owner"}})
    e.update(kw)
    return e


def _ev(scen, value, kind="dest", sensor="necko-http"):
    return {"scenario": scen, "mode": "direct", "sensor": sensor, "kind": kind, "value": value, "port": 443}


def test_widevine_allowed_only_by_a_scoped_approved_entry_and_only_in_its_scenario():
    allow = {"entries": [_entry(id="wv", values=["*.gvt1.com"], scenarios=["drm-request"])]}
    fail, lists = gate.judge([_ev("drm-request", "edgedl.me.gvt1.com")], allow, {}, ["C:/b"], [], 3, True, False)
    assert not any("gvt1" in x for x in fail["NETWORK_POLICY"])
    # the same host anywhere else fails, even with an approved catch-all entry
    allow_all = {"entries": [_entry(id="wv", values=["*.gvt1.com"], scenarios="*")]}
    fail, _ = gate.judge([_ev("startup-idle", "edgedl.me.gvt1.com")], allow_all, {}, ["C:/b"], [], 3, True, False)
    assert any("outside its scenario drm-request" in x for x in fail["NETWORK_POLICY"])
    # Cisco's host in the DRM scenario is not Widevine's: outside its own scenario h264-call
    fail, _ = gate.judge([_ev("drm-request", "ciscobinary.openh264.org")], allow_all, {}, ["C:/b"], [], 3, True, False)
    assert any("outside its scenario h264-call" in x for x in fail["NETWORK_POLICY"])


def test_first_run_of_a_video_scenario_fails_until_the_maintainer_approves():
    fail, lists = gate.judge([_ev("h264-call", "ciscobinary.openh264.org")], {"entries": []}, {}, ["C:/b"], [], 3, True, False)
    assert any("ciscobinary" in x and "unexpected" in x for x in lists["UNEXPECTED_DESTINATIONS"])


def test_a_mozilla_host_in_a_video_scenario_fails_even_when_allowlisted():
    allow = {"entries": [_entry(id="moz", values=["aus5.mozilla.org", "*.mozilla.net"], scenarios="*")]}
    for scen in ("drm-request", "h264-call"):
        for host, kind in (("aus5.mozilla.org", "dest"), ("redirector.cdn.mozilla.net", "sni"), ("aus5.mozilla.org", "dns")):
            fail, _ = gate.judge([_ev(scen, host, kind)], allow, {}, ["C:/b"], [], 3, True, False)
            pol = "DNS_POLICY" if kind == "dns" else "NETWORK_POLICY"
            assert any("never goes through Mozilla" in x for x in fail[pol]), (scen, host, kind)


def test_allowlist_scope_problems():
    good = {"entries": [_entry(id="wv", values=["*.gvt1.com", "dl.google.com"], scenarios=["drm-request"]),
                        _entry(id="ub", values=["cdn.jsdelivr.net"])]}
    assert al.scope_problems(good) == []
    bad = {"entries": [_entry(id="wv", values=["*.gvt1.com"], scenarios="*"),
                       _entry(id="c", values=["ciscobinary.openh264.org"], scenarios=["h264-call", "startup-idle"]),
                       _entry(id="m", values=["aus5.mozilla.org"], scenarios=["drm-request"])]}
    probs = al.scope_problems(bad)
    assert any(p.startswith("wv:") for p in probs) and any(p.startswith("c:") for p in probs)
    assert any(p.startswith("m:") and "Mozilla" in p for p in probs)


def test_propose_offers_only_scoped_unapproved_video_entries(tmp_path, monkeypatch):
    p = tmp_path / "allow.json"
    al.save(p, {"version": 1, "entries": []})
    events = [_ev("drm-request", "edgedl.me.gvt1.com"), _ev("drm-request", "dl.google.com", "sni", "pktmon"),
              _ev("drm-request", "edgedl.me.gvt1.com", "dns", "necko-dns"),
              _ev("drm-request", "aus5.mozilla.org"),                     # Mozilla: never proposed
              _ev("startup-idle", "dl.google.com"),                        # outside its scenario: never proposed
              _ev("drm-request", "tracker.example"),                       # not a maker host: never proposed
              _ev("h264-call", "ciscobinary.openh264.org")]
    added = al.propose(p, events, "run1", say=lambda m: None)
    entries = {e["id"]: e for e in al.load(p)["entries"]}
    assert sorted(added) == ["video-drm-request-dest-run1", "video-drm-request-dns-run1", "video-h264-call-dest-run1"]
    assert entries["video-drm-request-dest-run1"]["values"] == ["dl.google.com", "edgedl.me.gvt1.com"]
    assert entries["video-drm-request-dest-run1"]["scenarios"] == ["drm-request"]
    assert entries["video-h264-call-dest-run1"]["values"] == ["ciscobinary.openh264.org"]
    assert all(e["approval"] is None for e in entries.values())
    assert al.problems(al.load(p)) == [] and al.scope_problems(al.load(p)) == []
    # a second propose adds nothing: the proposals already cover the hosts (pending, still failing the gate)
    assert al.propose(p, events, "run2", say=lambda m: None) == []
    fail, lists = gate.judge([_ev("drm-request", "dl.google.com")], al.load(p), {}, ["C:/b"], [], 3, True, False)
    assert lists["PENDING_APPROVAL"] == ["video-drm-request-dest-run1"]
