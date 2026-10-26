"""Session rotation for every agent: the Codex, Gorilla OpenCode, generic and IBM Bob adapters, the agent-neutral
trigger (rotate check --source/--cwd/--newest, the rule block, the run-guard check) and rotate doctor's per-agent rows.

Fixtures (tests/data/tokens/agents, recorded by record_fixtures.py from the real local stores on 2026-10-26, ids,
paths, titles, account fields and response ids replaced):
  codex/rollout-...-01a0dd9e-...0001.jsonl[.zst]  a real 4-call Codex desktop session (cli 0.158), gpt-6-luna
  gorilla-opencode.json                           the real sessions/messages schema + two real session rows

Codex numbers worked out by hand (prices.json openai, gpt-6-luna: input $0.10, cached $0.01, output $0.50 per M;
input_tokens INCLUDES cached_input_tokens):
  call 1: (25641-20224) x 0.10 + 20224 x 0.01 + 125 x 0.50 = 541.70 + 202.24 + 62.50 = 806.44 per M
  call 2: (25826-25344) x 0.10 + 25344 x 0.01 + 168 x 0.50 =  48.20 + 253.44 + 84.00 = 385.64
  call 3: (26120-25344) x 0.10 + 253.44 + 419 x 0.50        =  77.60 + 253.44 + 209.50 = 540.54
  call 4: (26650-25344) x 0.10 + 253.44 + 87 x 0.50         = 130.60 + 253.44 + 43.50 = 427.54
  total 2160.16 per M = $0.00216016; re-reading 202.24 + 3 x 253.44 = 962.56 per M; last call sent 26,650 tokens.
Gorilla OpenCode sess-1 (real row): prompt_tokens 13,260, cost $0.00947882, 3 priced calls, last call $0.00131472.
"""
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from fieldkit.core import preflight
from fieldkit.tokens import rotate, session_cost as sc

HERE = Path(__file__).resolve().parent
FIX = HERE / "data" / "tokens" / "agents"
SID = "01a0dd9e-0000-7000-8000-000000000001"
ROLLOUT = f"rollout-2026-09-26T13-08-41-{SID}.jsonl"
CFG = dict(rotate.DEFAULTS)
#: reading a .zst rollout needs Python 3.14 or the zstandard package; CI has neither (a new download is the owner's call)
NEEDS_ZSTD = pytest.mark.skipif(sc._zstd_reader() is None, reason="no zstd decoder (Python 3.14+ or zstandard)")


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.setattr(rotate, "config", lambda: dict(CFG))


def codex_home(tmp_path, zst=False, lines=None, name=ROLLOUT):
    d = tmp_path / "codex" / "sessions" / "2026" / "09" / "26"
    d.mkdir(parents=True, exist_ok=True)
    src = FIX / "codex" / (ROLLOUT + (".zst" if zst else ""))
    dst = d / (name + (".zst" if zst else ""))
    if lines is None:
        shutil.copy(src, dst)
    else:
        dst.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    return tmp_path / "codex" / "sessions", dst


def fixture_lines():
    return [json.loads(x) for x in (FIX / "codex" / ROLLOUT).read_text(encoding="utf-8").splitlines()]


def priced(calls):
    return [sc.price_call(c, sc.prices()) for c in calls]


# ---------------------------------------------------------------- Codex

@pytest.mark.parametrize("zst", [False, pytest.param(True, marks=NEEDS_ZSTD)])
def test_codex_real_session_measured_and_priced(tmp_path, zst):
    root, p = codex_home(tmp_path, zst=zst)
    a = sc.Codex(root)
    assert [s.stem for s in a.sessions()] == [SID]
    calls = priced(a.read(a.sessions()[0], {}))
    assert len(calls) == 4 and {c["model"] for c in calls} == {"gpt-6-luna"} and calls[0]["provider"] == "openai"
    assert sum(c["usd"] for c in calls) == pytest.approx(2160.16e-6)
    assert sum(c["usd_reread"] for c in calls) == pytest.approx(962.56e-6)
    assert calls[-1]["context"] == 26_650 and calls[0]["input"] == 25_641 - 20_224
    r = rotate.judged(rotate.measure(a, a.sessions()[0]), CFG)
    assert (r["session"], r["calls"], r["context_last"], r["level"], r["cwd"]) == (SID, 4, 26_650, "OK",
                                                                                   "C:\\work\\repo")


