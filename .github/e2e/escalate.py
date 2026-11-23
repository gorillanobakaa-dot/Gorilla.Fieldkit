"""e2e: the real `fieldkit escalate` entry point on this OS - no model is called."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FK = [sys.executable, "-m", "fieldkit", "escalate"]


def sh(args, expect):
    r = subprocess.run(FK + args, capture_output=True, text=True)
    print(r.stdout, r.stderr)
    assert r.returncode == expect, f"{args}: exit {r.returncode}, wanted {expect}"
    return r


d = Path(tempfile.mkdtemp())
brief = d / "brief.md"
brief.write_text("noop")
r = sh(["run", str(brief), "--verify", f'"{sys.executable}" -c "import sys; sys.exit(0)"', "--skip-if-green", "--json"], 0)
assert json.loads(r.stdout)["model_calls"] == 0
sh(["run", str(brief)], 2)
bad = d / "settings.json"
bad.write_text("{}")
sh(["cache", "--settings", str(bad)], 3)
good = d / "good.json"
good.write_text(json.dumps({"promptCacheTtl": "1h", "subagentPromptCacheTtl": "1h", "env": {
    "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": "1h", "ENABLE_PROMPT_CACHING_1H": "1"}}))
sh(["cache", "--settings", str(good)], 0)
sh(["ledger", "--last", "1"], 0)
print("PASS: escalate e2e")
