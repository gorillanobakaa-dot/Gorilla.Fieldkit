"""fieldkit zero / fieldkit net (2026-10-26, owner: "0 has been established the military way ... time syn, comms
check the whole shebang that makes a network stay alive"): every verb, with fake channels, a fake cable and a fixed
clock. Nothing here touches a real session, the cable or the scheduler."""
import io
import json
import time

import pytest

from fieldkit.zero import channels as ch
from fieldkit.zero import clock, net, schedule

INGEST = net.ingest_cable
CABLE = ch.Cable
T0 = 1_800_000_000.0
HOST = "HOSTA"
ZERO = f"claude:aaaaaaaa@{HOST}"
TWO = f"claude:bbbbbbbb@{HOST}"
GEM = f"gemini:g1@{HOST}"
SIT = {"job": "zero net", "state": "working", "branch": "feat/zero-net", "waiting": "-", "not_verified": "live VAIO",
       "shared": "-"}


class FakeChannel:
    name, mode = "fake", "push"

    def __init__(self, ok=True):
        self.sent, self.ok = [], ok

    def push(self, entry, n):
        self.sent.append(entry)
        return [{"channel": self.name, "target": "x", "ok": self.ok, "detail": "sent" if self.ok else "down"}]


class FakeCable:
    def __init__(self, devices=(), answers=None):
        self._devices, self.answers, self.pushed = list(devices), answers or {}, []

    def devices(self):
        return self._devices

    def hello(self, d):
        return self.answers.get(d, {"ok": False, "error": "refused"})

    def push(self, entry, n):
        self.pushed.append(entry)
        return [{"channel": "cable", "target": d, "ok": True, "detail": "stored there"} for d in self._devices]


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path))
    monkeypatch.setattr(net, "machine", lambda: HOST)
    monkeypatch.setattr(net, "_key", lambda: None)
    monkeypatch.setattr(net, "ingest_cable", lambda *a, **k: 0)
    monkeypatch.setattr(ch, "default", lambda: [FakeChannel()])
    monkeypatch.setattr(ch, "Cable", lambda *a, **k: FakeCable())
    # nothing a test does may reach the owner's real inbox, link home or room (2026-10-26: 78 test items in the
    # owner's inbox from a sibling suite); every place is a tmp folder, and the guard test below proves it
    from fieldkit.autopilot import inbox, room
    monkeypatch.setenv("FIELDKIT_AUTOPILOT_STATE", str(tmp_path / "autopilot"))
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "autopilot" / "inbox")
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    return tmp_path


def opened(**kw):
    return net.open_net(ZERO, now=T0, env={"CLAUDE_CODE_SESSION_ID": "aaaaaaaa-1"}, **kw)


# ---------------------------------------------------------------- identity

def test_station_id_comes_from_the_session_then_as_then_shell():
    assert net.station_id(env={"CLAUDE_CODE_SESSION_ID": "413702f5-1fed"}, host="H") == "claude:413702f5@H"
    assert net.station_id("gemini:x", env={"CLAUDE_CODE_SESSION_ID": "413702f5"}, host="H") == "gemini:x@H"
    assert net.station_id("opencode:a@VAIO", env={}, host="H") == "opencode:a@VAIO"
    assert net.station_id(env={"USERNAME": "u"}, host="H") == "shell:u@H"
    assert net.hook_station("aaaaaaaa-zz", host=HOST) == ZERO
    assert net.kind_of(GEM) == "gemini" and net.machine_of(GEM) == HOST


# ---------------------------------------------------------------- open / establish

def test_open_establishes_zero_with_epoch_1_and_announces(tmp_path):
    fake = FakeChannel()
    r = net.open_net(ZERO, second=TWO, name="command post", channels=[fake], now=T0, env={})
    assert r["net"]["epoch"] == 1 and net.zero_of(r["net"]) == ZERO
    assert r["net"]["2ic"]["station"] == TWO
    assert fake.sent and fake.sent[0]["kind"] == "open"
    assert "Zero has been established" in (tmp_path / "net-log.jsonl").read_text(encoding="utf-8")
    assert net.load_stations()["stations"][ZERO]["lease_until"] == T0 + 30 * 60


def test_a_second_zero_is_refused_split_brain():
    opened()
    with pytest.raises(net.Refused, match="split-brain"):
        net.open_net(TWO, now=T0 + 1)


def test_reopen_by_zero_keeps_the_epoch_and_close_then_open_raises_it():
    opened()
    assert net.open_net(ZERO, interval=5, now=T0 + 1, env={})["net"]["epoch"] == 1
    assert net.load_net()["interval_min"] == 5
    net.close_net(ZERO, channels=[FakeChannel()], now=T0 + 2)
    assert net.load_net()["net"] == "closed"
    assert net.open_net(TWO, now=T0 + 3, env={})["net"]["epoch"] == 2       # an epoch is never reused


def test_status_without_a_net_says_not_established():
    r = net.status(now=T0)
    assert not r["established"] and r["level"] == net.NONET
    assert net.status_lines(r)[0].startswith("ZERO NOT ESTABLISHED")


# ---------------------------------------------------------------- fencing / handover

def test_only_zero_hands_over_and_a_stale_epoch_is_refused(monkeypatch):
    from fieldkit import plan                       # the relief learns where the harness plan stands
    monkeypatch.setattr(plan, "line", lambda *a, **k: "PLAN: phase 3. X: open R2")
    opened(second=TWO)
    with pytest.raises(net.Refused, match="not Zero"):
        net.handover(GEM, to=TWO, now=T0 + 1)
    with pytest.raises(net.Refused, match="stale epoch"):
        net.handover(ZERO, to=TWO, epoch=7, now=T0 + 1)
    r = net.handover(ZERO, to=TWO, epoch=1, channels=[FakeChannel()], now=T0 + 1)
    assert r["net"]["epoch"] == 2 and net.zero_of(r["net"]) == TWO and r["previous"] == ZERO
    assert r["entry"]["text"].endswith(". PLAN: phase 3. X: open R2")
    assert r["net"]["2ic"] is None                       # the 2IC became Zero: no 2IC left
    with pytest.raises(net.Refused, match="not Zero"):     # the old Zero can no longer act
        net.rollcall(ZERO, now=T0 + 2)


