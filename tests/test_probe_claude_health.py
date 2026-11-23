"""claude-health starts a real fixed claude session and reports its exit + tail; claude-settings reads only the TTL keys."""
import json
import subprocess

from fieldkit.peers import link_probe as P


class _R:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _find(path="/x/claude"):
    return lambda name: {"agent": name, "path": path}


def test_an_expired_login_is_read_from_the_probe():
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return _R(3, "", "Failed to authenticate: OAuth session expired and could not be refreshed")
    r = P.run("claude-health", finder=_find(), run=run)
    assert r["ok"] and r["data"]["ok"] is False and r["data"]["exit"] == 3
    assert "OAuth session expired" in r["text"] and "FAILS" in r["text"]
    assert seen == [["/x/claude", "-p", P.HEALTH_PROMPT, "--max-turns", "1"]]


def test_a_working_login_reads_works():
    r = P.run("claude-health", finder=_find(), run=lambda c, **k: _R(0, "OK\n"))
    assert r["data"]["ok"] is True and "WORKS" in r["text"]


def test_timeout_and_missing_claude_and_argument():
    def slow(c, **k):
        raise subprocess.TimeoutExpired(c, 1)
    assert P.run("claude-health", finder=_find(), run=slow)["data"]["exit"] is None
    assert P.run("claude-health", finder=_find(""))["data"]["found"] is False
    assert P.run("claude-health", "rm -rf")["ok"] is False


def test_settings_probe_returns_only_the_ttl_keys(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "secret": "XYZ", "promptCacheTtl": "1h", "subagentPromptCacheTtl": "1h",
        "env": {"ENABLE_PROMPT_CACHING_1H": "1", "OTHER": "no", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}}), encoding="utf-8")
    r = P.run("claude-settings", home=str(tmp_path))
    assert "XYZ" not in r["text"] and "OTHER" not in r["text"]
    assert r["data"]["ok"] is False                      # the subagent env key is missing
    assert "env.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL = None" in r["text"]
