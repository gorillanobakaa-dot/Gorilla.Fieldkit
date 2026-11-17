"""e2e: the real `python -m fieldkit once` entry point on this OS (P28 E): a success is skipped, a changed input runs
again, a watcher's repeated news says stop."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def once(*args, stdin=None):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "once", *args], capture_output=True, text=True, timeout=120,
                       input=stdin, env=dict(os.environ, FIELDKIT_ONCE_JOURNAL=str(J)))
    print(r.stdout, r.stderr)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


with tempfile.TemporaryDirectory() as d:
    J = Path(d) / "journal.jsonl"
    inp, mark = Path(d) / "in.txt", Path(d) / "ran.txt"
    inp.write_text("a", encoding="utf-8")
    cmd = ["--", sys.executable, "-c", f"open({str(mark)!r}, 'a').write('x')"]
    rc, res = once("run", "--key", "k", "--state", str(inp), *cmd)
    assert rc == 0 and res["done"] and not res["skipped"], res
    rc, res = once("run", "--key", "k", "--state", str(inp), *cmd)
    assert rc == 0 and res["skipped"], res
    assert mark.read_text(encoding="utf-8") == "x"
    inp.write_text("b", encoding="utf-8")
    rc, res = once("check", "--key", "k", "--state", str(inp), *cmd)
    assert rc == 3 and not res["done"], res
    rc, res = once("run", "--key", "k", "--state", str(inp), *cmd)
    assert rc == 0 and mark.read_text(encoding="utf-8") == "xx", res
    rc, res = once("watch", "--key", "w", stdin="2026-11-16T10:00:00Z nothing changed\n")
    assert rc == 3 and res["changed"], res
    rc, res = once("watch", "--key", "w", stdin="2026-11-16T10:05:00Z nothing changed\n")
    assert rc == 0 and res["done"] and res["repeats"] == 2, res
print("E2E OK: once on", sys.platform)
