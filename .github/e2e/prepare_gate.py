"""e2e: the real `python -m fieldkit prepare` entry point on this OS (plan P37, P9 "prepare first, execute second"):
a job whose tools are all there is READY (exit 0); a brief that needs a tool the runner lacks (nfpm) is NOT READY
(exit 3) naming the missing tool, its recipe and its fallback, and --check set nothing up."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def prepare(*a):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "prepare", *a, "--check", "--json"],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout[-3000:], r.stderr[-2000:])
    return r.returncode, json.loads(r.stdout)


code, p = prepare("core")
assert code == 0 and p["ready"] and p["status"] == "READY", (code, p["status"])
assert {r["tool"] for r in p["rows"]} >= {"git", "python3"} and all(r["ok"] for r in p["rows"]), p["rows"]

with tempfile.TemporaryDirectory() as d:
    brief = Path(d) / "brief.md"
    brief.write_text("# Release\n\nWrite the release page and run gh release.\n", encoding="utf-8")
    code, p = prepare(str(brief))
assert "release" in p["jobs"], p["jobs"]
missing = {r["tool"]: r for r in p["rows"] if not r["ok"]}
assert "nfpm" in missing, list(missing)
assert missing["nfpm"]["fallback"] and missing["nfpm"]["cmd"], missing["nfpm"]      # what to do, and the way around it
assert code == 3 and not p["ready"] and p["status"] == "NOT-READY", (code, p["status"])
assert p["done"] == [], p["done"]
print("E2E OK: prepare on", sys.platform)