def test_codex_old_writer_token_count_only_gives_the_same_calls(tmp_path):
    rows = [x for x in fixture_lines() if x["type"] != "token_usage_record"]
    rows.insert(5, rows[4])                                   # the same token_count written twice: one call
    root, _ = codex_home(tmp_path, lines=rows)
    calls = priced(sc.Codex(root).read(sc.Codex(root).sessions()[0], {}))
    assert len(calls) == 4 and sum(c["usd"] for c in calls) == pytest.approx(2160.16e-6)


def test_codex_reads_only_new_lines_of_a_live_rollout(tmp_path):
    rows = fixture_lines()
    root, p = codex_home(tmp_path, lines=rows[:6])
    a = sc.Codex(root)
    st = {}
    first = a.read(a.sessions()[0], st)
    with open(p, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(x) + "\n" for x in rows[6:]))
        f.write('{"type": "token_usage_record", "payload"')           # a line still being written
    second = a.read(a.sessions()[0], st)
    assert len(first) + len(second) == 4 and a.read(a.sessions()[0], st) == []
    with open(p, "a", encoding="utf-8") as f:                         # the line is finished: read then, not lost
        f.write(': {"response_id": "r9", "usage": {"input_tokens": 5, "cached_input_tokens": 0, '
                '"output_tokens": 1}}}\n')
    assert [c["context"] for c in priced(a.read(a.sessions()[0], st))] == [5]


def test_codex_same_response_written_twice_is_one_call(tmp_path):
    rows = fixture_lines()
    rec = next(x for x in rows if x["type"] == "token_usage_record")
    root, _ = codex_home(tmp_path, lines=rows + [rec])
    assert len(sc.Codex(root).read(sc.Codex(root).sessions()[0], {})) == 4


@NEEDS_ZSTD
def test_codex_zst_read_once_then_skipped(tmp_path, monkeypatch):
    root, _ = codex_home(tmp_path, zst=True)
    a, st = sc.Codex(root), {}
    assert len(a.read(a.sessions()[0], st)) == 4
    monkeypatch.setattr(sc, "_zstd_reader", lambda: pytest.fail("an unchanged .zst is decompressed again"))
    assert a.read(a.sessions()[0], st) == []


def test_codex_without_a_zstd_decoder_is_said_not_hidden(tmp_path, monkeypatch):
    root, _ = codex_home(tmp_path, zst=True)
    monkeypatch.setattr(sc, "_zstd_reader", lambda: None)
    rows = rotate.check(hours=1, adapters=[sc.Codex(root)], cfg=CFG)
    assert rows[0]["level"] == "OK" and "zstandard" in rows[0]["why"][0]


def test_codex_subagent_call_adds_dollars_not_context(tmp_path):
    rows = fixture_lines()
    sub = json.loads(json.dumps(next(x for x in rows if x["type"] == "token_usage_record")))
    sub["payload"].update(thread_id="another-thread", response_id="resp_sub")
    sub["payload"]["usage"]["input_tokens"] = 900_000
    rows.append(sub)
    root, _ = codex_home(tmp_path, lines=rows)
    r = rotate.measure(sc.Codex(root), sc.Codex(root).sessions()[0])
    assert r["calls"] == 5 and r["context_last"] == 26_650


def test_codex_long_context_priced_at_the_long_rate_and_rotates(tmp_path):
    rows = [x for x in fixture_lines() if x["type"] in ("session_meta", "turn_context")]
    rows.append({"timestamp": "2026-09-26T12:10:00Z", "type": "token_usage_record",
                 "payload": {"response_id": "r1", "usage": {"input_tokens": 400_000, "cached_input_tokens": 300_000,
                                                            "cache_write_input_tokens": 0, "output_tokens": 1_000}}})
    root, _ = codex_home(tmp_path, lines=rows)
    c = priced(sc.Codex(root).read(sc.Codex(root).sessions()[0], {}))[0]
    # over 272K: input $0.20, cached $0.02, output $0.75 -> 100k x 0.20 + 300k x 0.02 + 1k x 0.75 = 26,750 per M
    assert c["usd"] == pytest.approx(26_750e-6)
    assert rotate.check(hours=1, adapters=[sc.Codex(root)], cfg=CFG)[0]["level"] == "ROTATE"


