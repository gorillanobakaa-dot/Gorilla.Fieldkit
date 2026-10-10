"""The methods turned into tools (docs/METHODS.md): wait, mutate, regress, drift, mcp-probe.

Each is shown doing its job on a small real case, and refusing what it must refuse.
"""
import json
import sys
from pathlib import Path

import pytest

from fieldkit.method import drift, mcp_probe, mutate, regress, wait

PY = sys.executable


# -- wait ----------------------------------------------------------------------------------------------------
def test_wait_returns_as_soon_as_the_condition_holds(tmp_path):
    flag = tmp_path / "done"
    calls = []

    def fake_sleep(s):                       # the file appears after the second check
        calls.append(s)
        if len(calls) == 2:
            flag.write_text("x")

    r = wait.wait(path=flag, timeout=60, every=1, sleep=fake_sleep)
    assert r["ok"] and r["tries"] == 3


def test_wait_stops_at_the_deadline_with_the_last_output():
    t = [0.0]
    r = wait.wait([PY, "-c", "print('still building')"], matches="^done", timeout=10, every=4,
                  clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    assert not r["ok"] and "still building" in r["last"] and r["next"]


def test_wait_cli_exit_codes(tmp_path):
    assert wait.main(["--until", f"{PY} -c pass", "--timeout", "5", "--every", "1"]) == 0
    assert wait.main(["--until-file", str(tmp_path / "never"), "--timeout", "0.5", "--every", "1"]) == 3
    assert wait.main(["--until", "x", "--every", "0"]) == 2


# -- mutate --------------------------------------------------------------------------------------------------
def _project(tmp_path, test_body):
    (tmp_path / "rule.py").write_text("def allowed(age):\n    return age >= 18\n", encoding="utf-8")
    (tmp_path / "check.py").write_text("import sys; sys.path.insert(0, '.')\nfrom rule import allowed\n" + test_body,
                                       encoding="utf-8")
    return [PY, "check.py"]


def test_a_test_that_holds_the_rule_catches_the_mutation_and_the_file_is_restored(tmp_path):
    test = _project(tmp_path, "assert allowed(18) and not allowed(17)\n")
    before = (tmp_path / "rule.py").read_bytes()
    r = mutate.mutate([{"file": str(tmp_path / "rule.py"), "swap": [">= 18", "> 18"], "why": "18 is allowed"}],
                      test, cwd=str(tmp_path))
    assert r["ok"] and r["caught"] == 1 and (tmp_path / "rule.py").read_bytes() == before


def test_a_weak_test_lets_the_mutation_survive(tmp_path):
    test = _project(tmp_path, "assert allowed(30)\n")                       # never tests the boundary
    r = mutate.mutate([{"file": str(tmp_path / "rule.py"), "swap": [">= 18", "> 18"], "why": "18 is allowed"}],
                      test, cwd=str(tmp_path))
    assert not r["ok"] and r["survived"] == 1 and "18 is allowed" in r["next"]


def test_mutate_refuses_an_ambiguous_swap_and_a_test_that_already_fails(tmp_path):
    test = _project(tmp_path, "assert allowed(18)\n")
    r = mutate.mutate([{"file": str(tmp_path / "rule.py"), "swap": ["e", "x"]}], test, cwd=str(tmp_path))
    assert r["results"][0]["verdict"] == "refused" and not r["ok"]
    bad = _project(tmp_path, "assert False\n")
    r = mutate.mutate([{"file": str(tmp_path / "rule.py"), "swap": [">= 18", "> 18"]}], bad, cwd=str(tmp_path))
    assert r["baseline"] == "failed"
    assert mutate.main([str(tmp_path / "rule.py"), "--swap", ">= 18", "> 18"]) == 2        # no test after --


# -- regress -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("output,expected", [
    ("PASSED tests/test_a.py::test_one\nFAILED tests/test_a.py::test_two - assert 1", {
        "tests/test_a.py::test_one": "pass", "tests/test_a.py::test_two": "fail"}),
    ("tests/test_a.py::test_one PASSED  [ 50%]\ntests/test_a.py::test_two FAILED [100%]", {
        "tests/test_a.py::test_one": "pass", "tests/test_a.py::test_two": "fail"}),
    ("=== RUN   TestA\n--- PASS: TestA (0.00s)\n--- FAIL: TestB (0.01s)", {"TestA": "pass", "TestB": "fail"}),
    ("test_x (mod.Case) ... ok\ntest_y (mod.Case) ... FAIL", {"test_x (mod.Case)": "pass",
                                                              "test_y (mod.Case)": "fail"}),
    ("  [PASS] counts body only\n  [FAIL] D:\\Dropbox is seen as Dropbox", {
        "counts body only": "pass", "D:\\Dropbox is seen as Dropbox": "fail"}),
])
def test_regress_reads_test_names_in_every_supported_format(output, expected):
    assert regress.parse(output) == expected


