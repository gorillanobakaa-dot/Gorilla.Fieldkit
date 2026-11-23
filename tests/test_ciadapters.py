"""CI runner adapters (fieldkit/method/ciadapters.py, CI-SPEED-PLAN C8): fake collect and durations outputs."""
import json
import shlex

import pytest

from fieldkit.method import ciadapters as ad
from fieldkit.method import ciplan

CFG = ciplan.load_config(".github/ci.yaml")

GO_LIST = "TestA\nTestB\nok  \texample.com/x/a\t0.01s\nTestC\nExampleD\nok  \texample.com/x/b\t0.02s\n?   \texample.com/x/c\t[no test files]\n"
GO_JSON = "\n".join(json.dumps(e) for e in [
    {"Action": "pass", "Package": "example.com/x/a", "Test": "TestA", "Elapsed": 100.0},
    {"Action": "pass", "Package": "example.com/x/a", "Test": "TestA/sub", "Elapsed": 50.0},
    {"Action": "pass", "Package": "example.com/x/a", "Test": "TestB", "Elapsed": 100.0},
    {"Action": "pass", "Package": "example.com/x/b", "Test": "TestC", "Elapsed": 100.0},
    {"Action": "pass", "Package": "example.com/x/b", "Test": "ExampleD", "Elapsed": 100.0},
    {"Action": "pass", "Package": "example.com/x/b", "Elapsed": 1.0}])
PYTEST_LIST = "tests/test_a.py::test_1\ntests/test_a.py::K::test_2\ntests/test_b.py::test_3\n\n3 tests collected in 0.1s\n"
JUNIT = ('<testsuites><testsuite><testcase classname="tests.test_a" name="test_1" file="tests/test_a.py" time="4"/>'
         '<testcase classname="tests.test_b" name="test_3" file="tests/test_b.py" time="4"/></testsuite></testsuites>')
FF_LIST = json.dumps({"tests": [{"path": "dom/a/test_x.html"}, {"path": "dom/a/test_y.html"}, {"path": "gfx/test_z.html"}]})
FF_LOG = "\n".join(json.dumps(e) for e in [
    {"action": "test_start", "test": "dom/a/test_x.html", "time": 1000},
    {"action": "test_end", "test": "dom/a/test_x.html", "time": 61000},
    {"action": "test_start", "test": "gfx/test_z.html", "time": 2000},
    {"action": "test_end", "test": "gfx/test_z.html", "time": 32000}])


def test_go_collect_slice_and_durations():
    g = ad.ADAPTERS["go"]
    units = g.collect(GO_LIST)
    assert units == ["example.com/x/a::TestA", "example.com/x/a::TestB", "example.com/x/b::ExampleD", "example.com/x/b::TestC"]
    assert g.durations(GO_JSON)["example.com/x/a::TestA"] == 100.0 and len(g.durations(GO_JSON)) == 4
    cmds = g.slice(units[:3])
    assert cmds == [["go", "test", "example.com/x/a", "-run", "^(TestA|TestB)$"],
                    ["go", "test", "example.com/x/b", "-run", "^(ExampleD)$"]]


def test_firefox_list_replaces_this_chunk_and_reads_mozlog():
    f = ad.ADAPTERS["firefox"]
    units = f.collect(FF_LIST)
    assert units == ["dom/a/test_x.html", "dom/a/test_y.html", "gfx/test_z.html"]
    assert f.collect("# c\nb/t.html\na/t.html\n") == ["a/t.html", "b/t.html"]
    assert f.durations(FF_LOG) == {"dom/a/test_x.html": 60.0, "gfx/test_z.html": 30.0}
    assert f.slice(units[:2]) == [["./mach", "test", "dom/a/test_x.html", "dom/a/test_y.html"]]
    assert "--this-chunk" not in " ".join(f.slice(units)[0])


def test_kernel_is_one_build_unit_and_no_tests():
    k = ad.ADAPTERS["kernel"]
    assert k.collect("") == ["kernel-build"] and k.durations("x") == {} and k.slice(["kernel-build"]) == [["build"]]
    pl, bad = ad.plan(k, k.collect(""), None, CFG)
    assert bad == [] and pl["linux"]["n"] == 1


