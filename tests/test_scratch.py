"""fieldkit scratch: marked jobs, the one delete rule, never outside the root, never through a link (2026-10-26)."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit import scratch

WIN = os.name == "nt"


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "fk"
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(r))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STALE_HOURS", "24")
    return r


def _fill(d, n=30):
    for i in range(n):
        sub = Path(d) / f"s{i % 3}" / "deeper"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"f{i}.txt").write_text("x" * i)


def _rewrite(d, **kw):
    mp = Path(d) / scratch.MARKER
    m = json.loads(mp.read_text(encoding="utf-8"))
    m.update(kw)
    mp.write_text(json.dumps(m), encoding="utf-8")
    return m


DEAD = {"pid": 4_000_000, "ctime": 1.0, "name": "gone", "host": socket.gethostname()}


def test_new_writes_a_marker_naming_job_root_and_owner(root):
    d, m = scratch.new("t1", purpose="unit")
    assert d == root / "t1" and d.is_dir()
    on_disk = json.loads((d / scratch.MARKER).read_text(encoding="utf-8"))
    assert on_disk == m
    assert m["job"] == "t1" and m["root"] == os.path.normcase(os.path.abspath(root)) and m["schema"] == 1
    assert m["owner"] == scratch.whoami()
    with pytest.raises(scratch.Refused):
        scratch.new("t1")
    for bad in ("..", ".hidden", "a/b", "a\\b", "-x", "x" * 81, ""):
        with pytest.raises(scratch.Refused):
            scratch.new(bad) if bad else scratch.new("_x")


def test_generated_job_names_are_unique(root):
    a, _ = scratch.new()
    b, _ = scratch.new()
    assert a != b and a.name.startswith("job-")


def test_clean_mine_deletes_everything_and_logs_one_line(root, tmp_path):
    d, _ = scratch.new("mine")
    _fill(d)
    rows = scratch.clean(mine=True)
    assert [r["job"] for r in rows] == ["mine"] and rows[0]["ok"]
    assert rows[0]["files"] == 30 and rows[0]["rule"] == "mine"
    assert not d.exists() and root.is_dir()
    log = (tmp_path / "state" / "clean.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and json.loads(log[0])["job"] == "mine"


def test_a_live_owner_s_job_is_refused_by_name_and_skipped_by_mine_and_stale(root):
    d, _ = scratch.new("theirs", owner_pid=os.getpid())     # this pytest process: alive, not the caller's session
    me = dict(scratch.whoami(), pid=1, ctime=2.0)           # a caller that is someone else
    assert scratch.classify(scratch.marker(d), me) == "live"
    rows = scratch.clean(["theirs"], me=me)
    assert not rows[0]["ok"] and rows[0]["rule"] == "refused" and d.exists()
    assert scratch.clean(["theirs"], claim=True, me=me)[0]["ok"] is False and d.exists()
    assert scratch.clean(mine=True, me=me) == [] and scratch.clean(stale=True, me=me) == []
    assert d.exists()


def test_stale_means_owner_gone_and_old_enough_and_not_kept(root):
    old = time.time() - 25 * 3600
    a, _ = scratch.new("old")
    _rewrite(a, owner=DEAD, created=old)
    y, _ = scratch.new("young")
    _rewrite(y, owner=DEAD, created=time.time() - 23 * 3600)
    k, _ = scratch.new("kept", keep=True)
    _rewrite(k, owner=DEAD, created=old)
    me = scratch.whoami()
    st = {p.name: scratch.classify(m, me) for p, m, _ in scratch.jobs()}
    assert st == {"old": "stale", "young": "young", "kept": "kept"}
    rows = scratch.clean(stale=True)
    assert [r["job"] for r in rows] == ["old"] and rows[0]["ok"] and rows[0]["rule"].startswith("stale")
    assert not a.exists() and y.exists() and k.exists()
    # named without --claim: refused; with --claim (owner gone): deleted, kept or not
    assert scratch.clean(["kept"])[0]["ok"] is False
    assert scratch.clean(["kept", "young"], claim=True)[0]["ok"]
    assert not k.exists() and not y.exists()


def test_stale_hours_is_the_limit(root, monkeypatch):
    a, _ = scratch.new("edge")
    m = _rewrite(a, owner=DEAD, created=time.time() - 2 * 3600)
    monkeypatch.setenv("FIELDKIT_SCRATCH_STALE_HOURS", "2")
    assert scratch.classify(m, scratch.whoami()) == "stale"
    monkeypatch.setenv("FIELDKIT_SCRATCH_STALE_HOURS", "3")
    assert scratch.classify(m, scratch.whoami()) == "young"


def test_a_link_job_is_a_hand_over_claimable_while_its_receiver_lives(root):
    d, _ = scratch.new("link-0123456789abcdef", kind="link", keep=True, owner_pid=os.getpid())
    me = dict(scratch.whoami(), pid=1, ctime=2.0)
    assert scratch.clean(["link-0123456789abcdef"], me=me)[0]["ok"] is False
    r = scratch.clean(["link-0123456789abcdef"], claim=True, me=me)[0]
    assert r["ok"] and "kind link" in r["rule"] and not d.exists()


def test_unmarked_foreign_and_outside_folders_are_never_touched(root, tmp_path):
    scratch.new("ok")
    (root / "unmarked").mkdir()
    (root / "unmarked" / "keep.txt").write_text("x")
    w, _ = scratch.new("wrongname")
    _rewrite(w, job="other")
    o, _ = scratch.new("otherroot")
    _rewrite(o, root="c:\\elsewhere")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / scratch.MARKER).write_text(json.dumps({"schema": 1, "job": "outside", "root": str(tmp_path)}))
    rows = scratch.clean(["unmarked", "wrongname", "otherroot", str(outside), "../outside", "missing"], claim=True)
    assert all(not r["ok"] and r["rule"] == "refused" for r in rows), rows
    assert (root / "unmarked" / "keep.txt").exists() and w.exists() and o.exists() and outside.exists()
    rep = {x["job"]: x["state"] for x in scratch.report(with_size=False)}
    assert rep["unmarked"] == "UNMARKED" and rep["wrongname"] == "UNMARKED" and rep["ok"] == "mine"


def test_the_marker_is_checked_again_right_before_the_delete(root):
    d, m = scratch.new("race")
    _rewrite(d, owner=DEAD)
    with pytest.raises(scratch.Refused):
        scratch.delete_job(d, m, "mine")
    assert d.exists()


@pytest.mark.skipif(not WIN, reason="junctions are Windows")
def test_a_junction_inside_a_job_is_removed_never_followed(root, tmp_path):
    import _winapi
    target = tmp_path / "precious"
    target.mkdir()
    (target / "owner.txt").write_text("keep me")
    d, _ = scratch.new("withlink")
    _winapi.CreateJunction(str(target), str(d / "j"))
    rows = scratch.clean(["withlink"])
    assert rows[0]["ok"] and rows[0]["links"] == 1
    assert (target / "owner.txt").read_text() == "keep me" and not d.exists()


def test_a_symlink_inside_a_job_is_removed_never_followed(root, tmp_path):
    target = tmp_path / "precious2"
    target.mkdir()
    (target / "owner.txt").write_text("keep me")
    d, _ = scratch.new("withsym")
    try:
        os.symlink(target, d / "s", target_is_directory=True)
        os.symlink(target / "owner.txt", d / "f")
    except OSError:
        pytest.skip("this account may not make symlinks")
    rows = scratch.clean(["withsym"])
    assert rows[0]["ok"] and rows[0]["links"] == 2
    assert (target / "owner.txt").read_text() == "keep me"


@pytest.mark.skipif(not WIN, reason="junctions are Windows")
def test_a_root_or_job_that_is_a_junction_is_refused(tmp_path, monkeypatch):
    import _winapi
    real = tmp_path / "real"
    real.mkdir()
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(real))
    d, _ = scratch.new("inner")
    (d / "f.txt").write_text("x")
    _winapi.CreateJunction(str(real), str(tmp_path / "jroot"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(tmp_path / "jroot"))
    with pytest.raises(scratch.Refused):
        scratch.marker(tmp_path / "jroot" / "inner")
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(real))
    _winapi.CreateJunction(str(d), str(real / "jjob"))
    rows = scratch.clean(["jjob"], claim=True)
    assert rows[0]["ok"] is False and (d / "f.txt").exists()


@pytest.mark.skipif(not WIN, reason="the 260-character limit is Windows")
def test_paths_longer_than_260_and_read_only_files_are_deleted(root):
    import stat
    d, _ = scratch.new("long")
    deep = scratch.long_path(d)
    for i in range(12):
        deep = os.path.join(deep, "d" * 30)
    os.makedirs(deep)
    f = os.path.join(deep, "ro.txt")
    with open(f, "w") as h:
        h.write("x")
    os.chmod(f, stat.S_IREAD)
    assert len(f) > 300
    rows = scratch.clean(["long"])
    assert rows[0]["ok"], rows
    assert rows[0]["files"] == 1 and rows[0]["dirs"] == 12 and not d.exists()


@pytest.mark.skipif(not WIN, reason="the \\\\?\\ form is Windows")
def test_long_path_form():
    assert scratch.long_path("C:\\a\\b") == "\\\\?\\C:\\a\\b"
    assert scratch.long_path("\\\\srv\\share\\x") == "\\\\?\\UNC\\srv\\share\\x"
    assert scratch.long_path("\\\\?\\C:\\a") == "\\\\?\\C:\\a"


def test_a_marked_folder_deeper_than_the_root_is_refused(root):
    d, _ = scratch.new("outer")
    nested = d / "nested"
    nested.mkdir()
    (nested / scratch.MARKER).write_text(json.dumps(
        {"schema": 1, "job": "nested", "root": os.path.normcase(os.path.abspath(root)), "kind": "link",
         "owner": DEAD, "created": 0}), encoding="utf-8")         # claimable in every other way
    (nested / "keep.txt").write_text("x")
    rows = scratch.clean([str(nested)], claim=True)
    assert rows[0]["ok"] is False and (nested / "keep.txt").exists()


@pytest.mark.skipif(not WIN, reason="junctions are Windows")
def test_a_junction_named_like_its_marker_is_still_refused(root, tmp_path):
    import _winapi
    scratch.new("first")                                   # makes the root
    away = tmp_path / "away"
    away.mkdir()
    (away / scratch.MARKER).write_text(json.dumps(
        {"schema": 1, "job": "jx", "root": os.path.normcase(os.path.abspath(root))}), encoding="utf-8")
    (away / "precious.txt").write_text("x")
    _winapi.CreateJunction(str(away), str(root / "jx"))
    rows = scratch.clean(["jx"], claim=True)
    assert rows[0]["ok"] is False and "link or junction" in rows[0]["why"] and (away / "precious.txt").exists()


def test_wipe_keeps_the_marker_until_the_end(root):
    d, _ = scratch.new("order")
    _fill(d, 5)
    res = scratch.wipe(d)
    assert res["files"] == 5 and sorted(os.listdir(d)) == [scratch.MARKER]


def test_may_delete_is_the_one_rule():
    m, link = {"kind": "job"}, {"kind": "link"}
    assert scratch.may_delete(m, "mine", "named")[0]
    assert scratch.may_delete(m, "stale", "stale")[0]
    assert not scratch.may_delete(m, "stale", "named")[0]
    assert not scratch.may_delete(m, "live", "claim")[0]
    assert not scratch.may_delete(m, "kept", "stale")[0]
    assert not scratch.may_delete(m, "young", "stale")[0]
    assert scratch.may_delete(m, "kept", "claim")[0] and scratch.may_delete(m, "young", "claim")[0]
    assert scratch.may_delete(link, "live", "claim")[0]
    assert not scratch.may_delete(link, "live", "named")[0]


def test_preflight_reports_stale_and_unmarked_and_never_stops(root):
    assert scratch.preflight_check()[0] == "ok"
    a, _ = scratch.new("stale1")
    _rewrite(a, owner=DEAD, created=time.time() - 48 * 3600)
    (root / "loose").mkdir()
    st, line = scratch.preflight_check()
    assert st == "warn" and "1 stale job(s) (stale1)" in line and "1 unmarked" in line
    assert scratch.hog_findings()[0]["action"] is None


def test_preflight_names_scratch_in_its_steps():
    from fieldkit.core import preflight
    assert "fieldkit.scratch:preflight_check" in preflight.STEPS


def test_report_sizes_come_from_fieldkit_size(root, monkeypatch):
    d, _ = scratch.new("sized")
    calls = []

    def fake(paths):
        calls.append(paths)
        return {os.path.normcase(os.path.abspath(p)): (123, 4) for p in paths}
    monkeypatch.setattr(scratch, "sizes", fake)
    rows = scratch.report()
    assert calls == [[d]] and rows[0]["bytes"] == 123 and rows[0]["files"] == 4


def test_exclusion_reads_the_live_list_then_the_owner_proof(root, tmp_path, monkeypatch):
    if not WIN:
        assert scratch.exclusion()["present"] is None
        return

    class R:
        def __init__(self, out):
            self.stdout, self.stderr = out, ""
    r = str(root)
    assert scratch.exclusion(run=lambda *a, **k: R(f"C:\\other\n{r}\n"))["present"] is True
    assert scratch.exclusion(run=lambda *a, **k: R("C:\\other\n"))["present"] is False
    admin = "N/A: Must be an administrator to view exclusions"
    assert scratch.exclusion(run=lambda *a, **k: R(admin))["present"] is None
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "exclusion.json").write_text(json.dumps({"root": r, "present": True, "at": "x"}),
                                                        encoding="utf-8-sig")
    ex = scratch.exclusion(run=lambda *a, **k: R(admin))
    assert ex["present"] is True and ex["how"] == "owner script proof"


def test_owner_script_adds_only_one_path_exclusion():
    text = scratch.OWNER_SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    import re
    call = lambda verb: re.findall(r"(?:^|[{;]\s*)" + verb + r"-MpPreference\b[^;}\n]*", code, re.M)
    adds, removes = call("Add"), call("Remove")
    assert len(adds) == 1 and "-ExclusionPath $Root" in adds[0]
    assert len(removes) == 1 and "-ExclusionPath $Root" in removes[0]
    assert "Set-MpPreference" not in code
    for bad in ("ExclusionProcess ", "ExclusionExtension "):
        assert not any(bad.strip() in l and ("Add-" in l or "Remove-" in l) for l in code.splitlines())


@pytest.mark.skipif(not WIN or not shutil.which("powershell"), reason="Windows PowerShell parser")
def test_owner_script_and_wrappers_parse(tmp_path, root):
    out = scratch.write_owner_script(tmp_path / "prop")
    files = sorted(p.name for p in out.iterdir())
    assert files == ["apply.ps1", "scratch-exclusion.ps1", "undo.ps1", "verify.ps1"]
    for f in out.iterdir():
        cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
               f"'{f}', [ref]$null, [ref]$e); $e.Count")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
        assert r.stdout.strip() == "0", (f, r.stdout, r.stderr)
    assert f"-Root '{root}'" in (out / "apply.ps1").read_text(encoding="utf-8")


def test_cli_new_report_clean(root, capsys):
    assert scratch.main(["new", "cli1", "--print-path"]) == 0
    assert capsys.readouterr().out.strip() == str(root / "cli1")
    assert scratch.main(["report", "--no-size", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["job"] == "cli1"
    assert scratch.main(["clean", "cli1", "--dry-run"]) == 0 and (root / "cli1").exists()
    assert scratch.main(["clean", "--mine", "--stale"]) == 2
    assert scratch.main(["clean", "cli1"]) == 0 and not (root / "cli1").exists()
    assert scratch.main(["new", ".."]) == 2


def test_fieldkit_dispatches_scratch(root):
    r = subprocess.run([sys.executable, "-m", "fieldkit", "scratch", "report", "--no-size", "--json"],
                       capture_output=True, text=True, timeout=120, env=dict(os.environ))
    assert r.returncode == 0 and json.loads(r.stdout) == []


def test_bench_times_a_fixture_and_leaves_nothing(root):
    res = scratch.bench(files=250, say=lambda *_: None)
    assert res["made"] == 250 and res["clean_ok"] and list(root.iterdir()) == []


# ---- the cable receiver lands in a scratch job ----------------------------------------------------------------------
class _Link:
    def __init__(self, home):
        self._home, self.logged = home, []

    def home(self):
        return self._home

    def _log(self, t):
        self.logged.append(t)


def test_a_put_stream_lands_in_its_own_scratch_job(root, tmp_path, monkeypatch):
    from fieldkit.peers import link_transfer as lt
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "lh"))
    lk = _Link(tmp_path / "lh")
    dest, part = lt.landing("0123456789abcdef", {"name": "tree"}, {"from_host": "vaio"}, lk)
    assert dest == root / "link-0123456789abcdef" and part == dest / ".partial"
    m = scratch.marker(dest)
    assert m["kind"] == "link" and m["keep"] is True and m["owner"]["pid"] == os.getpid()
    assert lt.landing("0123456789abcdef", {"name": "tree"}, {}, lk)[0] == dest      # resume: same job


def test_without_a_scratch_root_a_test_link_home_keeps_the_old_place(tmp_path, monkeypatch):
    from fieldkit.peers import link_transfer as lt
    monkeypatch.delenv("FIELDKIT_SCRATCH_ROOT", raising=False)
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "lh"))
    lk = _Link(tmp_path / "lh")
    dest, part = lt.landing("0123456789abcdef", {"name": "x"}, {}, lk)
    assert dest == tmp_path / "lh" / "received" and part == dest / ".partial" / "0123456789abcdef"


def test_a_name_clash_with_a_foreign_folder_falls_back_and_says_so(root, tmp_path, monkeypatch):
    from fieldkit.peers import link_transfer as lt
    (root / "link-0123456789abcdef").mkdir(parents=True)              # not a job: never adopted
    lk = _Link(tmp_path / "lh")
    dest, _ = lt.landing("0123456789abcdef", {"name": "x"}, {}, lk)
    assert dest == tmp_path / "lh" / "received" and "scratch root not used" in lk.logged[0]