def test_the_2ic_assumes_only_when_zero_is_missing():
    opened(second=TWO)
    with pytest.raises(net.Refused, match="not MISSING"):
        net.handover(TWO, assume=True, now=T0 + 60)
    with pytest.raises(net.Refused, match="not UP"):                 # a station that is not live never assumes
        net.handover(GEM, assume=True, now=T0 + 3 * 3600)
    r = net.handover(TWO, assume=True, channels=[FakeChannel()], now=T0 + 30 * 60 * 2 + 1)
    assert net.zero_of(r["net"]) == TWO and "2IC assumed" in r["entry"]["text"]


def test_a_dead_zero_with_no_live_2ic_cannot_hold_the_net_hostage():
    opened()                                                        # no 2IC named
    late = T0 + 3 * 3600
    st = net.load_stations()
    net.renew(st, GEM, net.load_net(), late - 10)
    net.save_stations(st)
    with pytest.raises(net.Refused, match="not MISSING"):
        net.handover(GEM, assume=True, now=T0 + 60)
    r = net.handover(GEM, assume=True, channels=[FakeChannel()], now=late)
    assert net.zero_of(r["net"]) == GEM and "MISSING" in r["entry"]["text"]


def test_a_rotated_zero_leaves_a_pending_handover_its_relief_takes():
    opened()
    assert net.on_rotate("bbbbbbbb-other", now=T0) == ""            # not Zero: nothing
    line = net.on_rotate("aaaaaaaa-1", now=T0 + 5)
    assert "handover --take" in line and net.load_net()["handover_pending"]["from"] == ZERO
    relief = f"claude:cccccccc@{HOST}"
    r = net.handover(relief, take=True, channels=[FakeChannel()], now=T0 + 10, env={})
    assert net.zero_of(r["net"]) == relief and r["net"]["epoch"] == 2 and "handover_pending" not in r["net"]
    with pytest.raises(net.Refused, match="no handover is pending"):
        net.handover(GEM, take=True, now=T0 + 11)


def test_a_stale_pending_handover_is_refused():
    opened()
    net.on_rotate("aaaaaaaa-1", now=T0)
    with pytest.raises(net.Refused, match="no handover is pending"):
        net.handover(TWO, take=True, now=T0 + net.PENDING_TTL_S + 1)


# ---------------------------------------------------------------- leases

@pytest.mark.parametrize("dt,state,m", [(-60, "UP", 0), (0, "UP", 0), (1, "SILENT", 1), (1799, "SILENT", 1),
                                        (1800, "MISSING", 2), (3600, "MISSING", 3)])
def test_lease_states_from_the_ttl(dt, state, m):
    rec = {"lease_until": T0, "ttl": 1800}                          # dt counts from the end of the lease
    assert net.misses(rec, T0 + dt) == m
    assert net.state_of(rec, T0 + dt) == state


def test_signed_off_station_is_off():
    assert net.state_of({"lease_until": 0, "ttl": 1, "off": True}, T0) == "OFF"


# ---------------------------------------------------------------- checkin / sitrep

def test_checkin_without_a_net_says_not_established():
    assert net.checkin(GEM, now=T0, env={})["established"] is False


def test_sitrep_format_is_enforced():
    assert net.check_sitrep(SIT)["state"] == "WORKING"
    with pytest.raises(ValueError, match="missing: waiting on"):
        net.check_sitrep(dict(SIT, waiting=""))
    with pytest.raises(ValueError, match="state"):
        net.check_sitrep(dict(SIT, state="busy"))
    with pytest.raises(ValueError, match="one plain line"):
        net.check_sitrep(dict(SIT, job="x" * 301))


def test_rollcall_answer_late_and_missing():
    opened()
    net.checkin(GEM, now=T0, env={})
    net.checkin(TWO, now=T0 - 3 * 3600, env={})          # heard long ago: MISSING by now
    r = net.rollcall(ZERO, channels=[FakeChannel()], now=T0 + 10)
    assert set(r["expect"]) == {GEM, TWO}
    rows = {x["station"]: x["status"] for x in net.roll(net.load_net(), T0 + 20)["rows"]}
    assert rows == {GEM: "PENDING", TWO: "MISSING"}
    c = net.checkin(GEM, sitrep=SIT, now=T0 + 30, env={})
    assert c["entry"]["ref"] == r["entry"]["id"]                         # answers the open roll call by itself
    assert any(e["kind"] == "rollcall" for e in c["pending"])
    rr = net.roll(net.load_net(), T0 + 40)
    got = {x["station"]: x for x in rr["rows"]}
    assert got[GEM]["status"] == "ANSWERED" and got[GEM]["sitrep"]["branch"] == "feat/zero-net"
    assert net.roll_level(rr["rows"]) == net.ALARM
    # a station that never answers is LATE after the deadline
    net.checkin(f"opencode:x@{HOST}", now=T0 + 40, env={})
    r2 = net.rollcall(ZERO, answer=1, channels=[FakeChannel()], now=T0 + 50)
    late = {x["station"]: x["status"] for x in net.roll(net.load_net(), T0 + 50 + 61)["rows"]}
    assert late[f"opencode:x@{HOST}"] == "LATE" and r2["entry"]["kind"] == "rollcall"


