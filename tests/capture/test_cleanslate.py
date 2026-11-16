"""P40: the clean-slate start gate. One test per branch: wipe while undigested fails, a mixed-project episode is refused,
the keep list survives, a forced wipe is the owner's."""
import json

from fieldkit.capture import cleanslate as C
from fieldkit.capture import loop


class FakeStore:
    def __init__(self, **led):
        self.led = {"outstanding": [], "lost": [], "unaccounted": [], "stuck": [], **led}

    def recover(self):
        pass

    def ledger(self):
        return self.led


def row(path, status="untracked"):
    return {"path": path, "status": status, "name": path.rsplit("/", 1)[-1], "kind": "script", "cards": [], "root": "/r", "age_days": 1}


def _env(tmp_path, monkeypatch):
    cap = tmp_path / "capture"
    cap.mkdir()
    (cap / "journal.jsonl").write_text("{}\n")
    monkeypatch.setattr(C.st, "capture_dir", lambda: cap)
    return cap


def _go(tmp_path, monkeypatch, store, rows, disp=None, **kw):
    cap = _env(tmp_path, monkeypatch)
    recycled = []
    r = C.start("firefox", "why", store=store, rows=rows, disp=disp or {}, recycle_fn=lambda p: recycled.append(str(p)) or True,
                turn_on=lambda lesson, **k: {"since": "s", "until": "u", "project": k.get("project")}, archive_fn=lambda p: {"path": "a"}, **kw)
    return r, recycled, cap


def test_all_digested_wipes_and_starts(tmp_path, monkeypatch):
    r, recycled, cap = _go(tmp_path, monkeypatch, FakeStore(), [row("/s/ok.py", "ok")])
    assert r["started"] and r["wiped"] and str(cap) in recycled and r["switch"]["project"] == "firefox"


def test_wipe_while_store_undigested_fails(tmp_path, monkeypatch):
    r, recycled, _ = _go(tmp_path, monkeypatch, FakeStore(outstanding=["i1"]), [])
    assert not r["started"] and not recycled and r["refused"] == "undigested material" and "classify" in r["next"]


def test_wipe_while_script_undigested_fails(tmp_path, monkeypatch):
    r, recycled, _ = _go(tmp_path, monkeypatch, FakeStore(), [row("/s/a.py")])
    assert not r["started"] and not recycled


def test_disposed_script_is_wiped_adopted_one_stays(tmp_path, monkeypatch):
    f = tmp_path / "junk.py"
    f.write_text("x")
    k = tmp_path / "adopted.py"
    k.write_text("x")
    r, recycled, _ = _go(tmp_path, monkeypatch, FakeStore(), [row(str(f)), row(str(k), "ok")],
                         disp={C.os.path.normcase(str(f)): {"because": "dup"}})
    assert r["started"] and str(f) in recycled and str(k) not in recycled


def test_keep_list_survives(tmp_path, monkeypatch):
    keep = tmp_path / "local" / "mine.py"
    keep.parent.mkdir()
    keep.write_text("x")
    assert C.is_kept(str(keep)) and C.is_kept("/work/.ssh/id_rsa") and C.is_kept("/x/Fieldkit/fieldkit/a.py")
    r, recycled, _ = _go(tmp_path, monkeypatch, FakeStore(), [row(str(keep))], disp={C.os.path.normcase(str(keep)): {"because": "x"}})
    assert r["started"] and str(keep) not in recycled and r["check"]["scripts"] == []


def test_force_from_agent_refused(tmp_path, monkeypatch):
    r, recycled, _ = _go(tmp_path, monkeypatch, FakeStore(outstanding=["i"]), [], force=True, terminal=False)
    assert not r["started"] and "owner's" in r["refused"] and not recycled


def test_force_at_real_terminal_wipes_with_archive(tmp_path, monkeypatch):
    r, recycled, cap = _go(tmp_path, monkeypatch, FakeStore(outstanding=["i"]), [], force=True, terminal=True)
    assert r["started"] and r["forced"] and r["archive"] and str(cap) in recycled


def test_failed_recycle_starts_nothing(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    r = C.start("firefox", "w", store=FakeStore(), rows=[], disp={}, recycle_fn=lambda p: False, archive_fn=lambda p: {})
    assert not r["started"] and "could not recycle" in r["refused"]


def test_archive_is_hash_chained(tmp_path, monkeypatch):
    cap = _env(tmp_path, monkeypatch)
    (cap / "b.txt").write_text("b")
    a = C.archive("firefox", out_dir=tmp_path / "arch")
    m = json.loads(open(a["manifest"]).read())
    assert m["head"] == a["head"] and len(m["files"]) == 2 and m["files"][0]["chain"] != m["files"][1]["chain"]


def test_disposition_needs_reason(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError):
        C.record_disposition("/x", " ")
    C.record_disposition("/x", "dup")
    assert C.dispositions()


def test_mixed_project_episode_refused():
    assert not loop.episode_in_project({"cwd": "/work/kernel-build"}, "firefox")
    assert loop.episode_in_project({"cwd": "/work/gorilla-firefox"}, "firefox")
    assert loop.episode_in_project({"cwd": "/tmp/x"}, "firefox")
    assert loop.episode_in_project({"cwd": "/work/kernel"}, None)