def test_regress_names_new_fixed_still_and_vanished(tmp_path, monkeypatch):
    monkeypatch.setattr(regress, "STORE", tmp_path)
    before = {"passed": 3, "failed": 1, "tests": {"a": "pass", "b": "pass", "c": "fail", "gone": "pass"}}
    after = {"passed": 3, "failed": 1, "tests": {"a": "pass", "b": "fail", "c": "pass", "new": "pass"}}
    r = regress.compare(before, after)                                         # same totals, different story
    assert r["new_failures"] == ["b"] and r["fixed"] == ["c"] and r["vanished"] == ["gone"] and not r["ok"]


def test_regress_record_and_compare_through_the_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(regress, "STORE", tmp_path)
    script = tmp_path / "suite.py"
    script.write_text("print('[PASS] one'); print('[FAIL] two')", encoding="utf-8")
    assert regress.main(["record", "before", "--", PY, str(script)]) == 0
    assert regress.main(["record", "after", "--", PY, str(script)]) == 0
    assert regress.main(["compare", "before", "after"]) == 0                    # 'two' was failing already
    script.write_text("print('[FAIL] one'); print('[FAIL] two')", encoding="utf-8")
    assert regress.main(["record", "after", "--", PY, str(script)]) == 0
    assert regress.main(["compare", "before", "after"]) == 3                    # 'one' broke
    assert regress.main(["compare", "before", "nope"]) == 2


# -- drift ---------------------------------------------------------------------------------------------------
def test_drift_names_json_paths_and_never_writes(tmp_path):
    f = tmp_path / "schema.json"
    f.write_text(json.dumps({"models": ["a", "b"], "x": 1}), encoding="utf-8")
    gen = [PY, "-c", "import json; print(json.dumps({'x': 1, 'models': ['a', 'c'], 'new': True}))"]
    before = f.read_bytes()
    r = drift.drift(gen, str(f))
    assert r["drift"] and r["kind"] == "json" and f.read_bytes() == before
    assert any("models[1]" in d for d in r["first"]) and any(d.startswith("+ new") for d in r["first"])
    same = [PY, "-c", "import json; print(json.dumps({'x': 1, 'models': ['a', 'b']}))"]
    assert drift.drift(same, str(f))["drift"] is False                          # key order does not count


def test_drift_compares_text_by_line_and_refuses_a_failing_generator(tmp_path):
    f = tmp_path / "t.md"
    f.write_text("one\ntwo\n", encoding="utf-8")
    assert drift.drift([PY, "-c", "print('one'); print('three')"], str(f))["differences"] == 2
    assert drift.main([str(f), "--gen", f"{PY} -c \"raise SystemExit(4)\""]) == 2


