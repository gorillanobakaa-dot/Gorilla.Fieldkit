"""e2e: the real `fieldkit rules inventory` and `rules lint --files` entry points on this OS (the song sheet): every
instruction file found, with its tokens, and the lint findings over them, as JSON (read only)."""
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

rc, out = fk("rules", "inventory", "--json")
assert rc == 0, rc
inv = last_json(out)
assert isinstance(inv, list), inv
assert all({"path", "kind", "tokens"} <= set(x) for x in inv), inv
rc, out = fk("rules", "lint", "--files", "--json")
assert rc in (0, 3), rc
lint = last_json(out)
assert isinstance(lint, list) and all("kind" in x for x in lint), lint
print("E2E OK: rules song on", sys.platform)