def test_roll_lists_unregistered_and_cable_with_no_station():
    opened()
    found = [{"station": f"claude:dddddddd@{HOST}", "kind": "claude", "via": "room", "detail": "transcript"},
             {"station": "*@VAIO", "kind": "cable", "via": "cable", "detail": "cable peer VAIO"}]
    rows = {x["station"]: x["status"] for x in net.roll(net.load_net(), T0, found)["rows"]}
    assert rows[f"claude:dddddddd@{HOST}"] == "UNREGISTERED" and rows["*@VAIO"] == "NO STATION"


def test_checkin_on_the_far_computer_goes_back_over_the_cable(monkeypatch):
    opened()
    cab = FakeCable(["HOSTA"])
    r = net.checkin("claude:eeeeeeee@VAIO", sitrep=SIT, now=T0, env={}, cable=cab)
    assert cab.pushed and cab.pushed[0]["kind"] == "checkin" and r["remote"][0]["ok"]
    r2 = net.checkin(GEM, now=T0, env={}, cable=cab)                  # same computer as Zero: not sent
    assert r2["remote"] == [] and len(cab.pushed) == 1


# ---------------------------------------------------------------- messages, acks, discipline, quiet hours

def test_message_wants_an_ack_is_retried_then_listed_undelivered():
    opened()
    net.checkin(GEM, now=T0, env={})
    fake = FakeChannel()
    m = net.send(ZERO, "hold all pushes", channels=[fake], now=T0)["entry"]
    assert m["want_ack"] and m["data"]["expect"] == [GEM]
    assert [u["entry"]["id"] for u in net.unacked(net.load_net(), T0)] == [m["id"]]
    for i in range(1, 5):                                            # GEM never checks in again: never acks
        net.check(heal=False, channels=[fake], now=T0 + i * 400, board=False)
    r = net.check(heal=False, channels=[fake], now=T0 + 3000, board=False)
    assert r["undelivered"] and r["undelivered"][0]["id"] == m["id"]
    assert sum(1 for e in fake.sent if e["id"] == m["id"]) == 1 + net.DEFAULTS["retries"]   # first send + retries


def test_checkin_delivers_and_acks_so_no_retry():
    opened()
    net.checkin(GEM, now=T0, env={})
    m = net.send(ZERO, "status?", channels=[FakeChannel()], now=T0 + 1)["entry"]
    c = net.checkin(GEM, now=T0 + 2, env={})
    assert m["id"] in c["acked"]
    assert net.unacked(net.load_net(), T0 + 3) == []
    again = net.checkin(GEM, now=T0 + 4, env={})                         # seen once, never shown again
    assert again["pending"] == [] and again["acked"] == []


def test_net_discipline_rate_limit_and_flash():
    opened()
    for i in range(net.DEFAULTS["per_minute"]):
        net.send(ZERO, f"m{i}", channels=[FakeChannel()], now=T0 + i)
    with pytest.raises(net.Refused, match="net discipline"):
        net.send(ZERO, "one more", channels=[FakeChannel()], now=T0 + 10)
    assert net.send(ZERO, "urgent", flash=True, channels=[FakeChannel()], now=T0 + 11)["entry"]["data"]["flash"]


def test_quiet_hours_hold_routine_traffic_not_command_traffic(monkeypatch):
    opened(quiet="00:00-23:59")
    fake = FakeChannel()
    r = net.send(ZERO, "routine", channels=[fake], now=T0)
    assert not fake.sent and "held" in r["pushed"][0]["detail"]
    net.send(ZERO, "flash", flash=True, channels=[fake], now=T0 + 1)
    net.rollcall(ZERO, channels=[fake], now=T0 + 2)
    assert [e["kind"] for e in fake.sent] == ["msg", "rollcall"]


@pytest.mark.parametrize("q,hh,mm,want", [("23:00-07:00", 23, 30, True), ("23:00-07:00", 6, 59, True),
                                          ("23:00-07:00", 7, 0, False), ("09:00-17:00", 12, 0, True),
                                          ("09:00-17:00", 8, 59, False), ("", 3, 0, False)])
def test_quiet_window(q, hh, mm, want):
    t = time.mktime((2026, 10, 26, hh, mm, 0, 0, 0, -1))
    assert net._quiet({"quiet": q}, t) is want


# ---------------------------------------------------------------- the net log

def test_log_is_hash_chained_and_a_changed_line_is_found(tmp_path):
    opened()
    net.checkin(GEM, now=T0, env={})
    net.send(ZERO, "x", channels=[FakeChannel()], now=T0)
    v = net.verify()
    assert v["ok"] and v["lines"] >= 3
    p = tmp_path / "net-log.jsonl"
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace(GEM, "claude:evil@X")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    v = net.verify()
    assert not v["ok"] and any("chain broken" in x for x in v["problems"])


def test_log_is_signed_with_the_cable_key_and_a_forged_mac_is_found(monkeypatch, tmp_path):
    monkeypatch.setattr(net, "_key", lambda: b"k" * 32)
    opened()
    v = net.verify()
    assert v["ok"] and v["signed"] == v["lines"] == 1
    p = tmp_path / "net-log.jsonl"
    e = json.loads(p.read_text(encoding="utf-8"))
    e["mac"] = "0" * 64
    p.write_text(json.dumps(e) + "\n", encoding="utf-8")
    assert any("bad signature" in x for x in net.verify()["problems"])


def test_seq_numbers_run_one_by_one():
    opened()
    for i in range(3):
        net.ack(GEM, f"M-{i}", now=T0 + i)
    assert [e["seq"] for _, e in net.entries()] == [1, 2, 3, 4]


# ---------------------------------------------------------------- the comms check

