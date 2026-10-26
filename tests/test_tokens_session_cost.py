"""session-cost tests: fixture sessions with known dollar totals, worked out by hand from prices.json.

Claude Code fixture (tests/data/tokens/claude-code/proj/sess-1.jsonl + its subagents folder):
  m1 Opus 5.5, written twice (one reply, two lines): 1M in x$4 + 1M 5-min write x$5 + 1M 1-hour write x$8
     + 1M out x$20 = $37
  m2 Opus 5.5, inference_geo us: 10M cache read x$0.20 x1.1 = $2.20 (all of it re-reading)
  m3 <synthetic>: written by Claude Code itself, not a call
  m4 Opus 5.5 fast mode: 1M in x$8 + 1M out x$40 = $48
  m5 an unknown model: counted, not priced
  s1 Haiku 5.5 subagent, 50k prompt: 50k x$0.10 + 1M out x$0.50 = $0.505
  s2 Haiku 5.5 subagent, 1M prompt (over 100k): 1M x$0.50 + 1M out x$2.50 = $3.00
  total $90.705, 6 calls, 5 priced
"""
import io
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
from fieldkit.tokens import session_cost as sc  # noqa: E402

FIX = HERE / "data" / "tokens" / "claude-code"


# ---------------------------------------------------------------- tiny protobuf writer for the Antigravity fixture

