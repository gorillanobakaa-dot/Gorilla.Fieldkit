"""e2e: the real `fieldkit ship batch build` entry point on this OS with `--only 0`: no PR is a member, so the build
stops at "empty" (exit 3) in the train's own local clone and pushes nothing."""
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

rc, out = fk("ship", "batch", "build", "--only", "0", "--json")
assert rc == 3, rc
res = last_json(out)
assert res["ok"] is False and res["state"] == "empty" and res["members"] == [], res
assert "nothing was pushed" in res["next"], res
print("E2E OK: ship batch build on", sys.platform)