def test_codex_custom_provider_is_counted_not_priced(tmp_path):
    rows = fixture_lines()
    rows[0]["payload"]["model_provider"] = "ollama"
    root, _ = codex_home(tmp_path, lines=rows)
    r = rotate.judged(rotate.measure(sc.Codex(root), sc.Codex(root).sessions()[0]), CFG)
    assert r["usd"] == 0 and r["unpriced_calls"] == 4 and "no published price" in r["why"][-1]


def test_codex_title_from_the_session_index(tmp_path):
    root, _ = codex_home(tmp_path)
    (root.parent / "session_index.jsonl").write_text(
        json.dumps({"id": SID, "thread_name": "Old"}) + "\n" + json.dumps({"id": "x", "thread_name": "No"}) + "\n"
        + json.dumps({"id": SID, "thread_name": "Clean the tree"}) + "\n", encoding="utf-8")
    a = sc.Codex(root)
    assert a.title(a.sessions()[0]) == "Clean the tree" and a.owns(a.sessions()[0])
    assert not a.owns(Path("notes.jsonl"))


# ---------------------------------------------------------------- Gorilla OpenCode

def opencode_db(tmp_path, extra=()):
    fx = json.loads((FIX / "gorilla-opencode.json").read_text(encoding="utf-8"))
    root = tmp_path / "goc"
    root.mkdir()
    con = sqlite3.connect(root / "gorilla-opencode.db")
    for sql in fx["schema"]:
        con.execute(sql)
    for table, rows in (("sessions", fx["sessions"] + list(extra)), ("messages", fx["messages"])):
        for r in rows:
            con.execute("insert into %s (%s) values (%s)" % (table, ",".join(r), ",".join("?" * len(r))),
                        list(r.values()))
    con.commit()
    con.close()
    return sc.GorillaOpenCode(root), fx


def test_opencode_real_rows_measured_from_its_own_totals(tmp_path):
    a, fx = opencode_db(tmp_path)
    ss = a.sessions()
    assert [s.stem for s in ss] == ["sess-1", "sess-2"]           # newest first
    r = rotate.judged(rotate.measure(a, ss[0]), CFG)
    assert (r["calls"], r["context_last"], r["unpriced_calls"], r["cwd"]) == (3, 13_260, 0, "C:\\work\\repo")
    assert r["usd"] == pytest.approx(0.00947882, abs=5e-5) and r["level"] == "OK" and r["title"] == "fixture session 1"
    calls, s = sc.measure(a, ss[0])
    assert s["calls"] == 3 and s["priced_calls"] == 3 and s["last_call_usd"] == 0.0013  # $0.00131472 to 4 places
    assert s["models"] == {"gemini-flash-lite-latest": 1} and s["unpriced"] == {}


def test_opencode_cached_meter_keeps_the_totals_until_the_row_changes(tmp_path):
    a, _ = opencode_db(tmp_path)
    s = a.sessions()[0]
    assert rotate.measure(a, s)["calls"] == 3
    assert rotate.measure(a, s)["calls"] == 3                     # nothing new: the meter's totals stand
    st = {}
    assert len(a.read(s, st)) == 1 and a.read(s, st) == []         # an unchanged row adds nothing (live view)
    con = sqlite3.connect(a.db)
    con.execute("update sessions set calls_priced = 5, prompt_tokens = 360000, updated_at = updated_at + 1 "
                "where id = 'sess-1'")
    con.commit()
    con.close()
    r = rotate.judged(rotate.measure(a, a.sessions()[0]), CFG)
    assert (r["calls"], r["context_last"], r["level"]) == (5, 360_000, "ROTATE")


def test_opencode_child_session_is_billed_to_its_parent_and_not_listed(tmp_path):
    a, fx = opencode_db(tmp_path)
    child = dict(fx["sessions"][1], id="kid", parent_session_id="sess-1", cost=1.5, calls_priced=2,
                 calls_unpriced=1, prompt_tokens=900_000)
    shutil.rmtree(tmp_path / "goc")
    a, _ = opencode_db(tmp_path, extra=[child])
    assert "kid" not in [s.stem for s in a.sessions()]
    r = rotate.judged(rotate.measure(a, a.sessions()[0]), CFG)
    assert r["calls"] == 6 and r["unpriced_calls"] == 1 and r["context_last"] == 13_260
    assert r["usd"] == pytest.approx(0.00947882 + 1.5, abs=5e-5)          # rotate rounds to 4 places
    assert "1 call(s) on models with no published price" in r["why"][-1]


