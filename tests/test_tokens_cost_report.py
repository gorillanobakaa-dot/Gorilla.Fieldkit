"""I1: cost report - both machines per local day and per source, Antigravity's Gemini priced from the dated table,
the other machine's days from files put over the cable, and the real bill beside the API-equivalent.
Made-up sessions; the process zone is pinned to UTC so a call cannot move across a local midnight."""
import json
import os
import time

import pytest

from fieldkit.tokens import cost_daily as cd
from fieldkit.tokens import cost_report as cr
from fieldkit.tokens import session_cost as sc

MODEL = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs time.tzset to pin the local zone")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    monkeypatch.setattr(cd, "_machine", lambda: "[VAIO-LNX]")
    yield
    monkeypatch.undo()
    time.tzset()


def _reply(mid, ts, read):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": MODEL, "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": read, "output_tokens": 100}}}


def _claude(tmp_path):
    """One session: a call on 10-26 (outside), one on 10-27, one on 10-28."""
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    rows = [{"type": "user", "timestamp": "2026-10-26T09:00:00Z", "message": {"content": "job"}},
            _reply("a1", "2026-10-26T09:00:05Z", 1_000_000),
            _reply("a2", "2026-10-27T10:00:00Z", 1_000_000),
            _reply("a3", "2026-10-28T10:00:00Z", 2_000_000)]
    (root / "p" / "aaaa1111.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return sc.ClaudeCode(root)


class FakeAntigravity:
    """Antigravity's shape as session_cost reads it: one Gemini call on 10-27 through the Gemini API table."""
    name = "antigravity"

    def __init__(self, tmp_path):
        self.db = tmp_path / "cascade.db"
        self.db.write_bytes(b"x")

    def sessions(self):
        return [self.db]

    def title(self, path):
        return ""

    def read(self, path, state):
        return [sc._call("antigravity", "cascade", "2026-10-27T15:00:00Z", "gemini-4-argon-high", "gemini-api",
                         inp=1_000_000, out=100_000)]


def _window():
    return sc.when("2026-10-27"), sc.when("2026-10-29")


def test_report_splits_days_and_sources_and_prices_antigravity_gemini(tmp_path):
    mine = cr.measure(*_window(), adapters=[_claude(tmp_path), FakeAntigravity(tmp_path)])
    assert list(mine["days"]) == ["2026-10-27", "2026-10-28"]
    d27, d28 = mine["days"]["2026-10-27"], mine["days"]["2026-10-28"]
    # gemini-4-argon until 2026-11-30: $2 in, $10 out per million -> 1M in + 0.1M out = $3.00
    assert d27["by_source"]["antigravity"] == 3.0
    # opus 5.5: 10 in, 1M read at $0.20, 100 out -> $0.20204
    assert d27["by_source"]["claude-code"] == 0.2 and d28["by_source"]["claude-code"] == 0.4
    assert "antigravity" not in d28["by_source"]
    assert d27["sessions"] == 2 and d28["sessions"] == 1
    assert mine["usd"] == round(d27["usd"] + d28["usd"], 2) and mine["by_source"]["antigravity"] == 3.0
    assert mine["prices_fetched"]["gemini-api"] == "2026-10-21"


def test_report_gemini_price_follows_the_date_table(tmp_path):
    ag = FakeAntigravity(tmp_path)
    ag.read = lambda path, state: [sc._call("antigravity", "c", "2026-12-02T10:00:00Z", "gemini-4-argon",
                                            "gemini-api", inp=1_000_000)]
    t = sc.when("2026-12-02T12:00:00").timestamp()
    os.utime(ag.db, (t, t))                     # written inside the window, as a real session would be
    mine = cr.measure(sc.when("2026-12-02"), sc.when("2026-12-03"), adapters=[ag])
    assert mine["usd"] == 4.0                    # the from-2026-12-01 price, not the old $2


def _peer(tmp_path, name, d):
    f = tmp_path / name
    f.write_text(json.dumps(d), encoding="utf-8")
    return f


def test_peer_days_from_report_and_daily_files_newest_first_other_machine_only(tmp_path):
    files = [_peer(tmp_path, "cost-daily-new.json", {"date": "2026-10-28", "machine": "[L15-WIN]", "usd": 5.0}),
             _peer(tmp_path, "cost-report-old.json",
                   {"kind": "cost-report", "machine": "[L15-WIN]",
                    "days": {"2026-10-27": {"usd": 10.0}, "2026-10-28": {"usd": 1.0}}}),
             _peer(tmp_path, "cost-daily-self.json", {"date": "2026-10-27", "machine": "[VAIO-LNX]", "usd": 99.0}),
             _peer(tmp_path, "cost-daily-out.json", {"date": "2026-10-20", "machine": "[L15-WIN]", "usd": 7.0})]
    machine, got = cr.peer_numbers({"2026-10-27", "2026-10-28"}, "[VAIO-LNX]", files)
    assert machine == "[L15-WIN]"
    assert got["2026-10-28"][0] == 5.0 and got["2026-10-27"][0] == 10.0      # newest file wins per day
    assert set(got) == {"2026-10-27", "2026-10-28"}


def test_combine_names_missing_days_and_never_guesses_them(tmp_path):
    mine = cr.measure(*_window(), adapters=[_claude(tmp_path)])
    r = cr.combine(mine, "[L15-WIN]", {"2026-10-27": (10.0, "f")}, bill=None)
    assert r["days"]["2026-10-27"]["both"] == round(mine["days"]["2026-10-27"]["usd"] + 10, 2)
    assert r["days"]["2026-10-28"]["peer"] is None and r["days"]["2026-10-28"]["both"] is None
    assert r["peer_days_missing"] == ["2026-10-28"] and r["usd_peer"] == 10.0
    assert "not over the cable" in cr.text(r)


def test_bill_beside_api_equivalent_with_the_gap(tmp_path):
    mine = cr.measure(*_window(), adapters=[_claude(tmp_path), FakeAntigravity(tmp_path)])
    peer = {"2026-10-27": (100.0, "f"), "2026-10-28": (96.4, "f")}
    r = cr.combine(mine, "[L15-WIN]", peer, bill=300.0)
    both = r["usd_both"]
    assert both == round(mine["usd"] + 196.4, 2)
    assert r["bill"] == {"usd": 300.0, "api_equivalent": both, "gap": round(300 - both, 2),
                         "seen_share": round(both / 300, 4), "complete": True}
    assert "gap $%.2f" % (300 - both) in cr.text(r)
    # a day missing from the other machine: the bill comparison says it is incomplete
    r = cr.combine(mine, "[L15-WIN]", {"2026-10-27": (100.0, "f")}, bill=300.0)
    assert r["bill"]["complete"] is False and "INCOMPLETE" in cr.text(r)


def test_cli_json_only_mine_file_round_trips_as_peer_input(tmp_path, monkeypatch, capsys):
    claude = _claude(tmp_path)
    monkeypatch.setattr(sc, "adapters_for", lambda source: [claude])
    assert cd.main(["report", "--since", "2026-10-27", "--until", "2026-10-29", "--mine", "--json"]) == 0
    out = capsys.readouterr().out
    assert len(out.splitlines()) == 1
    mine_file = _peer(tmp_path, "cost-report-x.json", dict(json.loads(out), machine="[L15-WIN]"))
    assert cd.main(["report", "--since", "2026-10-27", "--until", "2026-10-29", "--peer", str(mine_file),
                    "--bill", "50", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["peer_machine"] == "[L15-WIN]" and r["usd_both"] == round(2 * r["usd_mine"], 2)
    assert r["bill"]["usd"] == 50.0 and not r["peer_days_missing"]
    assert cd.main(["report", "--since", "2026-10-27", "--peer", os.fspath(tmp_path / "missing.json")]) == 2
    with pytest.raises(SystemExit) as e:
        cd.main(["report", "--since", "2026-10-28", "--until", "2026-10-27"])
    assert e.value.code == 2