def _v(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _int(field, n):
    return _v(field << 3) + _v(n)


def _len(field, b):
    return _v(field << 3 | 2) + _v(len(b)) + b


def ag_row(steps, model=None, provider=None, inp=0, out=0, write=0, read=0, usage=True):
    """CortexStepGeneratorMetadata as Antigravity writes it (field numbers from its descriptor)."""
    u = b""
    if model is not None:
        u += _int(1, 1401)
    for f, n in ((2, inp), (3, out), (4, write), (5, read)):
        if n:
            u += _int(f, n)
    if provider:
        u += _int(6, provider)
    u += _len(7, b"msg-id")
    cm = _len(1, b"system prompt text") + _int(3, 1401)
    if usage:
        cm += _len(4, u)
    if model:
        cm += _len(19, model.encode())
    cm += _len(17, _len(2, u))           # retry_infos copy of the usage: must not be counted twice
    return _len(1, cm) + _len(2, b"".join(_v(s) for s in steps)) + _len(4, b"exec-id")


def ag_step(seconds):
    return _len(1, _int(1, seconds) + _int(2, 500_000_000))


def make_ag_db(path, rows, steps):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE steps (idx integer, step_type integer, metadata blob, PRIMARY KEY (idx))")
    con.execute("CREATE TABLE gen_metadata (idx integer, data blob, size integer, PRIMARY KEY (idx))")
    for i, s in steps.items():
        con.execute("insert into steps values (?, 0, ?)", (i, ag_step(s)))
    for i, r in enumerate(rows):
        con.execute("insert into gen_metadata values (?, ?, ?)", (i, r, len(r)))
    con.commit()
    con.close()


T_2026_12_31 = 1798758000      # 2026-12-31T23:00:00Z: Gemini 3.8 Flash promotional price
T_2027_01_01 = 1798761601      # 2027-01-01T00:00:01Z: regular price


@pytest.fixture
def ag_db(tmp_path):
    p = tmp_path / "cascade-1.db"
    make_ag_db(p, [
        ag_row([1, 2], "gemini-3.8-flash", 24, inp=1_000_000, out=1_000_000, read=10_000_000),   # $5.25
        ag_row([3], "gemini-3.8-flash", 24, inp=1_000_000, out=1_000_000, read=10_000_000),      # $10.50
        ag_row([4], "claude-opus-5-5-medium", 26, inp=1_000_000, out=1_000_000, write=1_000_000,
               read=1_000_000),                                                                    # $29.20
        ag_row([5]),                                                    # no tokens, no model: skipped
        ag_row([6], "gemini-3.1-pro-low", 24, inp=300_000, out=100_000),                            # unpriced
        ag_row([7], "gemini-3.8-flash", 24, inp=5, usage=False),        # planner row without usage
    ], {1: T_2026_12_31, 2: T_2026_12_31 + 5, 3: T_2027_01_01, 4: T_2027_01_01, 5: T_2027_01_01,
        6: T_2027_01_01, 7: T_2027_01_01})
    return p


# ---------------------------------------------------------------- Claude Code adapter

def _cc_calls():
    a = sc.ClaudeCode(FIX)
    return [sc.price_call(c, sc.prices()) for c in a.read(FIX / "proj" / "sess-1.jsonl", {})]


def test_claude_code_calls_and_dollars():
    calls = _cc_calls()
    assert [c["model"] for c in calls] == ["claude-opus-5-5", "claude-opus-5-5", "claude-opus-5-5",
                                          "claude-unknown-9", "claude-haiku-5-5", "claude-haiku-5-5"]
    usd = [round(c["usd"], 6) for c in calls]
    assert usd == [37.0, 2.2, 48.0, 0.0, 0.505, 3.0]
    assert [c["priced"] for c in calls] == [True, True, True, False, True, True]
    s = sc.summarize(calls)
    assert s["usd"] == 90.705 and s["calls"] == 6 and s["priced_calls"] == 5
    assert s["usd_reread"] == 2.2
    assert s["unpriced"] == {"claude-unknown-9": {"calls": 1, "reason": "no published price for claude-unknown-9"}}
    assert s["hours"] == 1.67


def test_claude_code_live_reads_only_new_complete_lines(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(FIX / "proj", proj)
    f = proj / "sess-1.jsonl"
    data = f.read_bytes()
    cut = data.index(b'{"type": "assistant", "timestamp": "2026-10-21T11:30:00Z"')
    f.write_bytes(data[:cut + 40])               # m4 is half written
    a, state = sc.ClaudeCode(tmp_path), {}
    first = a.read(f, state)
    assert [c["model"] for c in first] == ["claude-opus-5-5", "claude-opus-5-5", "claude-haiku-5-5",
                                           "claude-haiku-5-5"]
    assert a.read(f, state) == []                 # nothing new, and m1 is not counted again
    f.write_bytes(data)
    second = a.read(f, state)
    assert [c["model"] for c in second] == ["claude-opus-5-5", "claude-unknown-9"]
    assert second[0]["speed"] == "fast"


def test_claude_code_title_and_session_lookup():
    a = sc.ClaudeCode(FIX)
    assert a.title(FIX / "proj" / "sess-1.jsonl") == "Fixture session"
    found = sc.resolve("sess-1", [a])
    assert found[0] is a and found[1].name == "sess-1.jsonl"


# ---------------------------------------------------------------- Antigravity adapter

def test_antigravity_usage_fields_providers_and_dates(ag_db):
    calls = [sc.price_call(c, sc.prices()) for c in sc.Antigravity(ag_db.parent).read(ag_db, {})]
    assert [c["model"] for c in calls] == ["gemini-3.8-flash", "gemini-3.8-flash", "claude-opus-5-5-medium",
                                          "gemini-3.1-pro-low"]
    c0, c1, c2, c3 = calls
    assert (c0["input"], c0["output"], c0["cache_read"], c0["cache_write_5m"]) == (1_000_000, 1_000_000,
                                                                                    10_000_000, 0)
    assert c0["ts"] == "2026-12-31T23:00:00Z" and c1["ts"] == "2027-01-01T00:00:01Z"
    assert [c["provider"] for c in calls] == ["gemini-api", "gemini-api", "google-cloud-claude", "gemini-api"]
    assert [round(c["usd"], 6) for c in calls] == [5.25, 10.5, 29.2, 0.0]
    assert c2["priced_as"] == "claude-opus-5-5" and c2["cache_write_5m"] == 1_000_000
    assert not c3["priced"]
    s = sc.summarize(calls)
    assert s["usd"] == 44.95 and s["usd_reread"] == round(0.75 + 1.5 + 0.2, 4)


def test_antigravity_assume_and_long_prompt_tier(ag_db):
    calls = [sc.price_call(c, sc.prices(), {"gemini-3.1-pro": "gemini-3.1-pro-preview"})
             for c in sc.Antigravity(ag_db.parent).read(ag_db, {})]
    # 300k prompt is over Gemini 3.1 Pro's 200k line: 300k x$4 + 100k out x$18 = $3.00
    assert round(calls[3]["usd"], 6) == 3.0 and calls[3]["priced_as"] == "gemini-3.1-pro-preview"


def test_antigravity_live_reads_only_new_rows(ag_db):
    a, state = sc.Antigravity(ag_db.parent), {}
    assert len(a.read(ag_db, state)) == 4
    assert a.read(ag_db, state) == []
    con = sqlite3.connect(str(ag_db))
    con.execute("insert into gen_metadata values (6, ?, 0)",
                (ag_row([3], "gemini-3.8-flash", 24, out=1_000_000),))
    con.commit()
    con.close()
    new = a.read(ag_db, state)
    assert len(new) == 1 and new[0]["output"] == 1_000_000


# ---------------------------------------------------------------- prices

def test_price_rules():
    P = sc.prices()
    assert sc.canonical_model("claude-opus-4-6-thinking", P) == "claude-opus-4-6"
    assert sc.canonical_model("claude-sonnet-5-5-20261001", P) == "claude-sonnet-5-5"
    assert sc.canonical_model("claude-opus-5-5[1m]", P) == "claude-opus-5-5"
    assert sc.canonical_model("gemini-3.8-flash-tiered", P) == "gemini-3.8-flash-tiered"
    r, why = sc.rate(P, "gemini-api", "gemini-3.8-flash", None, 10)
    assert r is None and "date" in why
    r, _ = sc.rate(P, "anthropic", "claude-sonnet-5-5", "2026-10-21", 10)
    assert r["cache_read"] == 0.1                 # the page said $0.10; the old table had $0.20
    for name, t in P["providers"].items():
        assert t["source"].startswith("https://") and len(t["fetched"]) == 10 and len(t["sha256"]) == 64
        for model, entries in t["models"].items():
            for e in entries:
                assert {"input", "output", "cache_read"} <= set(e), (name, model)


# ---------------------------------------------------------------- report, warning, live, CLI

def test_report_shows_reread_share_bands_and_warning():
    calls = _cc_calls()
    s = sc.summarize(calls)
    buf = io.StringIO()
    sc.report(s, calls, show_calls=True, warn_usd=50, out=buf)
    text = buf.getvalue()
    assert "re-reading old context (cache reads): $2.20 = 2% of the cost" in text
    assert "NOT PRICED: 1 call(s) on claude-unknown-9" in text
    assert "COST WARNING: this session has passed $50.00" in text
    assert "500k+" in text
    buf = io.StringIO()
    sc.report(s, calls, warn_usd=100, out=buf)
    assert "COST WARNING" not in buf.getvalue()


def test_live_prints_running_total_and_warns_once_per_step(tmp_path, monkeypatch):
    proj = tmp_path / "p"
    shutil.copytree(FIX / "proj", proj)
    f = proj / "sess-1.jsonl"
    data = f.read_bytes()
    cut = data.index(b'{"type": "assistant", "timestamp": "2026-10-21T11:30:00Z"')
    f.write_bytes(data[:cut])
    monkeypatch.setattr(sc.time, "sleep", lambda s: f.write_bytes(data))   # the next call lands
    buf = io.StringIO()
    a = sc.ClaudeCode(tmp_path)
    sc.live([a], (a, f), 0, 20, {}, polls=2, out=buf)
    lines = buf.getvalue().splitlines()
    totals = [x for x in lines if " calls  $" in x]
    assert len(totals) == 2
    assert "4 calls  $42.70" in totals[0] and "6 calls  $90.7" in totals[1]
    assert sum("COST WARNING" in x for x in lines) == 2      # passed $40 (at $42.70), then $80 (at $90.71)
    assert "passed $80.00" in buf.getvalue()


def test_cli(tmp_path, ag_db):
    run = lambda *a: subprocess.run([sys.executable, "-m", "fieldkit.tokens.session_cost", *a],
                                    capture_output=True, text=True)
    r = run(str(FIX / "proj" / "sess-1.jsonl"), "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["usd"] == 90.705
    r = run(str(ag_db), "--warn-usd", "40")
    assert r.returncode == 3 and "COST WARNING" in r.stdout and "$44.95" in r.stdout
    r = run(str(ag_db), "--assume", "gemini-3.1-pro=gemini-3.1-pro-preview", "--json")
    assert json.loads(r.stdout)["usd"] == 47.95
    r = run(str(ag_db), "--calls")
    assert r.returncode == 0 and "running $" in r.stdout and "unpriced" in r.stdout
    assert run(str(tmp_path / "missing.jsonl")).returncode == 2
    assert run("--assume", "nonsense").returncode == 2
    r = run("--doctor")
    assert r.returncode == 0 and "gemini-api" in r.stdout
