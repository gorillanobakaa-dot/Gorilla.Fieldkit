"""exam: fixture is fixed, graders are right both ways, tools behave, the loop records honestly."""
import hashlib

import pytest

from fieldkit.exam import fixture, runner, tasks
from fieldkit.exam.tools import PFIND, Toolbox

needs_pfind = pytest.mark.skipif(not PFIND.is_file(), reason="pfind not gathered: fieldkit gather --only pfind")


def _digest(root):
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + p.read_bytes())
    return h.hexdigest()


def test_fixture_is_deterministic(tmp_path):
    a = fixture.build(tmp_path / "a")
    b = fixture.build(tmp_path / "b")
    assert _digest(a) == _digest(b)
    assert (a / fixture.SNIPPET_FILE).read_bytes().count(b"\r\n") > 5          # really a Windows file
    for m in fixture.MISSING_FROM_MANIFEST:
        assert m in (a / "MANIFEST.txt").read_text() and not (a / m).exists()


LINE = fixture.definition_line()


@pytest.mark.parametrize("task,answer,ok", [
    ("locate", f"src/build/kernel_tools.py:{LINE}", True),
    ("locate", f"It is defined in src\\build\\kernel_tools.py at line {LINE}.", True),
    ("locate", "src/cli.py line 4", False),
    ("locate", f"src/build/kernel_tools.py line {LINE + 1}", False),
    ("trap", "rebalance_cache is not defined anywhere in this project.", True),
    ("trap", "It does not exist; only docs mention a plan for rebalance_caches.", True),
    ("trap", "src/cache.py line 4", False),
    ("trap", "It is defined in src/cache.py", False),
    ("build-log", "libdw-dev is missing: sudo apt-get install libdw-dev", True),
    ("build-log", "Missing libdw-dev.", False),
    ("build-log", "the compiler differs; reinstall gcc with apt install gcc", False),
    ("snippet", "src/win/profile_loader.py", True),
    ("snippet", "src/cache.py", False),
    ("manifest", "assets/icons/gorilla-256.png, docs/INSTALL.md, src/net/hotspot.py", True),
    ("manifest", "docs/INSTALL.md and src/net/hotspot.py", False),
    ("manifest", "assets/icons/gorilla-256.png, docs/INSTALL.md, src/net/hotspot.py, README.md", False),
])
def test_graders(task, answer, ok):
    assert tasks.BY_ID[task].grade(answer)[0] is ok


def test_final_answer_extraction():
    assert tasks.final_answer("thinking...\nANSWER: src/x.py:3") == ("src/x.py:3", True)
    assert tasks.final_answer("no marker here") == ("no marker here", False)


@pytest.fixture
def box(tmp_path):
    return Toolbox(fixture.build(tmp_path / "p"), kit=True)


def test_raw_tools(box):
    assert "src/" in box.list_dir(".") and "MANIFEST.txt" in box.list_dir(".")
    assert f"{LINE}: def apply_fragment" in box.read_file(fixture.DEFINED_IN, 1)
    hits = box.search_text("apply_fragment")
    assert "src/build/kernel_tools.py:" in hits and "src/cli.py:" in hits
    assert "more lines" in box.read_file("logs/build-7.1.2.log")                # paged, not dumped
    assert box.dispatch("read_file", {"path": "../../etc/passwd"}).startswith("ERROR")   # confined


@needs_pfind
def test_kit_find_points_at_the_definition(box):
    for q in ("apply_fragment", "def apply_fragment", "apply_fragment()"):
        out = box.find(q)
        assert out.startswith(f"DEFINED: `apply_fragment` is defined at {fixture.DEFINED_IN}:{LINE}"), out
        assert f"NEXT: answer {fixture.DEFINED_IN}:{LINE}." in out and len(out.splitlines()) <= 6


@needs_pfind
def test_kit_find_snippet_matches_crlf_file(box):
    assert fixture.SNIPPET_FILE in box.find(fixture.SNIPPET)


def test_kit_triage_names_the_package(box):
    out = box.triage("logs/build-7.1.2.log")
    assert "unmet-build-deps" in out and "libdw-dev" in out


def test_raw_toolbox_refuses_kit_tools(tmp_path):
    raw = Toolbox(fixture.build(tmp_path / "p"), kit=False)
    assert raw.dispatch("find", {"query": "x"}).startswith("ERROR: no tool named")
    assert raw.dispatch("refcheck", {"list_path": "MANIFEST.txt"}).startswith("ERROR: no tool named")
    assert {t["function"]["name"] for t in raw.schema} == {"list_dir", "read_file", "search_text"}