def test_check_flags_silent_then_inbox_on_the_second_miss_once(tmp_path):
    (tmp_path / "PLAN.md").write_text("# plan\n\n## Board (x)\n\n| Session | Job | State | Next |\n|---|---|---|---|\n"
                                      "| other | row | x | y |\n", encoding="utf-8")
    opened()
    net.checkin(GEM, now=T0, env={})
    box = []
    add = lambda *a: box.append(a)                                   # noqa: E731
    r = net.check(heal=False, now=T0 + 1801, inbox_add=add)
    assert {x["station"]: x["state"] for x in r["rows"]}[GEM] == "SILENT" and not box
    assert r["level"] == net.WARN                                    # SILENT (Zero too) is a warning, not yet an alarm
    plan = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
    assert net.BOARD_KEY in plan and "SILENT" in plan and "| other | row | x | y |" in plan
    net.check(heal=False, now=T0 + 3601, inbox_add=add)
    net.check(heal=False, now=T0 + 3700, inbox_add=add)
    assert len([b for b in box if GEM in b[0]]) == 1                 # once per lease, not every check
    misses = [e for _, e in net.entries() if e["kind"] == "miss" and e["to"] == GEM]
    assert [e["data"]["misses"] for e in misses] == [1, 2]


def test_an_ended_hook_station_is_logged_once_and_never_reaches_the_inbox():
    """2026-10-25..27: 59 of 83 inbox items were 'station MISSING' for Claude sessions that had simply ended. A
    station only the hook checked in is ENDED after two missed leases: one log line, no inbox item, no alarm. A
    station that checks in by hand, Zero and the 2IC still go to the inbox."""
    opened(second=GEM)
    net.hook_line(hook_call("aaaaaaaa-1"), "UserPromptSubmit", now=T0 + 61)     # Zero, renewed by the hook too
    net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0)          # TWO: checked in by the hook only
    other = f"gemini:hand@{HOST}"
    net.checkin(other, now=T0, env={})
    net.checkin(GEM, now=T0, env={})
    box = []
    add = lambda *a: box.append(a)                                   # noqa: E731
    for t in (T0 + 3601, T0 + 5401, T0 + 9000):
        r = net.check(heal=False, now=t, inbox_add=add, board=False)
    rows = {x["station"]: x["state"] for x in r["rows"]}
    assert rows[TWO] == "ENDED" and rows[other] == "MISSING" and rows[GEM] == "MISSING" and rows[ZERO] == "MISSING"
    assert net.load_stations()["stations"][ZERO]["via"] == "hook"
    assert not [b for b in box if TWO in b[0]]
    assert [b for b in box if other in b[0]] and [b for b in box if GEM in b[0]]
    assert len([e for _, e in net.entries() if e["kind"] == "miss" and e["to"] == TWO and "ENDED" in e["text"]]) == 1
    net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0 + 9100)    # it came back: UP again
    assert {x["station"]: x["state"] for x in net.check(heal=False, now=T0 + 9101, inbox_add=add,
                                                          board=False)["rows"]}[TWO] == "UP"


def test_check_without_a_net():
    assert net.check(heal=False, now=T0)["level"] == net.NONET


def test_check_revives_a_dead_cable():
    opened()
    cab = FakeCable(["VAIO"])
    called = []
    r = net.check(heal=True, classify=lambda d: False, now=T0 + 1, cable=cab, reviver=lambda d: called.append(d) or 0, board=False)
    assert called == ["VAIO"] and r["links"][0]["revived"] and "REVIVED" in r["links"][0]["detail"]
    r = net.check(heal=True, classify=lambda d: False, now=T0 + 2, cable=cab, reviver=lambda d: 3, board=False)
    assert r["level"] == net.ALARM and not r["links"][0].get("revived")
    r = net.check(heal=False, now=T0 + 3, cable=FakeCable(["VAIO"], {"VAIO": {"ok": True}}), board=False)
    assert r["links"][0]["ok"] and r["level"] == net.OK


@pytest.fixture
def linkhome_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))


def test_a_peer_that_is_off_is_off_not_lost_and_is_not_retried(linkhome_off):
    from fieldkit.peers import link_off
    opened()
    cab, asked = FakeCable(["VAIO"]), []
    r = net.check(now=T0 + 1, cable=cab, classify=lambda d: asked.append(d) or True, board=False)
    assert r["links"][0]["off"] and r["level"] != net.ALARM and link_off.is_off("VAIO")
    r = net.check(now=T0 + 2, cable=cab, classify=lambda d: asked.append(d) or True, board=False)
    assert asked == ["VAIO"] and r["links"][0]["off"]                        # the ladder ran once, not at every check
    net.check(now=T0 + 3, cable=FakeCable(["VAIO"], {"VAIO": {"ok": True}}), board=False)
    assert not link_off.is_off("VAIO")                                       # it answered: no longer off
    r = net.check(now=T0 + 4, cable=cab, classify=lambda d: False, board=False)
    assert not r["links"][0].get("off") and r["level"] == net.ALARM          # silent but not provably off: lost


def test_the_comms_check_does_not_revive_the_cable_unless_asked():
    opened()
    called = []
    r = net.check(now=T0 + 1, cable=FakeCable(["VAIO"]), classify=lambda d: False,
                  reviver=lambda d: called.append(d) or 0, board=False)
    assert called == [] and r["level"] == net.ALARM


def test_board_row_is_replaced_not_duplicated(tmp_path):
    p = tmp_path / "PLAN.md"
    p.write_text("## Board\n\n| a | b |\n|---|---|\n| keep | me |\n", encoding="utf-8")
    assert net.write_board(net.BOARD_KEY + " one |", p) == "written"
    assert net.write_board(net.BOARD_KEY + " one |", p) == "unchanged"
    assert net.write_board(net.BOARD_KEY + " two |", p) == "written"
    t = p.read_text(encoding="utf-8")
    assert t.count(net.BOARD_KEY) == 1 and "two" in t and "| keep | me |" in t
    assert net.write_board("x", tmp_path / "none.md") == "no board"


# ---------------------------------------------------------------- the cable: store-and-forward

