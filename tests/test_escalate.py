"""fieldkit escalate: the ladder, the failure summary, the cache check (fake starter; no model is called)."""
import json
import sys

import pytest

from fieldkit.escalate import cachecheck, cli, run as R

PY = sys.executable
PASS = [PY, "-c", "import sys; sys.exit(0)"]
FAIL = [PY, "-c", "import sys; print('boom line'); sys.exit(1)"]


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ledger_path", lambda: tmp_path / "escalate.jsonl")
    from fieldkit.lifecycle import models
    monkeypatch.setattr(models, "check_model", lambda m, role, origin: {"ok": True, "why": ""})
    return tmp_path / "escalate.jsonl"


def starter_log(calls, fix_at=None):
    def start(prompt, cwd, effort, model, env, wall):
        calls.append({"prompt": prompt, "effort": effort, "model": model, "env": env})
        return {"result": {"usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.01}}
    return start


def test_first_try_passes_one_call(tmp_path):
    calls = []
    r = R.ladder("brief", str(tmp_path), PASS, starter=starter_log(calls))
    assert r["ok"] and r["passed_at"] == 1 and len(calls) == 1 and calls[0]["effort"] == "low"


def test_red_escalates_to_fresh_high_with_tail(tmp_path):
    calls = []
    flag = tmp_path / "ok"
    check = [PY, "-c", f"import sys,os; print('boom line'); sys.exit(0 if os.path.exists(r'{flag}') else 1)"]

    def start(prompt, cwd, effort, model, env, wall):
        calls.append({"prompt": prompt, "effort": effort})
        if effort == "high":
            flag.write_text("x")
        return {}
    r = R.ladder("brief", str(tmp_path), check, starter=start)
    assert r["ok"] and r["passed_at"] == 2
    assert [c["effort"] for c in calls] == ["low", "high"]
    assert "boom line" in calls[1]["prompt"] and calls[1]["prompt"].startswith("brief")
    assert "boom line" not in calls[0]["prompt"]


def test_skip_if_green_makes_no_model_call(tmp_path):
    calls = []
    r = R.ladder("brief", str(tmp_path), PASS, starter=starter_log(calls), skip_if_green=True)
    assert r["ok"] and r["model_calls"] == 0 and calls == []


def test_xhigh_skipped_when_guard_refuses(tmp_path, monkeypatch):
    from fieldkit.lifecycle import models
    monkeypatch.setattr(models, "check_model", lambda m, role, origin: {"ok": False, "why": "no mandate"})
    calls = []
    r = R.ladder("brief", str(tmp_path), FAIL, starter=starter_log(calls))
    assert not r["ok"] and [c["effort"] for c in calls] == ["low", "high"]
    assert r["attempts"][-1]["result"] == "skipped" and r["model_calls"] == 2


def test_all_rungs_fail_then_xhigh_uses_opus(tmp_path):
    calls = []
    r = R.ladder("brief", str(tmp_path), FAIL, starter=starter_log(calls))
    assert not r["ok"] and [c["effort"] for c in calls] == ["low", "high", "xhigh"]
    assert calls[2]["model"] == "opus" and "boom line" in r["last_output"]


def test_every_session_gets_the_cache_env(tmp_path):
    calls = []
    R.ladder("brief", str(tmp_path), FAIL, starter=starter_log(calls))
    for c in calls:
        assert c["env"]["CLAUDE_CODE_PROMPT_CACHE_TTL"] == "1h"
        assert c["env"]["CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL"] == "1h"
        assert c["env"]["ENABLE_PROMPT_CACHING_1H"] == "1"


def test_start_is_never_a_resume(monkeypatch, tmp_path):
    from fieldkit.autopilot import run as ap
    seen = {}
    monkeypatch.setattr(ap, "claude", lambda *a, **k: seen.update(k) or {})
    R._start("p", str(tmp_path), "high", None, {}, None)
    assert seen["resume"] is None and seen["effort"] == "high"


def test_tail_is_small():
    big = "\n".join(f"line {i} " + "x" * 200 for i in range(500))
    ok, tail = R.run_check([PY, "-c", f"print({big!r}); raise SystemExit(1)"], ".")
    assert not ok and len(tail.splitlines()) <= R.TAIL_LINES and len(tail) <= R.TAIL_CHARS


def test_ledger_has_effort_model_seconds_result_tokens(tmp_path, ledger):
    R.ladder("brief", str(tmp_path), PASS, starter=starter_log([]))
    row = json.loads(ledger.read_text().splitlines()[0])
    assert row["effort"] == "low" and row["result"] == "pass" and row["tokens"] == 15 and "seconds" in row
    assert row["cost_usd"] == 0.01 and "model" in row


def test_check_needs_a_signal():
    with pytest.raises(ValueError):
        R.check_argv()
    assert R.check_argv(ci_pr=5)[-3:] == ["ship", "ci-wait", "5"]


def _settings(tmp_path, **kw):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(kw))
    return p


GOOD_ENV = dict(cachecheck.ENV_VARS)


def test_cache_ok(tmp_path):
    p = _settings(tmp_path, promptCacheTtl="1h", subagentPromptCacheTtl="1h", env=GOOD_ENV)
    assert cachecheck.problems(p, environ={}) == []


def test_cache_fails_without_ttl(tmp_path):
    p = _settings(tmp_path, promptCacheTtl="5m", env=GOOD_ENV)
    out = cachecheck.problems(p, environ={})
    assert len(out) == 2 and all("must be '1h'" in x for x in out)


def test_cache_fails_without_env(tmp_path):
    p = _settings(tmp_path, promptCacheTtl="1h", subagentPromptCacheTtl="1h")
    assert len(cachecheck.problems(p, environ={})) == 3


def test_cache_fails_on_unreadable(tmp_path):
    assert cachecheck.problems(tmp_path / "nope.json", environ={})


def test_cli_cache_exit_3(tmp_path):
    p = _settings(tmp_path)
    assert cli.main(["cache", "--settings", str(p)]) == 3


def test_cli_run_refuses_without_signal(tmp_path):
    b = tmp_path / "b.md"
    b.write_text("x")
    assert cli.main(["run", str(b)]) == 2