def test_loop_records_a_scripted_model(monkeypatch):
    """A fake model: one tool call, then an answer. The loop must execute and grade it."""
    script = [
        {"content": "", "tool_calls": [{"id": "1", "name": "search_text", "arguments": {"text": "def apply_fragment"}}],
         "malformed": 0, "prompt_tokens": 100, "completion_tokens": 10},
        {"content": f"ANSWER: {fixture.DEFINED_IN}:{LINE}", "tool_calls": [], "malformed": 0,
         "prompt_tokens": 150, "completion_tokens": 12},
    ]
    monkeypatch.setattr(runner, "chat", lambda *a, **k: script.pop(0))
    rec = runner.run_task(tasks.BY_ID["locate"], "fake", kit=False)
    assert rec["passed"] and rec["rounds"] == 2 and rec["prompt_tokens"] == 250
    assert rec["tool_calls"][0]["name"] == "search_text" and rec["answer_format"]


def test_loop_truncation_is_never_a_pass(monkeypatch):
    call = {"content": "ANSWER: src/build/kernel_tools.py:1", "malformed": 0, "prompt_tokens": 1, "completion_tokens": 1,
            "tool_calls": [{"id": "x", "name": "list_dir", "arguments": {"path": "."}}]}
    monkeypatch.setattr(runner, "chat", lambda *a, **k: dict(call))
    rec = runner.run_task(tasks.BY_ID["locate"], "fake", kit=False, max_rounds=3)
    assert rec["truncated"] and not rec["passed"] and rec["why"] == "no final answer"


@needs_pfind
def test_kit_find_never_points_at_a_lookalike(box):
    """2026-09-29: the hint once named `def test_apply_fragment` as the definition."""
    assert "tests/test_kernel_tools.py" not in box.find("apply_fragment")


@needs_pfind
def test_kit_find_near_miss_is_not_an_answer(box):
    """2026-09-29: find listed rebalance_caches for rebalance_cache and Gemma answered docs/DESIGN.md:3."""
    out = box.find("rebalance_cache")
    assert out.startswith("NOT DEFINED: no file defines or mentions `rebalance_cache`.")
    assert "rebalance_caches (docs/DESIGN.md:3)" in out and "does not exist in this project" in out


@needs_pfind
def test_kit_find_mentioned_but_not_defined(box):
    out = box.find("clear_cache")                         # defined in src/cache.py
    assert out.startswith("DEFINED: `clear_cache` is defined at src/cache.py:4")
    (box.root / "docs" / "X.md").write_text("call `ghost_fn` here\n")
    assert box.find("ghost_fn").startswith("NOT DEFINED: `ghost_fn` is mentioned (docs/X.md:1)")


@needs_pfind
def test_kit_find_text_and_nothing(box):
    assert box.find("zz no such phrase qq").startswith("NOT FOUND")
    assert box.find("Unmet build dependencies").startswith("1 file(s) matched")


@needs_pfind
def test_kit_find_given_a_list_of_paths_checks_each(box):
    """2026-09-29 round 2: Gemma pasted all 12 manifest paths into find, got NOT FOUND, called all missing."""
    names = (box.root / "MANIFEST.txt").read_text().split()
    out = box.find(" ".join(names))
    assert out.startswith("That is a list of 12 file paths, so each was checked: 9 exist, 3 missing.")
    for m in fixture.MISSING_FROM_MANIFEST:
        assert f"- missing: {m}" in out
    assert "- missing: README.md" not in out
    assert box.find("src/cli.py, src/cache.py").startswith("That is a list of 2 file paths")
    assert box.find("apply_fragment").startswith("DEFINED")            # names still go to the name search


def test_kit_refcheck_lists_exactly_the_missing(box):
    out = box.refcheck("MANIFEST.txt")
    assert out.splitlines()[0] == "3 of 12 missing (manifest):"
    for m in fixture.MISSING_FROM_MANIFEST:
        assert f"- {m}" in out


def test_kit_find_without_pfind_says_what_to_run(box, monkeypatch, tmp_path):
    from fieldkit.exam import tools
    monkeypatch.setattr(tools, "PFIND", tmp_path / "missing" / "pfind.py")
    assert "fieldkit gather --only pfind" in box.find("apply_fragment")
