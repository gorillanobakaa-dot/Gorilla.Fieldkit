"""The owner's preflight runs at the end of a port and its blockers become owner steps."""
import subprocess

from fieldkit.buildh import ownercheck, task

SAMPLE = """[*] Gorilla Firefox harness - stage 'preflight'
[*]   [ok  ] Disk space                 665.0 GB free
[*]   [FAIL] Lazy imports resolve       4 undeclared lazy symbol use(s) - run the checker
[-] Objdir vs CLOBBER (BLOCKER): tree CLOBBER is newer than the objdir marker by 1919199s - a clobber is required
[*]       fix : Delete the objdir (or ./mach clobber) and rebuild from scratch.
[*]       why : standard Firefox behaviour
[-] Lazy imports resolve (BLOCKER): 4 undeclared lazy symbol use(s) - run the checker
[*]       fix : Run: python "working scripts/check_lazy_getters.py" and restore the getter in each module it names.
[!] Staged mozconfig options (WARN): 3 staged, not enabled
[*]       fix : Uncomment them and rebuild once.
[!!!] FATAL HALT [!!!]
"""


def test_blockers_are_parsed_with_their_own_fix_text_warnings_ignored():
    b = ownercheck.parse(SAMPLE)
    assert [x["name"] for x in b] == ["Objdir vs CLOBBER", "Lazy imports resolve"]
    assert b[0]["fix"].startswith("Delete the objdir") and b[1]["fix"].startswith("Run: python")
    assert "clobber is required" in b[0]["detail"]


def test_the_step_turns_blockers_into_owner_steps_once(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    root = tmp_path / "owner"
    (root / "harness").mkdir(parents=True)
    (root / "harness" / "gorilla_build.py").write_text("import sys\nsys.stdout.write(open(%r, encoding='utf-8').read())\nsys.exit(1)\n"
                                                        % str(tmp_path / "sample.txt"), encoding="utf-8")
    (tmp_path / "sample.txt").write_text(SAMPLE, encoding="utf-8")
    w = tmp_path / "w"
    w.mkdir()
    task.start("o1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    t = task.load("o1")
    res = ownercheck.step_owner_preflight(t, owner_root=str(root))
    assert res["ok"] and res["blockers"] == ["Objdir vs CLOBBER", "Lazy imports resolve"]
    ids = [s["id"] for s in res["add_steps"]]
    assert ids == ["owner-preflight-objdir-vs-clobber", "owner-preflight-lazy-imports-resolve"]
    assert all(s["kind"] == "owner" and "fix:" in s["title"] for s in res["add_steps"])
    t["steps"] += res["add_steps"]
    assert ownercheck.step_owner_preflight(t, owner_root=str(root))["add_steps"] == []     # not added twice


def test_no_owner_preflight_is_not_an_error(tmp_path):
    rc, text = ownercheck.run_preflight(tmp_path)
    assert rc is None and "no owner preflight" in text
