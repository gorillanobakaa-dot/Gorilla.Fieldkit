"""I2 + I4: token-audit / session-cost --since with JSON-only stdout, and the one daily cost line (cost daily).
Made-up Claude Code transcripts across two days; times are UTC with explicit zones so the local zone cannot move a
call across the window."""
import json
import os
from datetime import date

from fieldkit.tokens import cost_daily as cd
from fieldkit.tokens import session_cost as sc
from fieldkit.tokens import token_audit as ta

MODEL = "claude-opus-5-5"


def _reply(mid, ts, read):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "model": MODEL, "content": [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": read, "output_tokens": 100}}}


def _projects(tmp_path):
    """Two sessions: 'aaaa1111' with one call on 10-26 and two on 10-27; 'bbbb2222' with one small call on 10-27."""
    root = tmp_path / "projects"
    (root / "p").mkdir(parents=True)
    a = [{"type": "user", "timestamp": "2026-10-26T09:00:00Z",
          "message": {"content": "<system-reminder>injected</system-reminder>\n# [VAIO-LNX] heading\n"
                                 "Background:\nFix the daily cost line\nmore"}},
         _reply("a1", "2026-10-26T09:00:05Z", 1_000_000),
         _reply("a2", "2026-10-27T10:00:00Z", 2_000_000),
         _reply("a3", "2026-10-27T11:00:00Z", 2_000_000)]
    b = [{"type": "user", "timestamp": "2026-10-27T12:00:00Z", "message": {"content": "small job"}},
         _reply("b1", "2026-10-27T12:00:01Z", 10_000)]
    for name, rows in (("aaaa1111", a), ("bbbb2222", b)):
        (root / "p" / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return root


SINCE, UNTIL = "2026-10-27T00:00:00+00:00", "2026-10-28T00:00:00+00:00"


def _json_lines(out):
    """Every stdout line must be a JSON object: a stray line of text (a guard, a warning) fails the test."""
    lines = [ln for ln in out.splitlines() if ln.strip()]
    rows = [json.loads(ln) for ln in lines]
    assert all(isinstance(r, dict) for r in rows)
    return rows


# ---------------------------------------------------------------- I2 token-audit

def test_token_audit_since_counts_only_calls_in_the_window(tmp_path):
    f = _projects(tmp_path) / "p" / "aaaa1111.jsonl"
    r = ta.audit(f, ta.when(SINCE), ta.when(UNTIL))
    assert r["api_calls"] == 2 and r["cache_read"] == 4_000_000
    assert ta.audit(f)["api_calls"] == 3


def test_token_audit_since_json_is_nothing_but_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ta, "PROJECTS", _projects(tmp_path))
    assert ta.main(["--all", "--since", SINCE, "--until", UNTIL, "--json"]) == 0
    rows = _json_lines(capsys.readouterr().out)
    assert sorted(r["session"] for r in rows) == ["aaaa1111", "bbbb2222"]   # small files are not skipped
    assert all(r["since"] == SINCE for r in rows)


def test_token_audit_doctor_json_is_json(capsys):
    ta.main(["--doctor", "--json"])
    assert _json_lines(capsys.readouterr().out)[0]["version"] == ta.VERSION


def test_token_audit_since_with_nothing_in_window_is_empty_not_an_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ta, "PROJECTS", _projects(tmp_path))
    assert ta.main(["--all", "--since", "2099-01-01", "--json"]) == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- I2 session-cost

def test_session_cost_since_lists_sessions_and_prices_only_window_calls(tmp_path, monkeypatch, capsys):
    root = _projects(tmp_path)
    monkeypatch.setattr(sc, "adapters_for", lambda source: [sc.ClaudeCode(root)])
    assert sc.main(["--since", SINCE, "--until", UNTIL, "--json"]) == 0
    rows = {r["session"]: r for r in _json_lines(capsys.readouterr().out)}
    assert set(rows) == {"aaaa1111", "bbbb2222"}
    assert rows["aaaa1111"]["calls"] == 2 and rows["aaaa1111"]["tokens"]["cache_read"] == 4_000_000
    # the 10-26 call is outside: the whole session costs more than the window
    _, whole = sc.measure(sc.ClaudeCode(root), root / "p" / "aaaa1111.jsonl")
    assert whole["usd"] > rows["aaaa1111"]["usd"] > 0


def test_session_cost_call_without_time_is_outside_any_window():
    assert sc.in_window({"ts": None}) is True
    assert sc.in_window({"ts": None}, since=sc.when(SINCE)) is False
    assert sc.in_window({"ts": "2026-10-27T23:59:59Z"}, sc.when(SINCE), sc.when(UNTIL)) is True
    assert sc.in_window({"ts": "2026-10-28T00:00:00Z"}, sc.when(SINCE), sc.when(UNTIL)) is False


