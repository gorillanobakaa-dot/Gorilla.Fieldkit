"""e2e: one tool's real `python -m fieldkit ...` entry point on this OS, safe paths only (read-only, a throwaway root,
or the refusal a CI runner must get). Usage: python .github/e2e/vaio_batch.py TOOL_ID. Written for the v0.10.0
release (docs/INTEGRATION.md [5-e2e]: a tool is done on an OS only after its entry point ran there)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def fk(*args, ok=(0,)):
    r = subprocess.run([sys.executable, "-m", "fieldkit", *args], capture_output=True, text=True, timeout=300)
    out = r.stdout + r.stderr
    print(f"$ fieldkit {' '.join(args)} -> {r.returncode}\n{out[-3000:]}")
    assert r.returncode in ok, f"exit {r.returncode}, wanted {ok}"
    assert "Traceback (most recent call last)" not in out, "the command crashed"
    return r.returncode, out


def autoaccept():
    _, out = fk("autoaccept", "status", "--json")
    assert "enabled" in json.loads(out) and "kinds" in json.loads(out)
    _, out = fk("autoaccept", "list", "--json")
    assert isinstance(json.loads(out), list)


def compileonce():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "fieldkit").mkdir()
        (Path(d) / "fieldkit" / "a.py").write_text("x = 1\n", encoding="utf-8")
        rc, out = fk("compileonce", "check", "--root", d, "--json", ok=(3,))
        assert json.loads(out)["stale_count"] == 1
        rc, out = fk("compileonce", "build", "--root", d, "--json")
        assert json.loads(out)["ok"] and json.loads(out)["compiled_now"] == 1
        rc, out = fk("compileonce", "check", "--root", d, "--json")
        assert json.loads(out)["ok"] and json.loads(out)["stale_count"] == 0


def idle_report():
    _, out = fk("idle", "report", "--since", "2h", "--json")
    json.loads(out)


def harness(name):
    _, out = fk("capture", "project", name, "--json")
    d = json.loads(out)
    assert d["project"] == name and d.get("workflows") is not None, d


def bh_refuses(*args):
    """A build-harness verb on a task that does not exist must refuse cleanly (exit 2), never crash or act."""
    _, out = fk("build-harness", *args, ok=(2,))
    assert "REFUSED" in out and "no task" in out, out


TOOLS = {
    "autoaccept": autoaccept,
    "compileonce": compileonce,
    "idle-report": idle_report,
    "harness-firefox": lambda: harness("firefox"),
    "harness-kernel": lambda: harness("kernel"),
    "harness-opencode": lambda: harness("opencode"),
    "bh-capture": lambda: bh_refuses("capture", "e2e-no-such-task"),
    "bh-request": lambda: bh_refuses("request", "e2e-no-such-task", "S1", "skip", "--note", "e2e"),
    "bh-leakgate-ci": lambda: bh_refuses("leakgate-ci", "pack", "e2e-no-such-task"),
}

tool = sys.argv[1]
TOOLS[tool]()
print(f"E2E OK: {tool} on {sys.platform}")
