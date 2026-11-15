"""P9 e: the autonomy test. A small code project and a non-code one go through the whole loop - emerge plan and run,
a failing stage that the fix rung repairs with a commit, auto-accept, the gate, the build - with no terminal, no owner
input and no inbox answer. CI runs it on Windows and on Linux (.github/workflows/tests.yml matrix).

Any new stop that waits for a person (input(), an inbox item, wait_owner), any "X only" refusal of a command and any
pinned recipe fails here with the stop's name. Mutation proof: tests/autonomy_mutations.yaml
(fieldkit mutate --plan tests/autonomy_mutations.yaml -- python -m pytest -q -x -p no:cacheprovider
tests/test_autonomy_loop.py).
"""
import builtins
import subprocess
import sys

import pytest
import yaml

from fieldkit.autopilot import hardlines
from fieldkit.core import crossos, platform_gate
from fieldkit.emerge import lint
from fieldkit.emerge import recipe as R
from fieldkit.emerge import runner as E
from fieldkit.lifecycle import autoaccept

PY = sys.executable


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


class Stops:
    """Every way the loop could wait for a person, recorded by name instead of waiting."""
    def __init__(self):
        self.seen = []

    def inbox(self, title, why="", command="", kind="", **kw):
        self.seen.append(f"inbox item: {title}")
        return len(self.seen)

    def input(self, prompt=""):
        self.seen.append(f"input() asked a person: {prompt!r}")
        raise EOFError("no terminal in the autonomy test")


@pytest.fixture
def stops(monkeypatch, tmp_path):
    s = Stops()
    monkeypatch.setattr(builtins, "input", s.input)
    monkeypatch.setattr(sys, "stdin", open(subprocess.os.devnull))       # no terminal
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path / "tc"))
    monkeypatch.setattr("fieldkit.autopilot.inbox.add", s.inbox)
    yield s
    sys.stdin.close()


# The two sample projects: (files, the stage that fails until fixed, the fix, the build output)
CODE = {"files": {"calc.py": "def add(a, b):\n    return a - b\n",
                  "check.py": "import sys; sys.path.insert(0, sys.argv[1]); import calc\n"
                              "sys.exit(0 if calc.add(2, 3) == 5 else 1)\n"},
        "fix": ("calc.py", "def add(a, b):\n    return a + b\n"),
        "test": ["{w}/check.py", "{w}"]}
DOC = {"files": {"essay.md": "# Title\n\nTODO write the body\n",
                 "check.py": "import sys; t = open(sys.argv[1] + '/essay.md').read()\n"
                             "sys.exit(1 if 'TODO' in t else 0)\n"},
       "fix": ("essay.md", "# Title\n\nThe body, written.\n"),
       "test": ["{w}/check.py", "{w}"]}


def sample(tmp, name, proj):
    w = tmp / name
    w.mkdir()
    git(w, "init", "-q", "-b", "main")
    git(w, "config", "user.email", "t@t")
    git(w, "config", "user.name", "t")
    for f, t in proj["files"].items():
        write(w / f, t)
    write(w / ".gitignore", "out/\n")
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "sample")
    gate = ("import sys,pathlib; bad=[p for p in pathlib.Path(sys.argv[1]).rglob('*') if p.is_file() and "
            "'.git' not in p.parts and (b'telemetry' + b'.example') in p.read_bytes()]; sys.exit(1 if bad else 0)")
    pack = ("import sys,zipfile,pathlib; w=pathlib.Path(sys.argv[1]); (w/'out').mkdir(exist_ok=True); "
            "z=zipfile.ZipFile(w/'out'/'build.zip','w'); [z.write(p, p.name) for p in w.glob('*.*') if p.is_file()]; "
            "z.close()")
    spec = {"name": f"sample-{name}", "vars": {"w": str(w)},
            "sources": [{"id": "w", "git": "{w}", "track": "tree"}],
            "stages": [
                {"id": "test", "run": {"cmd": [PY] + proj["test"]}, "inputs": ["{w}/" + proj["fix"][0]],
                 "outputs": [], "uses": ["inputs"], "needs": []},
                {"id": "gate", "run": {"cmd": [PY, "-c", gate, "{w}"]}, "outputs": [], "uses": ["inputs"],
                 "inputs": ["{w}/" + proj["fix"][0]], "needs": ["test"]},
                {"id": "build", "run": {"cmd": [PY, "-c", pack, "{w}"]}, "outputs": ["{w}/out/build.zip"],
                 "uses": [], "needs": ["gate"]},
            ]}
    write(w / "sample.yaml", yaml.safe_dump(spec))
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "recipe")
    return w, spec


