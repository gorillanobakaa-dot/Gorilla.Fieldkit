"""e2e: the real `fieldkit antigravity` entry points on a runner, against the freshly built agy-tools binary."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def fk(*a):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "antigravity", *a], capture_output=True, text=True)
    print(f"$ antigravity {' '.join(a)} -> {r.returncode}\n{r.stdout}{r.stderr}")
    return r


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    cfg = d / ".mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"r": {"url": "https://example.org/mcp"}}}))
    r = fk("doors", str(cfg))
    assert r.returncode == 0 and "remote_doors: 1" in r.stdout, (r.returncode, r.stdout, r.stderr)
    cfg.write_text(json.dumps({"mcpServers": {"r": {"url": "https://example.org/sse"}}}))
    assert fk("doors", str(cfg)).returncode == 3, "legacy /sse must be a finding"
    wf = d / ".agent" / "workflows"
    wf.mkdir(parents=True)
    (wf / "demo.md").write_text("---\ndescription: demo\n---\n# Demo\n")
    assert fk("migrate-workflows", str(d), "--apply").returncode == 0
    assert (d / ".agents" / "skills" / "demo" / "SKILL.md").is_file()
    assert fk("migrate-workflows", str(d), "--undo").returncode == 0
    assert (wf / "demo.md").is_file()
print("e2e agy-tools: ok")