def test_opencode_session_found_by_id_and_by_its_row_name(tmp_path, monkeypatch):
    a, _ = opencode_db(tmp_path)
    monkeypatch.setattr(sc, "adapters_for", lambda s: [a])
    assert sc.resolve("sess-2", [a])[1].stem == "sess-2"
    assert sc.resolve(str(a.sessions()[0]), [a])[1].stem == "sess-1"
    assert a.owns(a.sessions()[0]) and not a.owns(Path("x.jsonl"))


def test_opencode_missing_or_broken_db_has_no_sessions(tmp_path):
    assert sc.GorillaOpenCode(tmp_path / "none").sessions() == []
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "gorilla-opencode.db").write_bytes(b"not a database")
    assert sc.GorillaOpenCode(tmp_path / "bad").sessions() == []


# ---------------------------------------------------------------- the generic contract

def contract(**kw):
    d = {"ts": "2026-10-26T10:00:00Z", "session": "job-1", "model": "gpt-6-luna", "input_tokens": 300_000,
         "cached_tokens": 200_000, "output_tokens": 1_000, "provider": "openai"}
    d.update(kw)
    return {k: v for k, v in d.items() if v is not None}


def test_generic_contract_priced_and_bad_lines_counted(tmp_path):
    f = tmp_path / "usage" / "my-agent" / "job-1.jsonl"
    f.parent.mkdir(parents=True)
    good = [contract(), contract(input_tokens=10_000, cached_tokens=0, provider=None),
            contract(input_tokens=500_000, cached_tokens=0, sub=True, cwd="C:/work/repo")]
    bad = [contract(model=None), contract(output_tokens=-1), contract(session="job-2"),
           contract(cached_tokens=400_000), contract(input_tokens=1.5), contract(cached_tokens=False)]
    f.write_text("".join(json.dumps(x) + "\n" for x in good + bad) + "not json\n", encoding="utf-8")
    a = sc.Generic(tmp_path / "usage")
    st = {}
    calls = priced(a.read(a.sessions()[0], st))
    assert len(calls) == 3 and st["bad"] == 7
    # a 300k prompt is over 272K, the long rate: 100k x 0.20 + 200k x 0.02 + 1k x 0.75 = 24,750 per M
    assert calls[0]["usd"] == pytest.approx(24_750e-6) and calls[0]["context"] == 300_000
    assert not calls[1]["priced"] and calls[2]["sub"]
    r = rotate.judged(rotate.measure(a, a.sessions()[0]), CFG)
    assert r["context_last"] == 10_000 and r["cwd"] == "C:/work/repo" and r["unpriced_calls"] == 1
    assert a.owns(f) and not a.owns(tmp_path / "elsewhere.jsonl")


# ---------------------------------------------------------------- IBM Bob

def test_bob_is_never_parsed_and_doctor_says_why(tmp_path):
    b = sc.IBMBob(tmp_path / "bob.db")
    (tmp_path / "bob.db").write_bytes(b"x")
    assert b.sessions() == [] and b.read(tmp_path / "bob.db", {}) == [] and not b.verified
    row = next(s for s in rotate.agents_status() if s["agent"] == "ibm-bob")
    assert not row["measured"] and not row["triggered"] and "not verified" in row["note"]


# ---------------------------------------------------------------- the agent-neutral trigger

def test_check_source_cwd_newest(tmp_path, monkeypatch):
    rows = fixture_lines()
    root, p1 = codex_home(tmp_path)
    rows[0]["payload"]["cwd"] = "C:\\other"
    rows[2]["payload"]["cwd"] = "C:\\other"
    _, p2 = codex_home(tmp_path, lines=rows, name=ROLLOUT.replace("0001.jsonl", "0002.jsonl"))
    os.utime(p1, (time.time() - 60, time.time() - 60))
    monkeypatch.setattr(sc, "CODEX_HOME", root.parent)
    assert len(rotate.check(1, source="codex", cfg=CFG)) == 2
    assert [r["session"][-4:] for r in rotate.check(1, source="codex", newest=True, cfg=CFG)] == ["0002"]
    got = rotate.check(1, source="codex", cwd="C:\\work\\repo", newest=True, cfg=CFG)
    assert [r["session"][-4:] for r in got] == ["0001"]
    assert rotate.main(["check", "--source", "codex", "--newest"]) == 0


