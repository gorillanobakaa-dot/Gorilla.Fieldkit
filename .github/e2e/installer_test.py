"""e2e: the real `fieldkit build-harness installer-test` entry point on this OS. A runner has no Firefox build, so the
safe path is the one every install starts with: the task is looked up first, and a task that does not exist is REFUSED
(exit 2) before anything is installed, removed or written."""
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

rc, out = fk("build-harness", "installer-test", "e2e-no-such-task")
assert rc == 2, rc
assert "REFUSED: no task 'e2e-no-such-task'" in out, out
print("E2E OK: installer-test on", sys.platform)
