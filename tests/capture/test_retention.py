import json
import os
import shutil
import stat
from datetime import datetime, timedelta, timezone

import pytest

from fieldkit.capture import cleanslate, cli, hook, lesson, retention, scan, store, switch


@pytest.fixture
def scratch_area(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    root.mkdir()
    cap = tmp_path / "capture"
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(cap))
    monkeypatch.setattr(cleanslate, "scratch_roots", lambda: [root])
    return root, cap


@pytest.mark.parametrize(("age", "seconds"), [
    ("90s", 90), ("half an hour", 1800), ("30 minutes", 1800), ("1 day", 86400),
    ("3h", 10800), ("2w", 1209600), ("1 month", 2592000), ("3mo", 7776000),
])
def test_duration(age, seconds):
    assert retention.duration(age) == seconds


@pytest.mark.parametrize("age", ["0m", "-1d", "2", "tomorrow", "9e9 days"])
def test_invalid_duration(age):
    with pytest.raises(ValueError):
        retention.duration(age)


def test_cutoff_splits_mixed_tree_and_preserves_checkout(scratch_area):
    root, cap = scratch_area
    job = root / "job"
    checkout = job / "checkout"
    checkout.mkdir(parents=True)
    (checkout / ".git").write_text("gitdir: somewhere", encoding="utf-8")
    old, edge, new = (job / name for name in ("old.py", "edge.py", "new.py"))
    before = 1_760_000_000 * 10**9
    for path, when in ((old, before - 2 * 10**9), (edge, before), (new, before + 2 * 10**9)):
        path.touch()
        os.utime(path, ns=(when, when))
    result = cleanslate.check(before=before, check_store=False,
                              disp={retention.normalized(job): {"because": "copied to recipe"}})
    assert [r["path"] for r in result["wipeable"]] == [str(old)]
    assert {r["path"] for r in result["retained"]} == {str(job), str(checkout), str(edge), str(new)}
    assert not cap.exists()


