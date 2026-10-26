"""token-audit tests: a made-up transcript with known numbers."""
import json
import subprocess
import sys
from pathlib import Path

from fieldkit.tokens import token_audit  # noqa: E402


def _write(tmp_path):
    lines = [
        {"type": "user", "timestamp": "2026-10-21T10:00:00Z",
         "message": {"content": "hi\nROOM: what the other session(s) did ... 0123456789"}},
        # one reply written as two lines with the same id: counted once
        {"type": "assistant", "timestamp": "2026-10-21T10:00:05Z",
         "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 90_000,
                                            "cache_read_input_tokens": 0, "output_tokens": 50},
                     "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "timestamp": "2026-10-21T10:00:06Z",
         "message": {"id": "m1", "usage": {"input_tokens": 10, "cache_creation_input_tokens": 90_000,
                                            "cache_read_input_tokens": 0, "output_tokens": 50},
                     "content": [{"type": "text", "text": "ok"}]}},
        {"type": "assistant", "timestamp": "2026-10-21T12:00:00Z",
         "message": {"id": "m2", "usage": {"input_tokens": 5, "cache_creation_input_tokens": 1_000,
                                            "cache_read_input_tokens": 250_000, "output_tokens": 20},
                     "content": [{"type": "tool_use", "name": "Bash"}]}},
        "not an object",
    ]
    f = tmp_path / "s.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines) + "\n{broken\n", encoding="utf-8")
    return f


def test_numbers_are_deduplicated_and_summed(tmp_path):
    r = token_audit.audit(_write(tmp_path))
    assert r["api_calls"] == 2
    assert (r["input"], r["cache_write"], r["cache_read"], r["output"]) == (15, 91_000, 250_000, 70)
    assert r["context_first"] == 90_010 and r["context_max"] == 251_005
    assert r["context_bands_100k"][0] == 1 and r["context_bands_100k"][2] == 1
    assert r["hours"] == 2.0
    assert r["top_tools"][0] == ("Bash", 2)


def test_dollars_per_model_and_cache_ttl(tmp_path):
    lines = [
        {"type": "assistant", "message": {"id": "a", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 1_000_000, "cache_creation_input_tokens": 2_000_000,
            "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000},
            "cache_read_input_tokens": 10_000_000, "output_tokens": 1_000_000}}},
        {"type": "assistant", "message": {"id": "b", "model": "some-other-model", "usage": {
            "input_tokens": 5, "output_tokens": 5}}},
    ]
    f = tmp_path / "p.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    r = token_audit.audit(f)
    # 1M in x4 + 1M 5-min write x5 + 1M 1-hour write x8 + 10M read x0.20 + 1M out x20
    assert r["api_usd"] == 4 + 5 + 8 + 2 + 20
    assert r["api_usd_context_reread"] == 2
    assert r["unpriced_calls"] == 1 and r["models"] == {"claude-opus-5-5": 1, "some-other-model": 1}


def test_hook_text_is_counted(tmp_path):
    r = token_audit.audit(_write(tmp_path))
    assert r["hook_hits"]["room"] == 1
    assert r["hook_chars"]["room"] == len("ROOM: what the other session(s) did ... 0123456789")


def test_cli_table_json_and_errors(tmp_path):
    f = _write(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, "-m", "fieldkit.tokens.token_audit", *a],
                                    capture_output=True, text=True)
    r = run(str(f))
    assert r.returncode == 0 and "1 session(s)" in r.stdout
    r = run(str(f), "--json")
    assert json.loads(r.stdout)["api_calls"] == 2
    assert run(str(tmp_path / "missing.jsonl")).returncode == 2
    assert run().returncode == 2
    r = run("--doctor")
    assert r.returncode == 0 and "Python" in r.stdout
