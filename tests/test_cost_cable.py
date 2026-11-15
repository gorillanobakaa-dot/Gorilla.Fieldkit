"""Row 20: the other machine's spend comes over the cable by itself (the link's read-only verb `cost`), is used when
fresher than a day with the peer's own date shown, and is named "not counted" when there is no cable. Fake link."""
import json
from datetime import date, datetime

from fieldkit.peers import link
from fieldkit.tokens import cost_daily as cd
from fieldkit.tokens import cost_report as cr

OKV = type("V", (), {"check": lambda self, req: None})()
NOW = datetime(2026, 11, 2, 12, 0).timestamp()
DAY = date(2026, 10, 27)


def _peer_day(usd=7.5):
    return {"date": DAY.isoformat(), "machine": "[L15-WIN]", "usd": usd, "sessions": 2}


def _link(data, at=NOW - 60, ok=True):
    seen = []

    def call(req, timeout=15, device=None):
        seen.append(req)
        return {"ok": True, "measured_at": at, "data": data} if ok else {"ok": False, "error": "link down"}
    call.seen = seen
    return call


def test_the_cost_verb_answers_with_this_machines_numbers(monkeypatch):
    monkeypatch.setattr(cd, "measure_day", lambda d: {"date": d.isoformat(), "machine": "[VAIO-LNX]", "usd": 3.0})
    r = link.handle({"verb": "cost", "kind": "daily", "date": "2026-10-27"}, OKV, via="cable")
    assert r["ok"] and r["data"]["usd"] == 3.0 and isinstance(r["measured_at"], float)
    assert not link.handle({"verb": "cost", "kind": "nope"}, OKV, via="cable")["ok"]


def test_cable_answer_is_used_kept_and_dated(tmp_path):
    call = _link(_peer_day())
    f, at = cd.from_cable({"kind": "daily", "date": DAY.isoformat()}, call=call, now=NOW, cache_dir=tmp_path)
    assert call.seen[0]["verb"] == "cost" and at == NOW - 60
    d, where = cd.peer_numbers(DAY, "[VAIO-LNX]", [f])
    assert d["usd"] == 7.5 and where == f


def test_no_cable_uses_the_kept_copy_only_while_fresher_than_a_day(tmp_path):
    req = {"kind": "daily", "date": DAY.isoformat()}
    cd.from_cable(req, call=_link(_peer_day()), now=NOW, cache_dir=tmp_path)
    down = _link(None, ok=False)
    f, at = cd.from_cable(req, call=down, now=NOW + 3600, cache_dir=tmp_path)
    assert f and at == NOW - 60                                        # the peer's date, not now
    assert cd.from_cable(req, call=down, now=NOW + 86400, cache_dir=tmp_path) == (None, None)


def test_a_stale_answer_is_not_counted(tmp_path):
    f, at = cd.from_cable({"kind": "daily", "date": DAY.isoformat()}, call=_link(_peer_day(), at=NOW - 2 * 86400),
                          now=NOW, cache_dir=tmp_path)
    assert (f, at) == (None, None)


def test_daily_line_says_the_peer_date_and_when_not_counted():
    r = {"date": DAY.isoformat(), "machine": "[VAIO-LNX]", "usd": 1.0, "sessions": 1, "unpriced_calls": 0, "top": None,
         "peer": {"machine": "[L15-WIN]", "usd": 7.5, "measured_at": NOW}, "usd_both": 8.5}
    assert "the other machine measured it 2026-11-02 12:00" in cd.line(r)
    r["peer"], r["peer_name"] = None, "VAIO"
    assert "VAIO not counted" in cd.line(r)


def test_report_text_names_the_peer_date_and_not_counted():
    mine = {"since": "2026-10-27T00:00:00", "until": "2026-10-28T00:00:00", "machine": "[VAIO-LNX]", "usd": 1.0,
            "by_source": {}, "unpriced_calls": 0, "prices_fetched": {}, "days": {"2026-10-27": {"usd": 1.0, "by_source": {}}}}
    r = cr.combine(mine, "[L15-WIN]", {"2026-10-27": (7.5, "f")})
    r["peer_measured_at"], r["peer_name"] = NOW, "L15"
    assert "[L15-WIN] measured 2026-11-02 12:00" in cr.text(r)
    r = cr.combine(mine, "", {})
    r["peer_measured_at"], r["peer_name"] = None, "VAIO"
    assert "VAIO not counted: no cable" in cr.text(r)


def test_report_main_uses_the_cable_when_no_peer_file(tmp_path, monkeypatch, capsys):
    peer = {"kind": "cost-report", "machine": "[L15-WIN]", "days": {"2026-10-27": {"usd": 7.5}}}
    monkeypatch.setattr(cd, "_machine", lambda: "[VAIO-LNX]")
    monkeypatch.setattr(cr, "measure", lambda s, u, adapters=None: {
        "kind": "cost-report", "machine": "[VAIO-LNX]", "since": s.isoformat(), "until": u.isoformat(), "usd": 1.0,
        "by_source": {}, "sessions": 1, "unpriced_calls": 0, "prices_fetched": {},
        "days": {"2026-10-27": {"usd": 1.0, "by_source": {}, "sessions": 1}}})
    monkeypatch.setattr(link, "call", _link(peer, at=datetime.now().timestamp() - 5))
    monkeypatch.setattr(cd.settings, "state_root", lambda root=None: tmp_path)
    monkeypatch.setattr(cd, "peer_name", lambda: "L15")
    assert cr.main(["--since", "2026-10-27", "--until", "2026-10-28", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["usd_peer"] == 7.5 and out["usd_both"] == 8.5 and out["peer_measured_at"]