# -- mcp-probe -----------------------------------------------------------------------------------------------
def test_probe_the_real_fieldkit_server(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_RECORDER", str(tmp_path / "rec.jsonl"))
    r = mcp_probe.probe([PY, "-m", "fieldkit", "mcp"], call="describe", args={"tool": "privacy-scan"}, timeout=60,
                        cwd=str(Path(__file__).resolve().parents[1]))
    assert r["ok"], r["problems"]
    assert r["protocol"] == "2024-11-05" and "run" in [t["name"] for t in r["tools"]]
    assert r["call"]["meta"] == {"untrusted": False, "egress": False}
    assert r["tool_list_tokens_est"] < 1500


def test_probe_names_a_server_that_does_not_speak_the_protocol(tmp_path):
    noisy = tmp_path / "noisy.py"
    noisy.write_text(
        "import sys, json\nprint('Starting server...', flush=True)\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if 'id' in m:\n"
        "        r = {'protocolVersion': '1999-01-01'} if m['method'] == 'initialize' else {'tools': []}\n"
        "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}), flush=True)\n", encoding="utf-8")
    r = mcp_probe.probe([PY, str(noisy)], timeout=20)
    assert not r["ok"]
    assert any("1999-01-01" in p for p in r["problems"]) and any("stdout" in p for p in r["problems"])
    silent = mcp_probe.probe([PY, "-c", "import time; time.sleep(30)"], timeout=1)
    assert not silent["ok"] and "no answer to initialize" in silent["problems"][0]


def test_every_method_is_on_the_command_line_and_in_the_catalogue():
    from fieldkit.cli import METHODS
    text = (Path(__file__).resolve().parents[1] / "docs" / "METHODS.md").read_text(encoding="utf-8")
    for name in METHODS:
        assert f"fieldkit {name}" in text, name


# -- office render ----------------------------------------------------------------------------------------------
def test_render_draws_every_page_and_a_contact_sheet(tmp_path):
    pytest.importorskip("pypdfium2")
    from reportlab.pdfgen import canvas
    from fieldkit.office import render
    pdf = tmp_path / "two.pdf"
    c = canvas.Canvas(str(pdf))
    for text in ("first page", "second page"):
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    r = render.render(pdf, tmp_path / "out", scale=0.3, pages=6, sheet=True)
    assert r["pages"] == 2 and len(r["rendered"]) == 2 and Path(r["sheet"]).is_file()
    from PIL import Image
    a, s = Image.open(r["rendered"][0]), Image.open(r["sheet"])
    assert s.width == 2 * a.width                                             # side by side
    with pytest.raises(FileNotFoundError):
        render.render(tmp_path / "missing.docx")


def test_render_needs_libreoffice_for_office_files_and_says_how_to_get_it(tmp_path, monkeypatch):
    from fieldkit.office import render
    monkeypatch.setattr(render, "find_soffice", lambda: None)
    doc = tmp_path / "a.docx"
    doc.write_bytes(b"PK")
    with pytest.raises(LookupError):
        render.render(doc)
    assert "libreoffice" in render.INSTALL["linux"].lower() and "LibreOffice" in render.INSTALL["windows"]


# -- survey ---------------------------------------------------------------------------------------------------
def test_survey_reads_facts_not_guesses(tmp_path):
    from fieldkit.method import survey
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.24.0\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "main_test.go").write_text("package main\nimport \"testing\"\nfunc TestA(t *testing.T) {}\n"
                                           "func TestB(t *testing.T) {}\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x\n" * 50, encoding="utf-8")
    (tmp_path / "LICENSE").write_text("MIT License\n\nPermission is hereby granted, free of charge, to any person",
                                      encoding="utf-8")
    (tmp_path / "notes.py").write_text("PATH = '/home/" + "someone/secret'\n", encoding="utf-8")
    r = survey.survey(tmp_path)
    assert r["main_language"] == "Go" and r["build"]["go"] == {"module": "example.com/x", "go": "1.24.0"}
    assert r["test_functions"]["Go"] == 2 and r["licence"] == "MIT" and "main.go" in r["entry_points"]
    assert r["privacy"]["files"] == 1                                         # the hard-coded home path
    assert survey.main([str(tmp_path / "nope")]) == 2