def test_cli_preview_and_apply_leave_capture_off(scratch_area, monkeypatch, capsys):
    root, cap = scratch_area
    file = root / "disposed.py"
    file.touch()
    cleanslate.record_disposition(file, "adopted")
    switch._write({"on": False})
    snapshot = sorted(p.name for p in cap.iterdir())
    assert cli.main(["clean", "--all", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["check"]["wipeable"][0]["path"] == str(file)
    assert file.exists() and sorted(p.name for p in cap.iterdir()) == snapshot
    monkeypatch.setattr(cleanslate, "recycle", lambda path: path.unlink() is None)
    assert cli.main(["clean", "--all", "--apply", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["recycled"] == [str(file)]
    assert not file.exists() and not switch.is_on()


def test_cleanup_preserves_active_job_and_link(scratch_area, monkeypatch):
    root, _ = scratch_area
    from fieldkit import scratch

    job, _ = scratch.new("live", r=root, owner_pid=os.getpid())
    native_link = retention.link
    monkeypatch.setattr(retention, "link",
                        lambda info: native_link(info) or (stat.S_ISREG(info.st_mode) and info.st_size == 9))
    (root / "reparse").write_bytes(b"x" * 9)
    result = cleanslate.check(check_store=False, disp={})
    assert {r["path"]: r["reason"] for r in result["retained"]} == {
        str(job): "active or kept scratch job", str(root / "reparse"): "link or mount",
    }
    assert not result["wipeable"]


def test_kiro_crew_scratch_requires_inactive_owners_and_disposition(tmp_path, monkeypatch):
    root = tmp_path / ".kiro" / "crew" / "scratch"
    root.mkdir(parents=True)
    monkeypatch.setattr(cleanslate, "_crew_root", lambda: root)
    monkeypatch.setattr(retention, "_crew_owner_alive", lambda pid: pid == 42)
    owners = {
        "aaaaaaaa": "41\n",
        "bbbbbbbb": "41\n42\n",
        "cccccccc": None,
        "dddddddd": "41\n0\n",
        "eeeeeeee": "41\n",
        "ffffffff": "41\n",
        ".quarantine": None,
        "backup-20261105": None,
    }
    for name, owner in owners.items():
        job = root / name
        job.mkdir()
        (job / "scratch.py").touch()
        if owner is not None:
            (job / ".owner").write_text(owner, encoding="utf-8")
    (root / "eeeeeeee" / ".crew-relay").touch()
    (root / "ffffffff" / ".git").mkdir()
    rows = cleanslate.scan_items(roots=[root], protected=[])
    result = cleanslate.check(rows=rows, check_store=False, disp={})
    assert [row["name"] for row in result["scripts"]] == ["aaaaaaaa"]
    receipt = {retention.normalized(root / "aaaaaaaa"): {"because": "adopted"}}
    result = cleanslate.check(rows=rows, check_store=False, disp=receipt)
    assert [row["name"] for row in result["wipeable"]] == ["aaaaaaaa"]
    assert {row["name"]: row["reason"] for row in result["retained"]} == {
        "bbbbbbbb": "active Kiro Crew scratch owner",
        "cccccccc": "unverified Kiro Crew owner",
        "dddddddd": "unverified Kiro Crew owner",
        "eeeeeeee": "contains retained entries",
        "ffffffff": "checkout or worktree",
        ".quarantine": "protected path",
        "backup-20261105": "not a Kiro Crew allocation",
    }
    row = result["wipeable"][0]
    (root / "aaaaaaaa" / ".owner").write_text("42\n", encoding="utf-8")
    assert not cleanslate._unchanged(row, None, cleanslate.keep_list(), [], [root], set(), {}, root)


def test_opencode_shared_temp_selects_only_numbered_test_folders(tmp_path, monkeypatch):
    import re

    temp = tmp_path / "Temp"
    test_job = temp / "opencode-tools-test4294967"
    claude = temp / "claude-scratch"
    for directory in (test_job, claude, temp / "opencode-tools-testing", temp / "toolbox", temp / "review-cache"):
        directory.mkdir(parents=True)
        (directory / "fixture.txt").touch()
    (temp / "opencode-tools-test12").touch()
    monkeypatch.setattr(cleanslate, "_temp_item_roots",
                        lambda: {temp: re.compile(r"opencode-tools-test[0-9]+")})
    rows = cleanslate.scan_items(roots=[temp, claude], protected=[])
    assert {row["path"] for row in rows} == {str(test_job), str(claude / "fixture.txt")}
    receipt = {retention.normalized(test_job): {"because": "disposable test fixture"}}
    result = cleanslate.check(rows=rows, check_store=False, disp=receipt)
    assert [row["path"] for row in result["wipeable"]] == [str(test_job)]


def test_revalidate_and_retry_after_partial_cleanup(scratch_area):
    root, _ = scratch_area
    first, second = root / "a.py", root / "b.py"
    for path in (first, second):
        path.touch()
        cleanslate.record_disposition(path, "adopted")
    switch._write({"on": False})

    def recycle_first(path):
        assert path == first
        second.write_text("changed after inspection", encoding="utf-8")
        path.unlink()
        return True

    partial = cleanslate.clean(apply=True, recycle_fn=recycle_first)
    report = json.loads(open(partial["recovery_report"], encoding="utf-8").read())
    assert report["recycled"] == [str(first)]
    assert report["failure"]["path"] == str(second) and second.exists()
    retried = cleanslate.clean(apply=True, recycle_fn=lambda path: path.unlink() is None)
    assert retried["recycled"] == [str(second)] and not second.exists()


def test_active_capture_refuses_cleanup(scratch_area):
    switch._write({"on": True, "until": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()})
    result = cleanslate.clean(apply=True, recycle_fn=lambda _: pytest.fail("recycle while capture is active"))
    assert result["refused"] == "capture is active"


def test_unknown_agent_blocks_before_any_move(scratch_area):
    root, _ = scratch_area
    result = cleanslate.start("kernel", "build", workspace=root, agents=["kiro"],
                             recycle_fn=lambda _: pytest.fail("recycle with unverified route"))
    assert result["agents"] == {"kiro": "no verified capture adapter"}
    assert not result["started"]


def test_start_verifies_selected_hooks_before_cleanup(scratch_area, tmp_path, monkeypatch, capsys):
    root, cap = scratch_area
    workspace = tmp_path / "kernel"
    workspace.mkdir()
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(switch, "settings_file", lambda agent, home: settings)
    monkeypatch.setattr(switch.shutil, "which", lambda _: "installed")
    monkeypatch.setattr(switch, "schedule_on", lambda: "scheduled")
    script = root / "adopted.py"
    script.touch()
    cleanslate.record_disposition(script, "adopted")
    switch._write({"on": False})
    args = ["start", "--project", "kernel", "--lesson", "Kernel build", "--workspace", str(workspace),
            "--agents", "claude", "--all", "--json"]
    assert cli.main([*args, "--dry-run"]) == 0
    assert script.exists() and settings.read_text(encoding="utf-8") == "{}"
    capsys.readouterr()

    def trash(path):
        configured = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
        assert set(configured) == {"PostToolUse", "PostToolUseFailure", "Stop"}
        shutil.move(path, tmp_path / f"trash-{path.name}")
        return True

    monkeypatch.setattr(cleanslate, "recycle", trash)
    assert cli.main(args) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["recycled"] == [str(script), str(cap)]
    assert run["switch"]["scope"]["workspace"] == str(workspace.resolve())
    assert switch.read()["scope"]["agents"] == ["claude"] and not (cap / "journal.jsonl").exists()


def test_corrupt_archive_blocks_scratch_and_live_recycle(scratch_area, monkeypatch):
    root, cap = scratch_area
    switch._write({"on": False})
    verify = cleanslate.verify_archive

    def corrupt(bundle, *args):
        bundle.write_bytes(b"incomplete")
        return verify(bundle, *args)

    monkeypatch.setattr(cleanslate, "verify_archive", corrupt)
    result = cleanslate.start("kernel", "build", recycle_fn=lambda _: pytest.fail("recycle after invalid archive"))
    assert "archive verification failed" in result["refused"]
    assert cap.exists() and not list((root.parent / "capture-archive").glob("*.manifest.json"))


def test_archive_keeps_dispositions_and_original_dedup_index(scratch_area, monkeypatch):
    root, cap = scratch_area
    switch._write({"on": False})
    (cap / "scan-state.json").write_text("{}", encoding="utf-8")
    kept = root / "kept.py"
    kept.touch()
    cleanslate.record_disposition(kept, "adopted")

    def trash(path):
        shutil.move(path, root.parent / "trash")
        return True

    result = cleanslate.start("kernel", "build", seconds=1209600, recycle_fn=trash, turn_on=lambda *a, **k: {})
    assert result["started"] and kept.exists()
    assert str(retention.normalized(kept)) in cleanslate.dispositions()
    manifest = json.loads(open(result["archive"]["manifest"], encoding="utf-8").read())
    assert any(row["file"] == "scan-state.json" for row in manifest["files"])


def test_scoped_hooks_and_catchup_share_identity(scratch_area, tmp_path):
    _, cap = scratch_area
    workspace = tmp_path / "kernel"
    workspace.mkdir()
    outside = tmp_path / "kernel-other"
    outside.mkdir()
    ts = datetime.now(timezone.utc) - timedelta(minutes=10)
    session = "test-session"
    scope = {"lesson_id": "lesson", "workspace": str(workspace), "agents": ["claude"],
             "since": ts.isoformat(), "until": (ts + timedelta(hours=1)).isoformat(),
             "retrospective_since": ts.isoformat(), "sessions": {"claude": session}}
    s = store.Store(cap)
    payload = {"hook_event_name": "PostToolUse", "cwd": str(workspace), "session_id": session,
               "tool_name": "Bash", "tool_use_id": "first", "tool_input": {"command": "make kernel"},
               "tool_response": {"exit_code": 2}, "timestamp": (ts + timedelta(minutes=1)).isoformat()}
    assert hook.handle("claude", json.dumps(payload), s, scope)
    assert not hook.handle("claude", json.dumps({**payload, "cwd": str(outside)}), s, scope)
    assert not hook.handle("gemini", json.dumps(payload), s, scope)

    transcripts = tmp_path / ".claude" / "projects" / "kernel"
    transcripts.mkdir(parents=True)

    def transcript_call(call, timestamp, cwd, failed=False):
        meta = {"cwd": str(cwd), "timestamp": timestamp.isoformat(), "sessionId": session}
        return [
            {**meta, "type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "id": call, "input": {"command": "make kernel"}}]}},
            {**meta, "type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": call, "content": "done", "is_error": failed}]}},
        ]

    lines = (transcript_call("old", ts - timedelta(seconds=1), workspace)
             + transcript_call("foreign", ts + timedelta(minutes=1), outside)
             + transcript_call("first", ts + timedelta(minutes=1), workspace, failed=True)
             + transcript_call("fix", ts + timedelta(minutes=2), workspace)
             + transcript_call("repeat", ts + timedelta(minutes=3), workspace))
    (transcripts / f"{session}.jsonl").write_text("\n".join(map(json.dumps, lines)), encoding="utf-8")
    result = scan.scan(s, home=tmp_path, scope=scope)
    assert result["calls"] == 2 and result["already"] == 1 and result["out_of_scope"] == 2
    state = s.state()
    assert len(state["events"]) == 3 and len(state["items"]) == 3
    assert dict(state["links"])
    assert {item["lesson_id"] for item in state["items"].values()} == {"lesson"}
    assert scan.scan(s, home=tmp_path, scope=scope)["calls"] == 0
