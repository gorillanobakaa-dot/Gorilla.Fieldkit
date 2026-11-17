"""e2e: the real `fieldkit ship batch run` entry point on this OS with `--only 0`: fewer than two PRs are ready, so
the run stops at "nothing-to-batch" (exit 3) before any branch, PR or merge."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def fk(*args, cwd=None, env=None):
    r = subprocess.run([sys.executable, "-m", "fieldkit", *args], capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace", cwd=cwd, env=dict(os.environ, **(env or {})))
    print(r.stdout[-3000:], r.stderr[-3000:])
    assert "Traceback" not in r.stderr, r.stderr
    return r.returncode, r.stdout


def last_json(out):
    """The JSON document the command printed (it may be preceded by progress lines)."""
    i = min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.JSONDecoder().raw_decode(out[i:])[0]   # a NEXT: line may follow the document


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e", *a],
                   check=True, capture_output=True)

rc, out = fk("ship", "batch", "run", "--only", "0", "--json")
assert rc == 3, rc
res = last_json(out)
assert res["ok"] is False and res["state"] == "nothing-to-batch" and res["collected"] == [], res
print("E2E OK: ship batch run on", sys.platform)