# -- i18n -------------------------------------------------------------------------------------------------------
def test_i18n_table_complete_unique_and_covered():
    from fieldkit.method import i18n
    good = {"Introduction": ["Introducción", "Einleitung"], "Conclusion": ["Conclusión", "Fazit"]}
    assert i18n.check_table(good, ["es", "de"], keys=["Introduction"]) == []
    clash = {"Contents": ["Índice", "Cuprins"], "Main Body": ["Desarrollo", "Cuprins"]}
    assert any("not unique" in p and "Cuprins" in p for p in i18n.check_table(clash, ["es", "ro"]))
    short = {"Aim": ["Objetivo"]}
    assert any("incomplete" in p for p in i18n.check_table(short, ["es", "de"]))
    assert any("missing" in p for p in i18n.check_table(good, ["es", "de"], keys=["Results"]))


def test_i18n_disjoint_and_the_real_tables(tmp_path):
    from fieldkit.method import i18n
    assert i18n.check_disjoint({"es": {"d": ["este", "los"]}, "ro": {"d": ["este", "și"]}}, "d") == \
        ["shared: 'este' is in es, ro"]
    root = Path(__file__).resolve().parents[1] / "fieldkit" / "academic"
    assert i18n.main(["check", str(root / "headings.yaml"), "--table", "headings", "--langs", "es,pt,it,de,ro"]) == 0
    assert i18n.main(["check", str(root / "languages.yaml"), "--disjoint", "distinctive"]) == 0


# -- cards check / draft -----------------------------------------------------------------------------------------
def test_every_reviewed_card_agrees_with_its_command():
    from fieldkit.desk import cardcheck
    r = cardcheck.check()
    assert r["ok"], r["cards"]
    assert r["checked"] >= 30


def test_card_check_catches_a_renamed_option_and_merged_subcommands():
    from fieldkit.desk import cardcheck
    card = {"id": "x", "entry": ["python", "-m", "fieldkit", "academic", "refs"], "effects": ["reads-files"],
            "safety": "read-only", "inputs": [{"name": "style", "flag": "--styel", "type": "str"}], "tests": []}
    problems, _ = cardcheck.check_card(card)
    assert any("--styel" in p for p in problems)
    merged = dict(card, entry=["python", "-m", "fieldkit", "pipeline"], inputs=[], inputs_basis="read from argparse")
    problems, _ = cardcheck.check_card(merged)
    assert any("subcommands" in p for p in problems)
    lying = dict(card, inputs=[], effects=["writes-files"])
    assert any("read-only" in p for p in cardcheck.check_card(lying)[0])


def test_card_draft_reads_the_argparse_code_and_stays_a_draft(tmp_path):
    import yaml
    from fieldkit.desk import cardcheck
    f = tmp_path / "tool.py"
    f.write_text("import argparse\nap = argparse.ArgumentParser()\nap.add_argument('src')\n"
                 "ap.add_argument('--out', help='where')\na = ap.parse_args()\nopen(a.out, 'w').write('x')\n",
                 encoding="utf-8")
    card = yaml.safe_load(cardcheck.draft(f))[0]
    assert card["draft"] is True and {i["name"] for i in card["inputs"]} >= {"src", "out"}
    assert "writes-files" in card["effects"] and card["safety"] != "read-only"


# -- ship -------------------------------------------------------------------------------------------------------
def _git(repo, *args):
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _remote_and_clone(tmp_path):
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(work))
    for k, v in (("user.name", "T"), ("user.email", "t@example.com")):
        _git(work, "config", k, v)
    (work / "a.txt").write_text("one\n")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-q", "-m", "first")
    _git(work, "push", "-q", "origin", "HEAD:main")
    return remote, work


