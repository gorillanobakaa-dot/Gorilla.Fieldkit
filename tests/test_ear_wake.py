"""P28 A: the ear supervisor wakes a deaf station's note through Zero, once, after 20 s; never a running ear,
a broadcast, Zero itself, or history older than a day."""
from fieldkit.peers import ear_wake as ew

NOW = 1_000_000.0


def entry(to, ts, eid, kind="msg", frm="claude:z@H"):
    return {"to": to, "ts": ts, "id": eid, "kind": kind, "from": frm, "at": "t"}


def run(entries, ears=None, stations=None, woken=frozenset(), zero="claude:z@H"):
    stations = stations or {"claude:a@H": {"host_session": "local_a"}, "claude:z@H": {}}
    ears = ears if ears is not None else {"claude:a@H": {"state": "stopped", "heard": 10}}
    return ew.waiting(stations, ears, entries, zero, NOW, woken)


def test_old_direct_note_on_deaf_station_is_woken():
    got = run([(20, entry("claude:a@H", NOW - 30, "M1"))])
    assert [(w["station"], w["id"], w["host_session"]) for w in got] == [("claude:a@H", "M1", "local_a")]
    assert ew.wake_text(got[0]) == "WAKE local_a M1"


def test_young_note_waits():
    assert run([(20, entry("claude:a@H", NOW - ew.WAKE_AGE_S + 1, "M1"))]) == []


def test_running_ear_is_not_woken():
    assert run([(20, entry("claude:a@H", NOW - 30, "M1"))], ears={"claude:a@H": {"state": "running", "heard": 10}}) == []


def test_note_already_heard_is_not_woken():
    assert run([(5, entry("claude:a@H", NOW - 30, "M1"))]) == []


def test_broadcast_zero_woken_and_history_skipped():
    es = [(20, entry("*", NOW - 30, "B")), (21, entry("claude:z@H", NOW - 30, "Z", frm="claude:a@H")),
          (22, entry("claude:a@H", NOW - 30, "W")), (23, entry("claude:a@H", NOW - ew.MAX_AGE_S - 5, "OLD"))]
    ears = {"claude:a@H": {"state": "stopped", "heard": 10}, "claude:z@H": {"state": "stopped", "heard": 0}}
    assert run(es, ears=ears, woken={"W"}) == []


def test_run_sends_once_and_records(monkeypatch, tmp_path):
    item = {"station": "claude:a@H", "host_session": "local_a", "id": "M9", "at": "t"}
    monkeypatch.setattr(ew, "woken_path", lambda: tmp_path / "woken.json")
    monkeypatch.setattr(ew, "scan", lambda now=None: ("claude:z@H", [] if "M9" in ew.load_woken() else [item]))
    sent = []
    assert ew.run(send=sent.append) == [item]
    assert ew.run(send=sent.append) == []
    assert sent == ["WAKE local_a M9"]
    assert ew.deaf_waiting([item, item]) == ["claude:a@H"]


def test_follow_ear_keeps_listening_and_runs_supervisor():
    from fieldkit.peers import link_relay

    class A:
        as_station, since, seconds = "claude:a@H", None, 0.0
    calls, out = [], []

    def listen(st, since, secs):
        calls.append(secs)
        return 1, [], []
    code = link_relay.station_follow(A(), out=out.append, listen=listen,
                                     wake=lambda: [{"host_session": "local_b", "station": "s", "id": "M2"}])
    assert code == 1 and calls == [ew.WAKE_EVERY_S]
    assert out == ["WAKE SENT to Zero: WAKE local_b M2"]


def test_same_note_twice_in_log_is_one_wake():
    es = [(20, entry("claude:a@H", NOW - 30, "M1")), (21, entry("claude:a@H", NOW - 29, "M1"))]
    assert len(run(es)) == 1


def test_hog_findings_warns_on_deaf_station():
    items = [{"station": "claude:a@H", "host_session": "", "id": "M1", "at": ""}]
    f = ew.hog_findings(items)
    assert f and f[0]["severity"] == "warn"
    assert ew.hog_findings([]) == []