def test_ingest_cable_takes_a_far_checkin_once_and_a_higher_epoch(monkeypatch):
    monkeypatch.setattr(net, "ingest_cable", INGEST)                  # the real one, not the fixture's no-op
    opened()
    far = {"kind": "checkin", "id": "E-far1", "from": "claude:eeeeeeee@VAIO", "to": ZERO, "data": {"sitrep": SIT},
           "epoch": 1}
    assert net.ingest_cable([(5, far)], now=T0) == 1
    assert net.ingest_cable([(5, far)], now=T0) == 0                  # idempotent: the same id once
    assert net.load_stations()["stations"]["claude:eeeeeeee@VAIO"]["via"] == "cable"
    assert net.load_stations()["meta"]["cable_since"] == 5
    stale = {"kind": "handover", "id": "E-old", "from": "x@VAIO", "epoch": 0,
             "data": {"net": {"epoch": 0, "zero": {"station": "x@VAIO"}}}}
    net.ingest_cable([(6, stale)], now=T0)
    assert net.zero_of(net.load_net()) == ZERO
    brain = {"kind": "open", "id": "E-sb", "from": "y@VAIO", "epoch": 1,
             "data": {"net": {"net": "open", "epoch": 1, "zero": {"station": "y@VAIO"}}}}
    net.ingest_cable([(7, brain)], now=T0)
    assert net.zero_of(net.load_net()) == ZERO
    assert any(e["kind"] == "refused" and "split-brain" in e.get("text", "") for _, e in net.entries())
    newer = {"kind": "handover", "id": "E-new", "from": "y@VAIO", "epoch": 2,
             "data": {"net": {"net": "open", "epoch": 2, "zero": {"station": "y@VAIO", "machine": "VAIO"}}}}
    net.ingest_cable([(8, newer)], now=T0)
    n = net.load_net()
    assert net.zero_of(n) == "y@VAIO" and n["epoch"] == 2 and "mirror_of" in n


def test_envelope_round_trip_through_a_stamped_link_note():
    e = {"kind": "rollcall", "id": "R-1", "from": ZERO, "text": "report"}
    text = "[FROM WINDOWS #12] " + ch.envelope(e)
    assert ch.parse_envelope(text) == e
    assert ch.parse_envelope("[FROM LINUX #3] hello") is None
    assert ch.parse_envelope("NET1 {broken") is None


def test_inbox_items_reads_only_net_notes():
    items = [{"n": 1, "text": "plain note", "from_host": "VAIO"},
             {"n": 2, "text": "[FROM LINUX #4] " + ch.envelope({"kind": "ack", "id": "E-1", "ref": "M-1"}),
              "from_host": "VAIO"}]
    got = ch.inbox_items(0, inbox=lambda w, s: items)
    assert got == [(2, {"kind": "ack", "id": "E-1", "ref": "M-1", "via": "cable", "from_host": "VAIO"})]


# ---------------------------------------------------------------- channels

def test_opencode_channel_pushes_to_every_live_session():
    sent = []
    c = ch.OpenCode(sessions=lambda: [{"name": "a"}, {"name": "b"}],
                    send=lambda n, t, as_name: sent.append((n, t)) or {"ok": n == "a", "error": "busy"})
    r = c.push({"kind": "rollcall", "id": "R-9"}, {"zero": {"station": ZERO}, "epoch": 1})
    assert [x["ok"] for x in r] == [True, False] and "checkin --ref R-9" in sent[0][1]
    assert "--as opencode:a" in sent[0][1]


def test_cable_channel_not_paired_is_said():
    c = CABLE(household=lambda: {})
    assert c.push({"kind": "msg", "id": "M-1"}, {})[0]["detail"].startswith("not paired")


def test_cable_channel_sends_the_envelope_to_each_device():
    calls = []
    c = CABLE(household=lambda: {"me": "A", "devices": [{"name": "A"}, {"name": "VAIO"}]},
                 call=lambda req, device, **k: calls.append((req, device)) or {"ok": True})
    r = c.push({"kind": "msg", "id": "M-1"}, {})
    assert r[0]["ok"] and calls[0][1] == "VAIO" and calls[0][0]["text"].startswith(ch.NET1)


def test_one_dead_channel_never_silences_the_others():
    class Boom:
        name = "boom"

        def push(self, e, n):
            raise OSError("pipe gone")
    fake = FakeChannel()
    out = net._push({"kind": "open"}, {}, [Boom(), fake], T0)
    assert out[0]["ok"] is False and "pipe gone" in out[0]["detail"] and fake.sent


def test_human_lines_tell_the_station_what_to_run():
    n = {"zero": {"station": ZERO}, "epoch": 3}
    assert "Zero has been established" in ch.human({"kind": "open", "id": "E"}, n)
    assert "fieldkit net ack M-1" in ch.human({"kind": "msg", "id": "M-1", "want_ack": True, "from": ZERO, "text": "x"}, n)
    assert "Zero is now" in ch.human({"kind": "handover", "id": "E"}, n)


# ---------------------------------------------------------------- the hook rider

def hook_call(sid, event="UserPromptSubmit"):
    return {"session_id": sid, "cwd": "C:/x", "hook_event_name": event}


def test_hook_is_silent_without_a_net():
    # the machine tag line (peers/machine.py) is said once per session, net or no net; nothing of the net's own
    assert net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0).startswith("MACHINE: ")
    assert net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0) == ""


def test_hook_checks_a_new_session_in_and_shows_its_traffic_once():
    opened()
    r = net.rollcall(ZERO, channels=[FakeChannel()], now=T0 + 1)
    line = net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0 + 2)
    assert f"you are station {TWO}" in line and f"ROLL CALL {r['entry']['id']}" in line
    assert net.load_stations()["stations"][TWO]["via"] == "hook"
    assert net.hook_line(hook_call("bbbbbbbb-1", "PostToolUse"), "PostToolUse", now=T0 + 3) == ""
    net.send(ZERO, "direct", to=TWO, channels=[FakeChannel()], now=T0 + 4)
    line = net.hook_line(hook_call("bbbbbbbb-1", "PostToolUse"), "PostToolUse", now=T0 + 5)
    assert "direct" in line
    assert any(e["kind"] == "ack" and e["from"] == TWO for _, e in net.entries())