@pytest.mark.parametrize("name,collect,raw", [("go", GO_LIST, GO_JSON), ("firefox", FF_LIST, FF_LOG),
                                                ("pytest", PYTEST_LIST, JUNIT)])
def test_every_adapter_passes_the_same_c2_checks(name, collect, raw):
    a = ad.ADAPTERS[name]
    units = a.collect(collect)
    pl, bad = ad.plan(a, units, raw, CFG)
    assert bad == []
    for o, p in pl.items():
        assert sorted(u for s in p["shards"] for u in s) == units          # union = selection
        if p["n"] > 1 and not p["single"]:
            assert p["heaviest"] <= p["lightest"] * float(CFG["max_spread"]) + p["granule"]


def test_the_checks_catch_a_dropped_unit_and_an_over_budget_slot_plan():
    g = ad.ADAPTERS["go"]
    units = g.collect(GO_LIST)
    pl, _ = ad.plan(g, units, GO_JSON, CFG)
    pl["linux"]["shards"][0] = pl["linux"]["shards"][0][1:]
    assert any("do not cover" in p for p in ciplan.check(units, pl, CFG))
    bad_cfg = json.loads(json.dumps(CFG))
    bad_cfg["slots"]["account_limit"] = 5
    assert any("exceed the account limit" in p for p in ad.plan(g, units, GO_JSON, bad_cfg)[1])


def test_heavy_go_units_split_into_balanced_shards():
    g = ad.ADAPTERS["go"]
    units = [f"p/{i}::Test{i}" for i in range(40)]
    raw = "\n".join(json.dumps({"Action": "pass", "Package": f"p/{i}", "Test": f"Test{i}", "Elapsed": 30.0}) for i in range(40))
    pl, bad = ad.plan(g, units, raw, CFG)
    assert bad == [] and pl["linux"]["n"] > 1 and pl["linux"]["heaviest"] / pl["linux"]["lightest"] <= 1.25


def test_pytest_adapter_is_byte_identical_to_the_current_plan(tmp_path, capsys):
    a = ad.ADAPTERS["pytest"]
    units = a.collect(PYTEST_LIST)
    assert units == ["tests/test_a.py", "tests/test_b.py"]
    blk = ad.blocks(a, units, JUNIT)
    direct = ciplan.plan(units, blk, CFG)
    pl, _ = ad.plan(a, units, JUNIT, CFG)
    assert json.dumps(pl, sort_keys=True) == json.dumps(direct, sort_keys=True)
    # no --adapter: the old path, untouched (selection JSON in, the same describe() text out)
    (tmp_path / "tests").mkdir()
    for u in units:
        (tmp_path / u).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / u).write_text("", encoding="utf-8")
    sel = tmp_path / "sel.json"
    sel.write_text(json.dumps({"tests": units, "durations": blk}), encoding="utf-8")
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert ciplan.main(["plan", "--selection", str(sel), "--config", str(ROOT_CI)]) == 0
    finally:
        os.chdir(cwd)
    assert capsys.readouterr().out == ciplan.describe(ciplan.plan(units, blk, CFG)) + "\n"


ROOT_CI = __import__("pathlib").Path(__file__).resolve().parents[1] / ".github" / "ci.yaml"


def test_cli_adapter_prints_the_slice_and_checks(tmp_path, capsys):
    c = tmp_path / "list.txt"
    c.write_text(GO_LIST, encoding="utf-8")
    d = tmp_path / "j.json"
    d.write_text(GO_JSON, encoding="utf-8")
    assert ciplan.main(["plan", "--adapter", "go", "--collect", str(c), "--raw-durations", str(d), "--check"]) == 0
    out = capsys.readouterr().out
    assert "adapter go: 4 units" in out and "plan check ok" in out
    n = ciplan.plan(ad.ADAPTERS["go"].collect(GO_LIST), ad.blocks(ad.ADAPTERS["go"], ad.ADAPTERS["go"].collect(GO_LIST), GO_JSON), CFG)["linux"]["n"]
    assert ciplan.main(["plan", "--adapter", "go", "--collect", str(c), "--raw-durations", str(d),
                        "--os", "linux", "--slice", f"1/{n}"]) == 0
    assert "go test" in capsys.readouterr().out
    assert ciplan.main(["plan", "--adapter", "go"]) == 2
    assert shlex.split("go test p -run '^(A|B)$'")[-1] == "^(A|B)$"
