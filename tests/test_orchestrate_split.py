# Version: 1.0.0 · updated 26-10-28-20-22
"""Plan item I6 (2026-10-28): the inventory joins the household list and each device's `load`; the suite is split
into pieces with a PASS-line acceptance, placed on the devices that can run them, and run as a queue. Nothing here
reaches the real cable: reach, load, the local run and the remote run are all fakes."""
import pytest

from fieldkit import orchestrate_load as L
from fieldkit import orchestrate_split as S


def load_row(name, me=False, offered=2, pytest_=True, git=True, os_="LINUX", ok=True):
    return {"name": name, "me": me, "ok": ok, "os": os_, "cores": 8, "ram_total": 16 * 2 ** 30,
            "ram_free": 8 * 2 ** 30, "gate": {"offered": offered, "free": offered},
            "can": {"python": "3.13", "pytest": pytest_, "git": git, "go": True, "model": False}}


REACH = [
    {"tag": "[L15-WIN]", "what": "", "path": "cable", "state": "UP", "device": "win", "detail": "hello"},
    {"tag": "[VAIO-LNX]", "what": "", "path": "cable", "state": "HERE", "device": "", "detail": "this machine"},
    {"tag": "[AIR-MAC]", "what": "", "path": "ssh", "state": "UP", "device": "macair", "detail": "ssh answered"},
    {"tag": "[OLD]", "what": "", "path": "cable", "state": "DOWN", "device": "old", "detail": "no answer"},
]


def inv(loads):
    return {r["tag"]: r for r in S.inventory(reach_fn=lambda: REACH, load_fn=lambda: loads)}


def test_inventory_joins_reach_and_load_by_device_and_says_who_takes_pieces():
    rows = inv([load_row("vaio", me=True, offered=2), load_row("win", offered=1, os_="WINDOWS")])
    assert rows["[L15-WIN]"]["slots"] == 1 and rows["[L15-WIN]"]["load"]["os"] == "WINDOWS"
    assert rows["[VAIO-LNX]"]["slots"] == 2                       # HERE is matched to this machine's own load row
    assert "pytest" in rows["[VAIO-LNX]"]["runs"] and "go" in rows["[VAIO-LNX]"]["runs"]
    assert "model" not in rows["[VAIO-LNX]"]["runs"]
    assert rows["[AIR-MAC]"]["up"] and rows["[AIR-MAC]"]["slots"] == 0 and "SSH-only" in rows["[AIR-MAC]"]["why"]
    assert not rows["[OLD]"]["up"] and rows["[OLD]"]["slots"] == 0 and rows["[OLD]"]["why"] == "no answer"


def test_a_device_up_without_a_load_report_or_without_pytest_takes_no_piece():
    rows = inv([load_row("vaio", me=True, pytest_=False), load_row("win", ok=False)])
    assert rows["[VAIO-LNX]"]["slots"] == 0 and "no pytest" in rows["[VAIO-LNX]"]["why"]
    assert rows["[L15-WIN]"]["slots"] == 0 and "no load report" in rows["[L15-WIN]"]["why"]


def test_a_down_device_never_takes_a_piece_even_with_a_stale_load_row():
    rows = inv([load_row("vaio", me=True), load_row("old")])
    assert rows["[OLD]"]["slots"] == 0