def test_session_cost_live_json_is_refused(capsys):
    try:
        sc.main(["--live", "--json"])
    except SystemExit as e:
        assert e.code == 2
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- I4 cost daily

def _day(tmp_path, monkeypatch, plan=None):
    root = _projects(tmp_path)
    monkeypatch.setattr(cd, "window", lambda day: (sc.when(SINCE), sc.when(UNTIL)))
    monkeypatch.setattr(cd, "_machine", lambda: "[VAIO-LNX]")
    return cd.measure_day(date(2026, 10, 27), [sc.ClaudeCode(root)], plan=plan if plan else tmp_path / "none.md")


def test_daily_total_top_session_and_first_prompt(tmp_path, monkeypatch):
    r = _day(tmp_path, monkeypatch)
    assert r["sessions"] == 2 and r["date"] == "2026-10-27"
    assert r["top"]["session"] == "aaaa1111"
    # reminders, headings and a lead-in ending with ":" are skipped: the first real line says what it did
    assert r["top"]["what"] == "Fix the daily cost line" and r["top"]["what_from"] == "first prompt"
    assert abs(r["usd"] - sum(sc.measure(sc.ClaudeCode(tmp_path / "projects"), tmp_path / "projects" / "p" / n,
                                         None, sc.when(SINCE), sc.when(UNTIL))[1]["usd"]
                              for n in ("aaaa1111.jsonl", "bbbb2222.jsonl"))) < 0.01


def test_daily_board_row_wins_over_first_prompt(tmp_path, monkeypatch):
    plan = tmp_path / "PLAN.md"
    plan.write_text("| Session | State |\n|---|---|\n| [VAIO-LNX] cost line (session aaaa1111) | DONE, PR open |\n",
                    encoding="utf-8")
    t = _day(tmp_path, monkeypatch, plan)["top"]
    assert t["what_from"] == "board" and "DONE, PR open" in t["what"]


def test_daily_peer_numbers_same_day_other_machine_only(tmp_path):
    files = []
    for name, d in (("old", {"date": "2026-10-26", "machine": "[L15-WIN]", "usd": 9.0}),
                    ("self", {"date": "2026-10-27", "machine": "[VAIO-LNX]", "usd": 7.0}),
                    ("peer", {"date": "2026-10-27", "machine": "[L15-WIN]", "usd": 3.5})):
        f = tmp_path / f"cost-daily-{name}.json"
        f.write_text(json.dumps(d), encoding="utf-8")
        files.append(f)
    d, where = cd.peer_numbers(date(2026, 10, 27), "[VAIO-LNX]", files)
    assert d["usd"] == 3.5 and where.endswith("cost-daily-peer.json")
    assert cd.peer_numbers(date(2026, 10, 27), "[VAIO-LNX]", files[:2]) == (None, None)


def test_daily_line_is_one_line_and_says_when_the_peer_is_missing(tmp_path, monkeypatch):
    r = _day(tmp_path, monkeypatch)
    r["peer"], r["usd_both"] = None, None
    s = cd.line(r)
    assert "\n" not in s and s.startswith("cost 2026-10-27 [VAIO-LNX]: $")
    assert "have not come over the cable" in s
    r["peer"], r["usd_both"] = {"machine": "[L15-WIN]", "usd": 3.5}, round(r["usd"] + 3.5, 2)
    assert "both machines $%.2f ([L15-WIN] $3.50)" % r["usd_both"] in cd.line(r)


def test_daily_cli_json_is_nothing_but_json_and_text_is_one_line(tmp_path, monkeypatch, capsys):
    root = _projects(tmp_path)
    monkeypatch.setattr(sc, "adapters_for", lambda source: [sc.ClaudeCode(root)])
    monkeypatch.setattr(cd, "_plan", lambda: None)
    monkeypatch.setattr(cd, "_machine", lambda: "[VAIO-LNX]")
    monkeypatch.setattr(cd, "window", lambda day: (sc.when(SINCE), sc.when(UNTIL)))
    peer = tmp_path / "cost-daily-L15-WIN-2026-10-27.json"
    peer.write_text(json.dumps({"date": "2026-10-27", "machine": "[L15-WIN]", "usd": 1.25}), encoding="utf-8")
    assert cd.main(["daily", "--date", "2026-10-27", "--json", "--peer", str(peer)]) == 0
    r = _json_lines(capsys.readouterr().out)[0]
    assert r["peer"]["usd"] == 1.25 and r["usd_both"] == round(r["usd"] + 1.25, 2)
    assert cd.main(["daily", "--date", "2026-10-27", "--peer", str(peer)]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 1
    assert cd.main(["daily", "--peer", os.fspath(tmp_path / "missing.json")]) == 2