def fix_rung(w, proj, log):
    """The ladder's fix rung, deterministic here: a fix is a commit on a fix/ branch (never main, never by hand)."""
    def fixer(prompt, cwd=None):
        log.append(prompt)
        git(w, "checkout", "-q", "-B", "fix/emerge-sample-test")
        write(w / proj["fix"][0], proj["fix"][1])
        git(w, "add", "-A")
        git(w, "commit", "-q", "-m", "fix: the failing stage")
    return fixer


POLICY = {"enabled": True, "kinds": ["retry"], "ledger": None}


@pytest.mark.parametrize("name,proj", [("code", CODE), ("doc", DOC)])
def test_the_whole_loop_runs_with_nobody_there(tmp_path, stops, name, proj):
    w, spec = sample(tmp_path, name, proj)
    rec = R.Recipe(spec, source=w / "sample.yaml", state_root=tmp_path / "state", strict=False)
    prompts = []
    em = E.Emerge(rec, fixer=fix_rung(w, proj, prompts), inbox_add=stops.inbox, say=lambda m: None, guard=False,
                  checks=[("disk", "ok", "fine")], who="ci", household={"me": "ci", "devices": []})
    plan = em.plan()
    assert plan, "emerge plan returned nothing"
    rep = em.run()
    assert not stops.seen, f"the loop stopped for a person: {stops.seen}"
    assert rep["ok"], f"the loop did not finish: {[(s['id'], s['status']) for s in rep['stages']]}"
    assert len(prompts) == 1 and rep["fixes"][0]["ok"], "the failing stage was not fixed by one fix-rung commit"
    assert (w / "out" / "build.zip").is_file(), "the build produced nothing"

    # auto-accept: the checks passed and a reviewer above the author said yes -> accepted, no owner click
    pol = dict(POLICY, ledger=str(tmp_path / "aa.jsonl"))
    d = autoaccept.decide("retry", f"sample-{name}/test", "haiku",
                          [("stage test", True, "exit 0"), ("gate", True, "clean")], "accept the fix?",
                          evidence=git(w, "log", "-1", "--format=%H"), pol=pol, reviewer="sonnet",
                          asker=lambda m, p: {"yes": True, "why": "VERDICT: YES"})
    assert d["accepted"] and not d.get("wait_owner"), f"auto-accept waited for the owner: {d['why']}"


@pytest.mark.parametrize("here", ["linux", "windows"])
def test_no_command_refuses_on_either_system(here):
    """Every single-system command, asked on the other system: dispatched, offloaded or not-this-platform with a
    written reason. None is a refusal, none is left unwritten."""
    index = platform_gate.load_index(local=False)
    rows = crossos.load()
    stops = []
    for key, row in sorted(index.items()):
        platforms = row["platforms"]
        if here in platforms or (here == "linux" and "debian" in platforms):
            continue
        r = crossos.decide(key.split(), index=index, rows=rows, here=here)
        if r["action"] not in ("run", "dispatch", "offload", "not-this-platform") or r.get("unwritten"):
            stops.append(f"{key} on {here}: {r['action']}{' (no crossos.yaml row)' if r.get('unwritten') else ''}")
        elif r["action"] == "not-this-platform" and not r.get("why"):
            stops.append(f"{key} on {here}: not-this-platform with no reason")
    assert not stops, "commands that would stop a job: " + "; ".join(stops)


def test_no_recipe_is_pinned_or_single_system():
    bad = [f"{kind} {where}: {why}" for kind, where, why in lint.lint_all()]
    assert not bad, "recipes that are not general: " + "; ".join(bad)