def test_ship_refuses_the_base_and_pushes_a_new_branch(tmp_path):
    from fieldkit.method import ship
    _, work = _remote_and_clone(tmp_path)
    assert ship.status(work)["state"] == "on-base"
    _git(work, "switch", "-q", "-c", "feature")
    (work / "b.txt").write_text("two\n")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "second")
    r = ship.push(work)
    assert r["ok"] and r["pushed"] and r["state"] == "in-step"


def test_ship_records_a_squash_merged_branch_instead_of_forcing(tmp_path):
    from fieldkit.method import ship
    remote, work = _remote_and_clone(tmp_path)
    _git(work, "switch", "-q", "-c", "feature")
    (work / "b.txt").write_text("two\n")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "feature work")
    _git(work, "push", "-q", "-u", "origin", "feature")
    # the PR is squash-merged on the server: main gets ONE new commit with the same tree
    _git(work, "switch", "-q", "main")
    _git(work, "merge", "-q", "--squash", "feature")
    _git(work, "commit", "-q", "-m", "feature (#1)")
    _git(work, "push", "-q", "origin", "main")
    # follow-up work: the branch restarted from main, a new commit, and the old head still on the remote
    _git(work, "switch", "-q", "-C", "feature", "origin/main")
    (work / "c.txt").write_text("three\n")
    _git(work, "add", "c.txt")
    _git(work, "commit", "-q", "-m", "follow-up")
    assert ship.status(work)["state"] == "squash-merged"
    tree = _git(work, "rev-parse", "HEAD^{tree}")
    r = ship.push(work)
    assert r["ok"] and r["pushed"] and _git(work, "rev-parse", "HEAD^{tree}") == tree
    # nothing was rewritten: the old remote head is still in the history
    old = _git(remote, "rev-parse", "feature^2")                           # the merge is the tip
    assert _git(remote, "cat-file", "-t", old) == "commit"


def test_ship_refuses_real_divergence(tmp_path):
    from fieldkit.method import ship
    _, work = _remote_and_clone(tmp_path)
    _git(work, "switch", "-q", "-c", "feature")
    (work / "b.txt").write_text("two\n")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-q", "-m", "mine")
    _git(work, "push", "-q", "-u", "origin", "feature")
    _git(work, "reset", "-q", "--hard", "HEAD~1")                         # someone else's work is on the remote
    (work / "x.txt").write_text("other\n")
    _git(work, "add", "x.txt")
    _git(work, "commit", "-q", "-m", "different")
    r = ship.push(work)
    assert not r["ok"] and r["state"] == "diverged" and "never force-push" in r["next"]


# -- handover ---------------------------------------------------------------------------------------------------
def test_handover_is_refused_when_the_text_is_not_there_once(tmp_path):
    import yaml
    from fieldkit.method import handover
    target = tmp_path / "theirs"
    target.mkdir()
    (target / "ra.py").write_text('m = re.search(r"\\b(" + Y + r")\\b", c)\n', encoding="utf-8")
    spec = {"title": "One fix", "to": "their Claude",
            "fixes": [{"file": "ra.py", "function": "split", "before": 'r"\\b(" + Y + r")\\b"',
                       "after": 'r"(?<!\\w)(" + Y + r")(?!\\w)"', "why": "n.d. ends in a full stop",
                       "test": "def test_nd():\n    assert True\n"}],
            "not_bugs": ["Windows path tests on Linux"]}
    f = tmp_path / "fixes.yaml"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    out = tmp_path / "h.md"
    assert handover.main([str(f), "--target", str(target), "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "`ra.py`, function `split`" in md and "n.d. ends in a full stop" in md and "## Not bugs" in md
    spec["fixes"][0]["before"] = "not in the file"
    f.write_text(yaml.safe_dump(spec), encoding="utf-8")
    assert handover.main([str(f), "--target", str(target)]) == 3