def test_hook_renews_the_lease_at_most_once_a_minute():
    opened()
    net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0)
    net.hook_line(hook_call("bbbbbbbb-1", "PostToolUse"), "PostToolUse", now=T0 + 30)
    assert net.load_stations()["stations"][TWO]["last"] == T0
    net.hook_line(hook_call("bbbbbbbb-1", "PostToolUse"), "PostToolUse", now=T0 + 61)
    assert net.load_stations()["stations"][TWO]["last"] == T0 + 61


def test_a_hook_call_inside_the_lease_rewrites_nothing(monkeypatch):
    """C6: every tool call rewrote net-stations.json although nothing in it had changed."""
    opened()
    net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0)
    saved = []
    real = net.save_stations
    monkeypatch.setattr(net, "save_stations", lambda st: (saved.append(1), real(st)))
    net.hook_line(hook_call("bbbbbbbb-1", "PreToolUse"), "PreToolUse", now=T0 + 30)
    assert saved == []
    net.hook_line(hook_call("bbbbbbbb-1", "PreToolUse"), "PreToolUse", now=T0 + 61)    # the lease is renewed
    assert saved == [1] and net.load_stations()["stations"][TWO]["last"] == T0 + 61


def test_the_net_is_read_from_its_json_cache_until_net_yaml_changes(monkeypatch):
    """C6: importing the YAML parser was ~16 ms of every tool call."""
    import sys
    opened()
    first = net.load_net()
    assert (net.coord() / "NET.cache.json").is_file()
    monkeypatch.setitem(sys.modules, "yaml", None)                  # any import of yaml now fails
    assert net.load_net() == first
    monkeypatch.undo()
    n = dict(first, epoch=first["epoch"] + 7, note="a longer file than before")
    net.save_net(n)
    assert net.load_net()["epoch"] == first["epoch"] + 7            # a changed file is parsed again


def test_hook_tells_zero_once_per_change_which_stations_went_quiet():
    opened()
    net.checkin(GEM, now=T0, env={})
    later = T0 + 1801
    a = net.hook_line(hook_call("aaaaaaaa-1"), "UserPromptSubmit", now=later)
    assert f"{GEM} SILENT" in a
    b = net.hook_line(hook_call("aaaaaaaa-1"), "UserPromptSubmit", now=later + 5)
    assert "SILENT" not in b


def test_hook_never_raises(monkeypatch):
    monkeypatch.setattr(net, "load_net", lambda: 1 / 0)
    assert "NET" not in net.hook_line(hook_call("x"), "UserPromptSubmit")
    assert net.hook_line(hook_call("x"), "UserPromptSubmit") == ""


def test_room_hook_carries_the_net(monkeypatch, tmp_path, capsys):
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    opened()
    room.hook(io.StringIO(json.dumps(hook_call("bbbbbbbb-1"))))
    out = json.loads(capsys.readouterr().out)
    assert "Zero is" in out["hookSpecificOutput"]["additionalContext"]


# ---------------------------------------------------------------- time

def test_clock_levels_and_skew_from_the_round_trip():
    assert clock.level(0.5) == "OK" and clock.level(-2.5) == "WARN" and clock.level(31) == "STOP"
    assert clock.level(None) == "WARN"
    ticks = iter([100.0, 100.2])
    r = clock.skew(lambda: {"ok": True, "time": 103.1}, now=lambda: next(ticks))
    assert r["ok"] and abs(r["skew"] - 3.0) < 1e-9 and clock.level(r["skew"]) == "WARN"
    r = clock.skew(lambda: {"ok": False, "error": "refused"}, now=lambda: 1.0)
    assert not r["ok"] and r["skew"] is None


def test_clock_check_worst_wins(monkeypatch):
    monkeypatch.setattr(clock, "local", lambda run=None: {"synced": True, "detail": "ok", "fix": ""})
    r = clock.check(["VAIO"], lambda d: {"ok": True, "time": time.time() + 45})
    assert r["level"] == "STOP" and "VAIO" in "\n".join(clock.lines(r))
    monkeypatch.setattr(clock, "local", lambda run=None: {"synced": False, "detail": "CMOS", "fix": "w32tm"})
    assert clock.check([], None)["level"] == "WARN"


# ---------------------------------------------------------------- schedule

def test_schedule_plans_are_the_owner_s_own_context_and_reversible(tmp_path):
    w = schedule.plan("win32", python="C:/py/python.exe", root=tmp_path, state=tmp_path)
    assert w[0][0] == "write-utf16" and "InteractiveToken" in w[0][2] and "LeastPrivilege" in w[0][2]
    assert "PT15M" in w[0][2] and "net check --scheduled" in w[0][2]
    assert w[1][1][:3] == ["schtasks", "/Create", "/TN"]
    lx = schedule.plan("linux", python="/usr/bin/python3", root=tmp_path, home=tmp_path)
    assert "OnCalendar=*:0/15" in lx[1][2] and lx[-1][1][-1] == "fieldkit-net.timer"
    assert schedule.remove_plan("win32")[0][1][:2] == ["schtasks", "/Delete"]
    assert schedule.plan("sunos") == []


def test_schedule_apply_writes_and_runs(tmp_path):
    ran = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    steps = [("write", tmp_path / "a" / "x.timer", "t"), ("run", ["true"]), ("rm", tmp_path / "a" / "x.timer")]
    assert schedule.apply(steps, run=lambda argv, **k: ran.append(argv) or R(), say=lambda s: None) == 0
    assert ran == [["true"]] and not (tmp_path / "a" / "x.timer").exists()


# ---------------------------------------------------------------- the command line

def run(argv, capsys):
    rc = net.main(argv)
    return rc, capsys.readouterr()


