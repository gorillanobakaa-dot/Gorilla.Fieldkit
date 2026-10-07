"""gate.run wiring for the user-action scenarios, with every sensor, the browser and the audits faked: the run fills
in the ports and prefs, hands them to run_one, and judges the pages' reports and the served .exe fail-closed."""
import json
import urllib.request

import pytest

from fieldkit.leakgate import audit, extras, gate, scenarios as sc, sensors as se


class _FakeCerts:
    def __init__(self, *a):
        pass

    def ports(self):
        return {"valid": 1, "expired": 2, "wronghost": 3, "selfsigned": 4, "unknownissuer": 5}

    def close(self):
        pass


class _FakeDoh:
    queries = []

    def __init__(self, *a):
        pass

    def url(self):
        return "https://127.0.0.1:1/dns-query"

    def close(self):
        pass


def _post(url, obj):
    urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST"), timeout=10).read()


@pytest.fixture
def fake_world(tmp_path, monkeypatch):
    calls = []

    def run_one(bdir, url, secs, args, workdir, name, mode, canaries, proxy_port=None, watch=10, packets=False, say=print,
                graceful=False, poison=False, prefs=None):
        scen = name.rsplit("-r", 1)[0]
        calls.append({"scenario": scen, "mode": mode, "url": url, "prefs": prefs, "args": args})
        base = url.split("/", 3)[:3]
        origin = "/".join(base) if url.startswith("http") else None
        if origin and scen in sc.REPORTING and not fake_world_silent.get(scen):
            rep = {"scenario": scen}
            if scen == "lan-probe":
                rep["closed"] = url.split("closed=")[1]
            _post(origin + "/result", rep)
        if origin and scen == "download-exe":
            urllib.request.urlopen(origin + "/tiny.exe", timeout=10).read()
        if origin and scen == "early-hints":
            # a browser loads the page (raw socket: urllib treats the 103 as the final response)
            import socket
            hostport = origin.split("//", 1)[1]
            s = socket.create_connection((hostport.split(":")[0], int(hostport.split(":")[1])), timeout=10)
            s.sendall(b"GET /early-hints HTTP/1.1\r\nHost: " + hostport.encode() + b"\r\n\r\n")
            got = b""
            while b"</body>" not in got:
                chunk = s.recv(4096)
                if not chunk:
                    break
                got += chunk
            s.close()
        ev = [{"scenario": name, "mode": mode, "sensor": s, "kind": "coverage", "value": "ran"}
              for s in gate.required_coverage(mode, packets)]
        return ev, {}

    fake_world_silent = {}
    monkeypatch.setattr(gate.sys, "platform", "win32")
    monkeypatch.setattr(se, "is_admin", lambda: False)
    monkeypatch.setattr(se, "build_copy", lambda z, d, policies=None: d)
    monkeypatch.setattr(se, "run_one", run_one)
    monkeypatch.setattr(gate, "ensure_ca", lambda work: work / "ca.pem")
    monkeypatch.setattr(gate, "build_manifest", lambda *a: {k: None for k in ("BUILD", "VERSION", "SOURCE", "BINARY_SHA256", "OS", "KERNEL",
                                                                              "ARCHITECTURE", "NETWORK_CONFIGURATION",
                                                                              "PROFILE_CONFIGURATION", "HARNESS_VERSION")})
    monkeypatch.setattr(extras, "CertServers", _FakeCerts)
    monkeypatch.setattr(extras, "DohServer", _FakeDoh)
    monkeypatch.setattr(audit, "static_audit", lambda *a: {"inventory": {}, "count": 0, "unapproved": [], "undecided": [], "new_since_previous": []})
    monkeypatch.setattr(audit, "binary_audit", lambda *a: {"unapproved_new": [], "vendor_unlisted": ["x.mozilla.org"], "vendor_pending": []})
    monkeypatch.setattr(audit, "executable_audit", lambda *a: {"unapproved_new": [], "unexpected_network_imports": {}})
    monkeypatch.setattr(audit, "dependency_audit", lambda *a: {"unapproved_new": [], "network_capable_new": []})
    z = tmp_path / "build.zip"
    z.write_bytes(b"zip")
    owner = tmp_path / "owner"
    (owner / "leakgate").mkdir(parents=True)

    def go(only, silent=()):
        fake_world_silent.clear()
        fake_world_silent.update({s: True for s in silent})
        calls.clear()
        res, events, st, bi = gate.run(z, owner, tmp_path / "tree", "157.0", tmp_path / f"work{len(only)}{len(silent)}", only=set(only),
                                       say=lambda m: None)
        return res, calls
    return go


def test_ports_and_prefs_reach_run_one_and_the_new_scenarios_pass_their_own_checks(fake_world):
    res, calls = fake_world(sc.ACTION_SCENARIOS)
    by = {(c["scenario"], c["mode"]): c for c in calls}
    assert by[("certerror-toplevel", "direct")]["url"].endswith("/certerror?port=5")
    lan = by[("lan-probe", "direct")]
    assert "/lan?closed=" in lan["url"]
    host_port = lan["url"].split("//")[1].split("/")[0]
    assert lan["prefs"] == {"network.lna.address_space.public.override": host_port}
    assert by[("h264-call", "proxied")]["prefs"]["media.peerconnection.ice.loopback"] is True
    assert by[("profile-idle-actions", "direct")]["args"] == ["-new-tab", "about:welcome"]
    assert {c["scenario"] for c in calls} == set(sc.ACTION_SCENARIOS)
    why = res["WHY"]
    assert [x for x in why.get("NETWORK_POLICY", []) if any(x.startswith("[" + s) for s in sc.ACTION_SCENARIOS)] == []
    assert res["LAN_POLICY"] == "PASS" and res["LAN_REQUESTS_LEFT"] == []
    assert any("x.mozilla.org" in x for x in why["BINARY_POLICY"])          # the vendor host inventory is wired in
    assert res["FINAL_RESULT"] == "FAIL"                                       # quick run, no packets: never a release PASS


def test_a_silent_page_fails_closed_through_the_real_run(fake_world):
    res, _ = fake_world(["lan-probe", "drm-request"], silent=["lan-probe", "drm-request"])
    assert res["LAN_POLICY"] == "FAIL" and "the LAN probe page never reported" in res["WHY"]["LAN_POLICY"]
    assert any("[drm-request] the page never reported" in x for x in res["WHY"]["NETWORK_POLICY"])
