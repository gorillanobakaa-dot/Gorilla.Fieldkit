"""The local leak page's result is judged like browserleaks would: local IPs, WebGL renderer, battery API."""
from fieldkit.buildh import leaks


def _rows(monkeypatch, result):
    monkeypatch.setattr(leaks, "measure", lambda d, seconds=45: result)
    return {r["check"].split(":")[1].strip()[:20]: r for r in leaks.rows("x")}


def test_local_ip_fails_public_srflx_is_only_reported(monkeypatch):
    base = {"navigator": {}, "screen": {}, "time": {}, "apis": {"getBattery": False}, "webgl": {"unmaskedRenderer": None, "renderer": "Mozilla"}, "canvas": {}, "fonts": []}
    r = _rows(monkeypatch, {**base, "ice": {"summary": {"host": 2, "srflx": 1, "mdns": 2, "rawIPs": ["148.252.165.143"]}}})
    assert r["WebRTC reveals no lo"]["ok"] and "public ['148.252.165.143']" in r["WebRTC reveals no lo"]["evidence"]
    r = _rows(monkeypatch, {**base, "ice": {"summary": {"host": 2, "srflx": 0, "mdns": 0, "rawIPs": ["192.168.1.7"]}}})
    assert not r["WebRTC reveals no lo"]["ok"] and r["WebRTC reveals no lo"]["bad"] == ["192.168.1.7"]
    r = _rows(monkeypatch, {**base, "ice": {"summary": {}}, "webgl": {"unmaskedRenderer": "ANGLE (Intel ...)"}, "apis": {"getBattery": True}})
    assert not r["WebGL renderer not u"]["ok"] and not r["battery API absent"]["ok"]


def test_no_result_is_a_failure(monkeypatch):
    r = leaks.rows("x") if False else None
    monkeypatch.setattr(leaks, "measure", lambda d, seconds=45: None)
    rows = leaks.rows("x")
    assert len(rows) == 1 and not rows[0]["ok"]
