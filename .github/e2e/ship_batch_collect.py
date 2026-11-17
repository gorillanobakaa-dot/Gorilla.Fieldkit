"""e2e: the real `fieldkit ship batch collect` entry point on this OS, against this repository's open pull requests
through gh (GH_TOKEN, read only): every PR is either taken or left with a reason; nothing is merged or pushed."""
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
    i = out.find("{") if out.lstrip().startswith("{") else min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.loads(out[i:])


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e", *a],
                   check=True, capture_output=True)

rc, out = fk("ship", "batch", "collect", "--json")
assert rc == 0, rc
res = last_json(out)
assert res["ok"] and isinstance(res["take"], list) and isinstance(res["left"], list), res
assert all(x.get("why") for x in res["left"]), res
print("E2E OK: ship batch collect on", sys.platform)
