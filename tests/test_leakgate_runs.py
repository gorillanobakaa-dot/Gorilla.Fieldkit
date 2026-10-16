# Version: 1.0.0 · updated 26-10-16-17-20
"""leakgate-runs keeps every run that anything cites and calls only the uncited ones free (2026-10-16: runs chosen
by date included the baseline's source, a PASS cited by release notes and the evidence behind an allow entry)."""
import json

from fieldkit.leakgate import runs


def _run(d, stamp, final="FAIL", build="20261004224302", size=10):
    r = d / stamp
    r.mkdir(parents=True)
    (r / "test-results.json").write_text(json.dumps({"FINAL_RESULT": final}))
    (r / "build-manifest.json").write_text(json.dumps({"BUILD": build}))
    (r / "blob.pcapng").write_bytes(b"x" * size)
    return r


def test_cited_baseline_record_and_pass_are_kept_and_only_the_rest_is_free(tmp_path):
    task = tmp_path / "leakgate" / "firefox-x"
    owner = tmp_path / "owner"
    (owner / "leakgate").mkdir(parents=True)
    (owner / "state").mkdir()
    old_free = _run(task, "20261002-145502")
    _run(task, "20261004-042237")                       # cited by an allow entry
    _run(task, "20261006-041341")                       # the baseline's source
    _run(task, "20261006-100814", final="PASS")        # a PASS
    newest = _run(task, "20261010-083818")              # the record's last gate, and the newest
    (owner / "leakgate" / "allow.json").write_text('[{"purpose": "OBSERVED in leakgate run 20261004-042237"}]')
    (owner / "leakgate" / "baseline.json").write_text(json.dumps({"from_run": "20261006-041341"}))
    (owner / "state" / "leakgate_result.json").write_text(json.dumps({"artifacts": str(newest)}))
    rows = {r["stamp"]: r for r in runs.runs(task, owner)}
    assert rows["20261002-145502"]["why_kept"] == []
    assert any("cited" in w for w in rows["20261004-042237"]["why_kept"])
    assert any("baseline" in w for w in rows["20261006-041341"]["why_kept"])
    assert "a PASS" in rows["20261006-100814"]["why_kept"]
    assert any("record" in w for w in rows["20261010-083818"]["why_kept"])
    text = "\n".join(runs.lines(list(rows.values()), lambda b: f"{b} B"))
    assert "1 run(s) free to remove" in text and str(old_free) in text


def test_a_run_citing_itself_inside_its_own_folder_does_not_count(tmp_path):
    task = tmp_path / "leakgate" / "t"
    r = _run(task, "20261002-145502")
    (r / "notes.md").write_text("this is run 20261002-145502")
    _run(task, "20261010-083818")
    rows = {x["stamp"]: x for x in runs.runs(task, None)}
    assert rows["20261002-145502"]["why_kept"] == [], rows["20261002-145502"]
