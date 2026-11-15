"""fieldkit pool plan: keep / recycle / archive / start for Zero's session pool (fieldkit/lifecycle/pool.py)."""
import json
from datetime import datetime, timezone

from fieldkit.lifecycle import pool

NOW = datetime(2026, 11, 15, 18, 30, tzinfo=timezone.utc)


def S(n, title, **kw):
    d = {"sessionId": f"local_{n:08d}-0000-0000-0000-000000000000", "title": title, "isArchived": False,
         "isRunning": False, "lastActivityAt": "2026-11-15T17:00:00.000Z", "remoteControlActive": False,
         "startedBy": "local_00000001-0000-0000-0000-000000000000"}
    d.update(kw)
    return d


def sid(n):
    return f"local_{n:08d}-0000-0000-0000-000000000000"


def run(sessions, **kw):
    kw.setdefault("clean_fn", lambda i: True)
    return pool.plan(sessions, now=NOW, **kw)


def act(p):
    return {d["id"]: d["action"] for d in p["decisions"]}


def tools(p):
    return [s["tool"].split("__")[-1] for s in p["steps"]]


def test_running_is_kept():
    p = run([S(2, "[VAIO-LNX] job", isRunning=True)], reserve=0)
    assert act(p)[sid(2)] == "KEEP" and p["steps"] == []


def test_open_pr_is_kept():
    p = run([S(2, "[VAIO-LNX] job", prNumber=5, prState="OPEN")], reserve=0)
    assert act(p)[sid(2)] == "KEEP"


def test_merged_pr_idle_is_recycled_in_order_with_rc_off():
    p = run([S(2, "[VAIO-LNX] job", prNumber=5, prState="MERGED", remoteControlActive=True)], reserve=1)
    assert act(p)[sid(2)] == "RECYCLE"
    assert tools(p) == ["SendMessage", "set_remote_control", "clear_session", "set_session_title"]
    assert p["steps"][1]["args"]["enabled"] is False
    assert p["steps"][3]["args"]["title"] == "[VAIO-LNX] Reserve 1 (standby for Zero)"


def test_recent_idle_is_kept():
    p = run([S(2, "[VAIO-LNX] job", lastActivityAt="2026-11-15T18:25:00.000Z")], reserve=0)
    assert act(p)[sid(2)] == "KEEP"


def test_not_done_is_kept():
    p = run([S(2, "[VAIO-LNX] job", done=False)], reserve=0)
    assert act(p)[sid(2)] == "KEEP"


def test_unclean_or_unprovable_is_kept_never_recycled():
    for verdict in (False, None):
        p = run([S(2, "[VAIO-LNX] job")], reserve=0, clean_fn=lambda i, v=verdict: v)
        assert act(p)[sid(2)] == "KEEP"
        assert "permanence check --session" in p["decisions"][0]["why"]
    p = run([S(2, "[VAIO-LNX] job")], reserve=0, clean_fn=lambda i: None, clean={sid(2)})
    assert act(p)[sid(2)] == "RECYCLE"


def test_zero_seat_and_self_and_parents_are_kept():
    ss = [S(2, "[VAIO-LNX] Zero: coordinate", startedBy=None), S(3, "[VAIO-LNX] me"), S(4, "[VAIO-LNX] parent"),
          S(5, "[VAIO-LNX] child", startedBy=sid(4), isRunning=True)]
    a = act(run(ss, reserve=0, self_id=sid(3)))
    assert a[sid(2)] == a[sid(3)] == a[sid(4)] == "KEEP"


def test_archived_sessions_are_ignored():
    p = run([S(2, "[VAIO-LNX] job", isArchived=True)], reserve=0)
    assert p["decisions"] == []


def test_surplus_reserves_archived_oldest_first_with_rc_off():
    ss = [S(2, "[VAIO-LNX] Reserve 1 (standby for Zero)", lastActivityAt="2026-11-15T10:00:00.000Z",
            remoteControlActive=True),
          S(3, "[VAIO-LNX] Reserve 2 (standby for Zero)"), S(4, "[VAIO-LNX] Reserve 3 (standby for Zero)")]
    p = run(ss, reserve=2)
    assert act(p)[sid(2)] == "ARCHIVE" and act(p)[sid(3)] == "RESERVE"
    assert tools(p) == ["set_remote_control", "archive_session"]


def test_recycled_count_as_reserves_so_no_start():
    ss = [S(2, "[VAIO-LNX] a"), S(3, "[VAIO-LNX] b")]
    p = run(ss, reserve=2)
    assert "start_session" not in tools(p) and tools(p).count("clear_session") == 2
    titles = [s["args"]["title"] for s in p["steps"] if s["tool"].endswith("set_session_title")]
    assert titles == ["[VAIO-LNX] Reserve 1 (standby for Zero)", "[VAIO-LNX] Reserve 2 (standby for Zero)"]


def test_top_up_starts_and_numbers_skip_used():
    p = run([S(2, "[VAIO-LNX] Reserve 1 (standby for Zero)")], reserve=3)
    st = [s for s in p["steps"] if s["tool"].endswith("start_session")]
    assert [s["args"]["title"][-28:] for s in st] == ["Reserve 2 (standby for Zero)", "Reserve 3 (standby for Zero)"]
    assert st[0]["args"]["initiation"] == "user_asked" and st[0]["args"]["context"] == "fresh"


def test_event_turn_says_wait_not_start():
    p = run([], reserve=1, event_turn=True, tag="[VAIO-LNX]")
    assert tools(p) == ["WAIT"] and "owner turn" in p["steps"][0]["why"]


def test_tag_is_the_most_common_one():
    p = run([S(2, "[L15-WIN] x", isRunning=True), S(3, "[VAIO-LNX] y", isRunning=True), S(4, "[VAIO-LNX] z", isRunning=True)],
            reserve=1)
    assert p["steps"][0]["args"]["title"].startswith("[VAIO-LNX] Reserve 1")


def test_pool_as_wanted_does_nothing():
    p = run([S(2, "[VAIO-LNX] Reserve 1 (standby for Zero)"), S(3, "[VAIO-LNX] Reserve 2 (standby for Zero)")], reserve=2)
    assert p["steps"] == [] and "nothing to do" in pool.render(p)


def test_cli_reads_a_file_and_json(tmp_path, capsys):
    f = tmp_path / "l.json"
    f.write_text(json.dumps([S(2, "[VAIO-LNX] job", isRunning=True)]), encoding="utf-8")
    assert pool.main(["plan", "--sessions", str(f), "--reserve", "1", "--event-turn", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["steps"][0]["tool"] == "WAIT"
    assert pool.main(["plan", "--sessions", str(tmp_path / "none.json")]) == 2


def test_default_rules_load():
    r = pool.load_rules()
    assert r["reserve"] == 2 and r["idle_minutes"] > 0