def test_rule_install_add_same_update_keeps_crlf(tmp_path):
    f = tmp_path / "AGENTS.md"
    f.write_bytes(b"# Agent rules\r\n\r\nBe brief.\r\n")
    assert not rotate.rule_present(f)
    assert rotate.install_rule(f) == "added" and rotate.rule_present(f)
    assert rotate.install_rule(f) == "same"
    raw = f.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    f.write_bytes(raw.replace(b"At the start", b"OLD TEXT") + b"Tail line.\r\n")
    assert rotate.install_rule(f) == "updated"
    t = f.read_text(encoding="utf-8")
    assert "OLD TEXT" not in t and t.count(rotate.RULE_BEGIN) == 1 and t.rstrip().endswith("Tail line.")
    new = tmp_path / "codex" / "AGENTS.md"
    assert rotate.install_rule(new) == "added" and new.read_text(encoding="utf-8") == rotate.rule_text()


def test_rule_tells_the_agent_check_and_handover():
    t = rotate.rule_text()
    assert "fieldkit rotate check --source <agent> --cwd . --newest" in t and "fieldkit rotate handover" in t
    assert "exit 2 ROTATE" in t and "exit 1 WARN" in t


def test_agents_status_measured_and_triggered(tmp_path, monkeypatch):
    gem, cod, proj = tmp_path / "GEMINI.md", tmp_path / "AGENTS-codex.md", tmp_path / "AGENTS.md"
    monkeypatch.setattr(rotate, "RULE_FILES", {"antigravity": gem, "codex": cod})
    rotate.install_rule(cod)
    rotate.install_rule(proj)
    st = {s["agent"]: s for s in rotate.agents_status(proj)}
    assert set(st) == set(sc.ADAPTERS)
    assert st["codex"]["triggered"] and not st["antigravity"]["triggered"]
    assert "rotate rule --install" in st["antigravity"]["note"]
    assert st["gorilla-opencode"]["triggered"] and st["generic"]["triggered"]
    assert all(st[a]["measured"] for a in ("claude-code", "antigravity", "codex", "gorilla-opencode", "generic"))


def test_preflight_check_names_the_worst_session(tmp_path, monkeypatch):
    rows = [x for x in fixture_lines() if x["type"] in ("session_meta", "turn_context")]
    rows.append({"timestamp": "2026-09-26T12:10:00Z", "type": "token_usage_record",
                 "payload": {"response_id": "r1", "usage": {"input_tokens": 400_000, "cached_input_tokens": 0,
                                                            "output_tokens": 10}}})
    root, _ = codex_home(tmp_path, lines=rows)
    monkeypatch.setattr(sc, "adapters_for", lambda s: [sc.Codex(root)])
    st, line = rotate.preflight_check()
    assert st == "warn" and line.startswith("rotate: ROTATE codex 01a0dd9e") and "rotate handover --session" in line
    rows[-1]["payload"]["usage"]["input_tokens"] = 260_000                  # WARN is named too
    root, _ = codex_home(tmp_path, lines=rows)
    shutil.rmtree(tmp_path / "rot", ignore_errors=True)
    st, line = rotate.preflight_check()
    assert st == "warn" and line.startswith("rotate: WARN codex") and "fresh session" in line
    monkeypatch.setattr(sc, "adapters_for", lambda s: [sc.Codex(tmp_path / "none")])
    assert rotate.preflight_check()[0] == "ok"
    assert "fieldkit.tokens.rotate:preflight_check" in preflight.STEPS


def test_rule_cli_prints_and_installs(tmp_path, capsys):
    assert rotate.main(["rule"]) == 0 and rotate.RULE_BEGIN in capsys.readouterr().out
    f = tmp_path / "GEMINI.md"
    assert rotate.main(["rule", "--install", str(f)]) == 0 and "added" in capsys.readouterr().out


def test_openai_prices_have_their_source_and_date():
    p = sc.prices()["providers"]["openai"]
    assert p["source"] == "https://developers.openai.com/api/docs/pricing" and p["fetched"] == "2026-10-26"
    assert len(p["sha256"]) == 64 and "gpt-6-luna" in p["models"] and "gpt-5.3-codex" in p["models"]
    assert all("cache_read" in e[0] for e in p["models"].values())    # a model without one is left out
