"""CI shards (fieldkit/method/shard.py, plan P9 item 7): the balance, the junit record, the workflow's shape."""
import importlib.util
from pathlib import Path

import yaml

from fieldkit.method import shard

ROOT = Path(__file__).resolve().parents[1]
WF = yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8"))
DUR = {"tests/a.py": 100.0, "tests/b.py": 90.0, "tests/c.py": 10.0, "tests/d.py": 10.0, "tests/e.py": 5.0, "tests/f.py": 5.0}


def test_every_file_lands_in_exactly_one_shard_and_the_split_is_deterministic():
    files = list(DUR)
    one = shard.balance(files, DUR, 3)
    assert one == shard.balance(list(reversed(files)), DUR, 3)
    assert sorted(f for s in one for f in s) == sorted(files)


def test_the_balance_spreads_heavy_files_apart():
    s = shard.balance(list(DUR), DUR, 2)
    heavy, light = shard.spread(s, DUR)
    assert "tests/a.py" not in s[1] if "tests/b.py" in s[1] else True
    assert heavy - light <= 10                                      # 115 vs 105, not 190 vs 30


def test_a_file_with_no_record_counts_the_median_and_still_runs():
    s = shard.balance(["tests/a.py", "tests/new.py"], {"tests/a.py": 50.0}, 2)
    assert sorted(f for x in s for f in x) == ["tests/a.py", "tests/new.py"]
    assert all(len(x) == 1 for x in s)


def test_pick_is_one_based_and_refuses_out_of_range():
    import pytest
    assert shard.pick(list(DUR), DUR, "1/1") == sorted(DUR)
    with pytest.raises(ValueError):
        shard.pick(list(DUR), DUR, "3/2")


def test_more_shards_than_files_leaves_empty_shards():
    s = shard.balance(["tests/a.py"], {}, 4)
    assert sorted(len(x) for x in s) == [0, 0, 0, 1]


def test_from_junit_sums_per_file(tmp_path):
    x = tmp_path / "j.xml"
    x.write_text('<testsuites><testsuite><testcase classname="tests.test_a" name="t1" time="1.5"/>'
                 '<testcase classname="tests.test_a" name="t2" time="0.5"/>'
                 '<testcase classname="tests.test_b" name="t" time="2"/></testsuite></testsuites>', encoding="utf-8")
    assert shard.from_junit(x) == {"tests/test_a.py": 2.0, "tests/test_b.py": 2.0}


def test_the_workflow_shard_lists_match_the_policy_and_the_required_names_stay():
    n = yaml.safe_load((ROOT / "fieldkit" / "method" / "batch.yaml").read_text(encoding="utf-8"))["batch"]["shards"]["default"]
    for osn in ("windows-latest", "ubuntu-latest"):
        job = WF["jobs"][f"shard-{osn}"]
        assert job["strategy"]["matrix"]["shard"] == list(range(1, n + 1))
        assert f'/{n}"' in "".join(s.get("run", "") for s in job["steps"])
    agg = WF["jobs"]["test"]
    assert agg["name"] == "test (${{ matrix.os }})" and agg["strategy"]["matrix"]["os"] == ["windows-latest", "ubuntu-latest"]
    assert agg["if"] == "always()"                                  # a skipped shard must turn the aggregate red, not skip it


def test_longest_first_reaches_the_even_split():
    d = {f"tests/t{i}.py": float(w) for i, w in enumerate([7, 7, 6, 6, 5, 5, 4, 4, 4])}
    heavy, light = shard.spread(shard.balance(list(d), d, 3), d)
    assert heavy == light == 16.0