def test_local_model_is_found_by_its_runtime_and_is_false_without_one(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    assert L.local_model(which=lambda n: None, home=tmp_path) is False
    assert L.local_model(which=lambda n: "/usr/bin/ollama" if n == "ollama" else None, home=tmp_path) is True
    (tmp_path / ".lmstudio").mkdir()
    assert L.local_model(which=lambda n: None, home=tmp_path) is True


def make_tree(tmp_path, counts):
    (tmp_path / "tests").mkdir()
    for name, n in counts.items():
        (tmp_path / "tests" / name).write_text("".join(f"def test_{i}():\n    pass\n" for i in range(n)))
    (tmp_path / "tests" / "conftest.py").write_text("")
    (tmp_path / "tests" / "helper.py").write_text("def test_not_collected(): pass\n")
    return tmp_path


def test_split_puts_every_test_file_in_exactly_one_piece_and_balances_by_weight(tmp_path):
    tree = make_tree(tmp_path, {"test_a.py": 9, "test_b.py": 5, "test_c.py": 4, "test_d.py": 3, "test_e.py": 1})
    pieces = S.split(tree, 2)
    files = [f for p in pieces for f in p["files"]]
    assert sorted(files) == [f"tests/test_{x}.py" for x in "abcde"]           # conftest, helper: not pieces
    # weights are def-test count + 1: a10 b6 c5 d4 e2; largest first into the lightest -> {a, d} 14, {b, c, e} 13
    assert [(p["weight"], p["files"]) for p in pieces] == [
        (14, ["tests/test_a.py", "tests/test_d.py"]), (13, ["tests/test_b.py", "tests/test_c.py", "tests/test_e.py"])]
    assert all("PASS line" in p["acceptance"] for p in pieces)


def test_split_never_makes_more_pieces_than_files_and_refuses_an_empty_tree(tmp_path):
    tree = make_tree(tmp_path, {"test_a.py": 1, "test_b.py": 1})
    assert len(S.split(tree, 12)) == 2
    with pytest.raises(ValueError):
        S.split(tmp_path / "nothing", 3, files=[])


@pytest.mark.parametrize("out,ok", [
    ("....\n12 passed in 3.10s\n", True),
    ("12 passed, 3 skipped in 3.10s", True),
    ("FAILED tests/test_a.py::x\n1 failed, 11 passed in 3.10s", False),
    ("11 passed, 1 error in 3.10s", False),
    ("no tests ran in 0.01s", False),
    ("pass on win: exit 0 in 4 s", False),                                    # an exit code alone never accepts
    ("", False),
])
def test_acceptance_is_the_pytest_pass_line_not_the_exit_code(out, ok):
    assert S.accept(out)[0] is ok


def test_plan_deals_largest_first_to_the_least_loaded_slot():
    pieces = [{"id": f"p{i}", "weight": w} for i, w in enumerate([10, 9, 8, 1])]
    devs = [{"device": "vaio", "tag": "", "slots": 2}, {"device": "win", "tag": "", "slots": 1}]
    where = S.plan(pieces, devs)
    # three empty slots take 10, 9, 8 (name order breaks the tie); the 1 goes to the lightest slot, win's (8)
    assert where == {"p0": "vaio", "p1": "vaio", "p2": "win", "p3": "win"}
    assert S.plan(pieces, []) == {}


def test_run_is_a_queue_counts_only_accepted_pieces_and_gives_pieces_per_hour():
    pieces = [{"id": f"p{i:02d}", "files": [f"tests/test_{i}.py"], "weight": 1} for i in range(6)]
    devs = [{"device": "vaio", "tag": "[VAIO-LNX]", "state": "HERE", "slots": 2},
            {"device": "win", "tag": "[L15-WIN]", "state": "UP", "slots": 1}]
    seen = []

    def here(p):
        seen.append(("here", p["id"]))
        return "3 passed in 1.0s", 1.0

    def there(p, dev):
        seen.append((dev, p["id"]))
        return ("pass on win (WINDOWS)\nexit 0\n1 failed, 2 passed in 2.0s" if p["id"] == "p00"
                else "pass on win\n2 passed in 2.0s"), 2.0

    clock = iter([0.0, 1800.0])
    r = S.run(pieces, devs, "tree", "vaio", here=here, there=there, now=lambda: next(clock), say=lambda s: None)
    assert sorted(i for _, i in seen) == [p["id"] for p in pieces]             # each piece taken exactly once
    assert r["of"] == 6 and r["wall_s"] == 1800.0
    failed = [p for p in r["pieces"] if not p["accepted"]]
    assert all(p["device"] == "win" and p["id"] == "p00" for p in failed)
    assert r["finished"] == 6 - len(failed)
    assert r["per_hour"] == pytest.approx(r["finished"] / 0.5)
    assert sum(x["pieces"] for x in r["per_device"].values()) == 6


def test_run_there_fails_closed_without_a_signed_result():
    piece = {"id": "p01", "files": ["tests/test_a.py"]}
    rec = {"status": "started", "job": "run-x", "digest": "d" * 64}
    out, _ = S.run_there(piece, "win", "tree", wait=5, start=lambda *a, **k: rec, wait_fn=lambda j, s: {"status": "pass"},
                         result_fn=lambda *a: None)
    assert not S.accept(out)[0] and "no result signed" in out
    out, _ = S.run_there(piece, "win", "tree", start=lambda *a, **k: {"status": "not-started", "answer": "no mandate"})
    assert not S.accept(out)[0] and "no mandate" in out

    def boom(*a, **k):
        raise OSError("cable down")
    out, _ = S.run_there(piece, "win", "tree", start=boom)
    assert not S.accept(out)[0] and "cable down" in out


def test_run_there_sends_the_piece_as_a_c3_run_job_of_admit_suite():
    got = {}

    def start(argv, on, tree, **k):
        got.update(argv=argv, on=on, tree=tree)
        return {"status": "started", "job": "run-x", "digest": "d"}
    out, _ = S.run_there({"id": "p01", "files": ["tests/test_a.py", "tests/test_b.py"]}, "win", "/t", start=start,
                         wait_fn=lambda j, s: {"status": "pass"},
                         result_fn=lambda *a: {"status": "pass", "host": "w", "os": "WINDOWS",
                                               "evidence": "ok exit 0\n4 passed in 1.2s"})
    assert got == {"argv": ["fieldkit", "admit", "suite", "tests/test_a.py", "tests/test_b.py"], "on": "win",
                   "tree": "/t"}
    assert S.accept(out) == (True, "4 passed in 1.2s")
