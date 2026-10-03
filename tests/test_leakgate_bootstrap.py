"""First regression baseline (owner decision 2026-10-02): only from a release run that failed on nothing else."""
from fieldkit.leakgate import gate

WHY = {"REGRESSION_POLICY": [gate.NO_BASELINE + ": seed it"]}


def _res(**over):
    r = {p: "PASS" for p in gate.POLICIES}
    r.update(release_run=True, WHY={})
    r.update(over)
    return r


def test_only_the_missing_baseline_failed_may_seed_it(tmp_path):
    assert gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", WHY=WHY), tmp_path)[0]


def test_the_old_message_of_a_run_already_in_progress_is_recognised(tmp_path):
    old = {"REGRESSION_POLICY": [gate.NO_BASELINE + ": run leakgate-baseline after the first approved release PASS"]}
    assert gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", WHY=old), tmp_path)[0]


def test_any_other_failure_refuses(tmp_path):
    ok, why = gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", NETWORK_POLICY="FAIL", WHY=WHY), tmp_path)
    assert not ok and "NETWORK_POLICY" in why


def test_a_quick_run_refuses(tmp_path):
    assert not gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", WHY=WHY, release_run=False), tmp_path)[0]


def test_a_real_regression_refuses(tmp_path):
    why = {"REGRESSION_POLICY": ["example.org: not in the baseline of 157.0"]}
    assert not gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", WHY=why), tmp_path)[0]


def test_an_existing_baseline_is_never_replaced_this_way(tmp_path):
    (tmp_path / "leakgate").mkdir()
    (tmp_path / "leakgate" / "baseline.json").write_text("{}", encoding="utf-8")
    assert not gate.bootstrap_eligible(_res(REGRESSION_POLICY="FAIL", WHY=WHY), tmp_path)[0]


def test_save_baseline_marks_a_bootstrap(tmp_path):
    run = tmp_path / "run"
    (run / "runs").mkdir(parents=True)
    (run / "runs" / "mitm-x.jsonl").write_text('{"kind": "http", "host": "a.example"}\n', encoding="utf-8")
    owner = tmp_path / "owner"
    (owner / "leakgate").mkdir(parents=True)
    import json
    p = gate.save_baseline(run, owner, "157.0", bootstrap=True)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["bootstrap"] is True and d["request_counts"] == {"a.example": 1}
