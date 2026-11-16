"""v0.1.164 (2026-11-16): a release created empty made its own install checks fail, and the mandate's CI proof then
refused to let the files be added. Three fixes, each proven here."""
import json

from fieldkit.mandate import proofs
from fieldkit.method import ship


class R:
    def __init__(self, out="", rc=0):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def _ok(*a, **k):
    return ("allow", "ok", None)


def test_a_release_is_never_created_without_files(tmp_path, monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    (tmp_path / "page.md").write_text("p", encoding="utf-8")
    called = []
    r = ship.gh_release(str(tmp_path), "v1", ["--notes-file", "page.md"], mandate=_ok,
                        run=lambda *a, **k: called.append(a), gate=lambda s, t, h: {"ok": True})
    assert not r["ok"] and r["state"] == "bad-input" and "never created empty" in r["error"] and not called


def test_a_release_is_never_created_without_its_page(tmp_path, monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    (tmp_path / "app.bin").write_bytes(b"x")
    called = []
    r = ship.gh_release(str(tmp_path), "v1", ["app.bin"], mandate=_ok,
                        run=lambda *a, **k: called.append(a), gate=lambda s, t, h: {"ok": True})
    assert not r["ok"] and "without its page" in r["error"] and not called


def test_a_missing_file_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    (tmp_path / "page.md").write_text("p", encoding="utf-8")
    r = ship.gh_release(str(tmp_path), "v1", ["gone.bin", "-F", "page.md"], mandate=_ok,
                        run=lambda *a, **k: None, gate=lambda s, t, h: {"ok": True})
    assert not r["ok"] and "gone.bin" in r["error"]


def test_files_and_page_go_through(tmp_path, monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    (tmp_path / "app.bin").write_bytes(b"x")
    (tmp_path / "page.md").write_text("p", encoding="utf-8")
    seen = []
    r = ship.gh_release(str(tmp_path), "v1", ["app.bin", "--title", "v1", "--notes-file", "page.md"], mandate=_ok,
                        run=lambda argv, **k: seen.append(argv) or R("https://x"), gate=lambda s, t, h: {"ok": True})
    assert r["ok"] and seen[0][:3] == ["gh", "release", "create"]


def _gh(check_runs, release_suites):
    def run(argv, **k):
        url = argv[2]
        if "check-runs" in url:
            return R(json.dumps(check_runs))
        if "event=release" in url:
            return R(json.dumps(release_suites))
        raise AssertionError(argv)
    return run


def test_release_event_checks_do_not_block_the_commit(monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    runs = [{"name": "build-test", "status": "completed", "conclusion": "success", "suite": 1},
            {"name": "deb-ubuntu", "status": "completed", "conclusion": "failure", "suite": 9}]
    ok, why = proofs.ci_green(".", "abc", {}, run=_gh(runs, [9]))
    assert ok, why


def test_a_failing_commit_check_still_blocks(monkeypatch):
    """Mutation guard: the exclusion takes only release-event suites, never the commit's own failures."""
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    runs = [{"name": "build-test", "status": "completed", "conclusion": "failure", "suite": 1},
            {"name": "deb-ubuntu", "status": "completed", "conclusion": "failure", "suite": 9}]
    ok, why = proofs.ci_green(".", "abc", {}, run=_gh(runs, [9]))
    assert not ok and "build-test" in why and "deb-ubuntu" not in why


def test_only_release_checks_is_no_ci(monkeypatch):
    monkeypatch.setattr("fieldkit.core.sync._slug", lambda repo: "o/r")
    runs = [{"name": "deb-ubuntu", "status": "completed", "conclusion": "success", "suite": 9}]
    ok, why = proofs.ci_green(".", "abc", {}, run=_gh(runs, [9]))
    assert not ok and "no CI run" in why


def test_failed_release_checks_are_rerun_after_files_are_added():
    calls = []

    def run(argv, **k):
        calls.append(argv)
        if argv[1] == "api":
            return R("[11, 12]")
        return R()
    assert ship.rerun_release_checks("o/r", "v1", run) == [11, 12]
    assert ["gh", "run", "rerun", "11", "--failed", "-R", "o/r"] in calls
