"""e2e: the real `fieldkit build-harness installer-test` entry point on this OS. A runner has no Firefox build, so the
safe path is the one every install starts with: the task is looked up first, and a task that does not exist is REFUSED
(exit 2) before anything is installed, removed or written."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def fk(*args, cwd=None, env=None, both=False):
    r = subprocess.run([sys.executable, "-m", "fieldkit", *args], capture_output=True, text=True, timeout=300,
                       encoding="utf-8", errors="replace", cwd=cwd, env=dict(os.environ, **(env or {})))
    print(r.stdout[-3000:], r.stderr[-3000:])
    assert "Traceback" not in r.stderr, r.stderr
    # the REFUSED line goes to stderr: both=True reads stdout and stderr together
    return r.returncode, (r.stdout + r.stderr) if both else r.stdout


def last_json(out):
    """The JSON document the command printed (it may be preceded by progress lines)."""
    i = min(x for x in (out.find("{"), out.find("[")) if x >= 0)
    return json.JSONDecoder().raw_decode(out[i:])[0]   # a NEXT: line may follow the document


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=e2e", "-c", "user.email=e2e", *a],
                   check=True, capture_output=True)

rc, out = fk("build-harness", "installer-test", "e2e-no-such-task", both=True)
assert rc == 2, rc
assert "REFUSED: no task 'e2e-no-such-task'" in out, out

rc_pf, out_pf = fk("install", "preflight", "--allow-missing-terminal", "--json", both=True)
doc_pf = last_json(out_pf)
assert doc_pf["mutations_performed"] == 0, doc_pf
assert doc_pf["order"][0] == "installer", doc_pf
assert "terminal:gorilla-size" in doc_pf["order"] and "terminal:gorilla-trash" in doc_pf["order"], doc_pf
print("E2E OK: installer-test and install preflight on", sys.platform)
