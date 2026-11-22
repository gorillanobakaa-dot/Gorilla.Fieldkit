"""e2e for `fieldkit ci`: the real entry point turns a junit report into a durations file and explains a selection."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "ci", *args], capture_output=True, text=True)
    print(r.stdout, r.stderr)
    assert r.returncode == 0, f"fieldkit ci {' '.join(args)} exited {r.returncode}"
    return r.stdout


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    xml = tmp / "j.xml"
    xml.write_text('<testsuites><testsuite><testcase classname="tests.test_a" name="t" file="tests/test_a.py" time="2"/>'
                   '</testsuite></testsuites>', encoding="utf-8")
    out = tmp / "d.json"
    run("durations", "merge", "--os", "linux", "--run", "1", "--complete", "--out", str(out), str(xml))
    run("durations", "merge", "--os", "windows", "--run", "1", "--complete", "--prev", str(out), "--out", str(out), str(xml))
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["complete"] and d["tests"]["linux"] and d["tests"]["windows"], d
    block = {"run": "1", "coverage": {"linux": 1.0, "windows": 1.0}, "weights": {}}
    sel = tmp / "s.json"
    sel.write_text(json.dumps({"durations": block}), encoding="utf-8")
    line = run("plan", "--explain", "--selection", str(sel))
    assert "durations: run 1, coverage 100%" in line, line
print("ci e2e ok")