def test_cli_alias_words_reach_the_same_verbs(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))        # no household: the real cable is never touched
    rc, out = run([], capsys)
    assert rc == net.NONET and "ZERO NOT ESTABLISHED" in out.out
    rc, out = run(["establish", "--name", "command post", "--2ic", "claude:bbbbbbbb"], capsys)
    assert rc == 0 and "ZERO HAS BEEN ESTABLISHED" in out.out and f"2IC = {TWO}" in out.out
    rc, out = run(["sitrep"], capsys)
    assert "ROLL CALL R-" in out.out
    rc, out = run(["comms"], capsys)
    assert out.out.startswith("COMMS CHECK")
    rc, out = run(["log", "--verify"], capsys)
    assert rc == 0 and "INTACT" in out.out
    rc, out = run(["--json"], capsys)
    assert json.loads(out.out)["established"] is True


def test_cli_refusals_exit_4(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    run(["open"], capsys)
    rc, out = run(["open", "--as", "claude:bbbbbbbb"], capsys)
    assert rc == net.REFUSED and "split-brain" in out.err
    rc, out = run(["checkin", "--as", "gemini:g1", "--job", "x"], capsys)
    assert rc == net.REFUSED and "sitrep incomplete" in out.err
    rc, out = run(["rollcall", "--as", "gemini:g1"], capsys)
    assert rc == net.REFUSED and "not Zero" in out.err


def test_cli_checkin_send_ack_close(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    run(["open"], capsys)
    rc, out = run(["checkin", "--as", "gemini:g1", "--job", "j", "--state", "idle", "--branch", "-", "--waiting", "-",
                   "--not-verified", "-", "--shared", "-"], capsys)
    assert rc == 0 and "CHECKED IN: gemini:g1@HOSTA" in out.out and "sitrep recorded" in out.out
    rc, out = run(["send", "radio", "check"], capsys)
    mid = out.out.split()[1].rstrip(",")
    rc, out = run(["ack", mid, "--as", "gemini:g1"], capsys)
    assert rc == 0 and f"ACKED {mid}" in out.out
    rc, out = run(["close"], capsys)
    assert rc == 0 and "NET CLOSED" in out.out


def test_fieldkit_net_routes_zero_verbs_and_keeps_the_network_tool(monkeypatch, capsys):
    from fieldkit import cli
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    assert cli.main(["zero"]) == net.NONET
    assert cli.main(["net"]) == net.NONET
    assert cli.main(["net", "comms"]) == net.NONET
    assert "rollcall" in cli.ZERO_VERBS and "route" not in cli.ZERO_VERBS


def test_doctor_reports_each_item(tmp_path):
    class R:
        returncode, stdout, stderr = 1, "", ""
    rows = {i: o for i, o, _ in net.doctor(settings=tmp_path / "s.json", gemini=tmp_path / "g.md",
                                          agents=tmp_path / "a.md", run=lambda *a, **k: R())}
    assert rows["net log"] is True and rows["GEMINI.md rule"] is False and rows["scheduled comms check"] is False
    (tmp_path / "g.md").write_text("run fieldkit zero checkin at start", encoding="utf-8")
    rows = {i: o for i, o, _ in net.doctor(settings=tmp_path / "s.json", gemini=tmp_path / "g.md",
                                          agents=tmp_path / "a.md", run=lambda *a, **k: R())}
    assert rows["GEMINI.md rule"] is True


def test_hook_decides_well_under_100_ms_on_a_busy_log():
    """Owner 2026-10-26: hooks are cheap. A quiet hook call on a net with 2000 log lines and 30 stations: no
    subprocess, no network, under 100 ms (measured, median of 20)."""
    opened()
    for i in range(30):
        net.checkin(f"gemini:s{i}@{HOST}", now=T0 + i, env={}, ingest=False)
    for i in range(2000):
        net.ack(GEM, f"M-{i}", now=T0 + 100 + i)
    net.hook_line(hook_call("bbbbbbbb-1"), "UserPromptSubmit", now=T0 + 3000)
    times = []
    for i in range(20):
        t = time.perf_counter()
        net.hook_line(hook_call("bbbbbbbb-1", "PostToolUse"), "PostToolUse", now=T0 + 3001 + i)
        times.append(time.perf_counter() - t)
    assert sorted(times)[10] < 0.1, f"median {sorted(times)[10] * 1000:.1f} ms"


def test_guard_a_missing_station_reaches_only_the_tmp_inbox(tmp_path):
    """The real check() path, no inbox_add injected: the item lands in the test's tmp inbox, never the owner's."""
    from pathlib import Path
    from fieldkit.autopilot import inbox
    real = Path.home() / ".fieldkit" / "autopilot" / "inbox"
    before = sorted(p.name for p in real.glob("*.json")) if real.is_dir() else []
    opened()
    net.checkin(GEM, now=T0, env={})
    net.check(heal=False, now=T0 + 3601)
    assert str(inbox.INBOX).startswith(str(tmp_path))
    got = [inbox.ledger_file()] if inbox.ledger_file().is_file() else []   # the tmp agent ledger
    assert got and any(GEM in p.read_text(encoding="utf-8") for p in got)
    after = sorted(p.name for p in real.glob("*.json")) if real.is_dir() else []
    assert after == before


# ---------------------------------------------------------------- C2: a station on the other computer checks in

def test_hook_on_the_far_computer_learns_the_net_from_the_cable_and_checks_in_there(monkeypatch):
    """2026-10-28 on the VAIO: Zero's open and 8 handovers sat unread in the link inbox, no NET.yaml, so the hook
    said nothing and no VAIO station ever reached Zero. Now the hook ingests the cable and pushes its check-in."""
    monkeypatch.setattr(net, "ingest_cable", INGEST)
    monkeypatch.setattr(net, "machine", lambda: "VAIO")
    monkeypatch.setattr(net, "tag_of", lambda s: "[VAIO-LNX]" if s.endswith("@VAIO") else "")
    cab = FakeCable(["HOSTA"])
    monkeypatch.setattr(ch, "Cable", lambda *a, **k: cab)
    far_open = {"kind": "open", "id": "E-open", "from": ZERO, "epoch": 1,
                "data": {"net": {"net": "open", "epoch": 1, "interval_min": 15,
                                 "zero": {"station": ZERO, "machine": HOST}}}}
    monkeypatch.setattr(ch, "inbox_items", lambda since: [(3, far_open)] if since < 3 else [])
    call = {"session_id": "ffffffff-1", "cwd": "/x"}
    assert "Zero is " + ZERO in net._net_line(call, "UserPromptSubmit", now=T0)
    me = "claude:ffffffff@VAIO"
    assert [e["kind"] for e in cab.pushed] == ["checkin"] and cab.pushed[0]["from"] == me
    assert cab.pushed[0]["data"]["tag"] == "[VAIO-LNX]"
    net._net_line(call, "UserPromptSubmit", now=T0 + 120)             # within the interval: not sent again
    assert len(cab.pushed) == 1
    net._net_line(call, "UserPromptSubmit", now=T0 + 15 * 60 + 1)     # an interval later: the far lease renewed
    assert len(cab.pushed) == 2 and cab.pushed[1]["id"] != cab.pushed[0]["id"]


def test_a_hook_on_zeros_own_computer_sends_nothing_over_the_cable(monkeypatch):
    cab = FakeCable(["VAIO"])
    monkeypatch.setattr(ch, "Cable", lambda *a, **k: cab)
    opened()
    net._net_line({"session_id": "cccccccc-1"}, "UserPromptSubmit", now=T0)
    assert cab.pushed == []


def test_a_far_checkin_keeps_its_machine_tag_and_status_counts_each_machine(monkeypatch):
    monkeypatch.setattr(net, "ingest_cable", INGEST)
    opened()
    far = {"kind": "checkin", "id": "E-far2", "from": "claude:eeeeeeee@VAIO", "to": ZERO,
           "data": {"by": "hook", "tag": "[VAIO-LNX]"}, "epoch": 1}
    net.ingest_cable([(9, far)], now=T0)
    assert net.load_stations()["stations"]["claude:eeeeeeee@VAIO"]["tag"] == "[VAIO-LNX]"
    r = net.status(now=T0)
    row = next(x for x in r["rows"] if x["station"] == "claude:eeeeeeee@VAIO")
    assert row["tag"] == "[VAIO-LNX]"
    text = "\n".join(net.status_lines(r, now=T0))
    assert "[VAIO-LNX] claude:eeeeeeee@VAIO" in text and "[VAIO-LNX] 1 (1 UP)" in text


# ---------------------------------------------------------------- retire: Zero archives only into a new Zero
# owner 2026-10-30: "we can ALLOW 0 to archive itself AS LONG AS A NEW ZERO with a handover is created"

def _up(station, now):
    st = net.load_stations()
    net.renew(st, station, net.load_net(), now)
    net.save_stations(st)


def _arch(sid, target="self"):
    return {"tool_name": "mcp__ccd_session_mgmt__archive_session", "session_id": sid,
            "tool_input": {"session_id": target}}


def test_retire_refuses_zero_until_handover_new_zero_up_and_handoff_written(tmp_path):
    import os
    from fieldkit.zero import retire
    opened()
    v = retire.verdict(ZERO, now=T0 + 10)
    assert not v["ok"] and "hold the Zero seat" in v["reason"]
    assert retire.verdict(TWO, now=T0 + 10)["ok"]                       # never Zero: free to go
    net.on_rotate("aaaaaaaa-1", now=T0 + 20)                          # a pending rotation is not a handover
    assert "rotation is pending" in retire.verdict(ZERO, now=T0 + 30)["reason"]
    net.handover(ZERO, to=TWO, channels=[FakeChannel()], now=T0 + 40)
    v = retire.verdict(ZERO, now=T0 + 50)
    assert not v["ok"] and "not UP" in v["reason"]                     # the new Zero never checked in
    _up(TWO, T0 + 60)
    v = retire.verdict(ZERO, now=T0 + 70)
    assert not v["ok"] and "HANDOFF.md" in v["reason"]                 # no handover file yet
    f = tmp_path / "HANDOFF.md"
    f.write_text("old", encoding="utf-8")
    os.utime(f, (T0 - 3600, T0 - 3600))                               # written before the handover: stale
    assert "older than the handover" in retire.verdict(ZERO, now=T0 + 80)["reason"]
    os.utime(f, (T0 + 75, T0 + 75))
    assert retire.verdict(ZERO, now=T0 + 80)["ok"]


def test_retire_guard_on_archive_session(tmp_path):
    from fieldkit.autopilot import toolrouter
    from fieldkit.zero import retire
    opened()
    z = dict(net.load_net())
    z["zero"] = dict(z["zero"], host_session="local_zz")
    net.save_net(z)
    deny = toolrouter.verdict(_arch("aaaaaaaa-1"))
    assert deny and "ZERO RETIRE" in deny["hookSpecificOutput"]["permissionDecisionReason"]
    assert retire.pretool(_arch("bbbbbbbb-2", "local_zz"))               # another session archiving Zero's session
    assert retire.pretool(_arch("bbbbbbbb-2")) is None                   # a worker archiving itself
    assert retire.pretool(_arch("aaaaaaaa-1", "local_other")) is None    # Zero archiving some other session
    assert retire.pretool({"tool_name": "Bash", "session_id": "aaaaaaaa-1", "tool_input": {"command": "ls"}}) is None


def test_retire_cli_exit_codes(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    opened()
    assert net.main(["retire"]) == net.REFUSED and "RETIRE NOT YET" in capsys.readouterr().out
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "bbbbbbbb-2")
    assert net.main(["retire"]) == net.OK
