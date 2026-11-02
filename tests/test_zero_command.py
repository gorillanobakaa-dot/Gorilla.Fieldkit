"""fieldkit zero, set 2: the command layer (2026-10-26, owner: "someone has to be in command ... to ask the idle
devices why are they doing and why aren't they working towards the common goal"). Orders, ground claims enforced by
the PreToolUse hook, drift, idle-without-done, the wake, orders carried across rotation, and the owner's hard lines:
no prompts, no message when quiet, the hook decision under 100 ms. Fake channels, fake git, a fixed clock."""
import builtins
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit.autopilot import toolrouter
from fieldkit.zero import channels as ch
from fieldkit.zero import claims, command, net

T0 = 1_800_000_000.0
HOST = "HOSTA"
ZERO = f"claude:aaaaaaaa@{HOST}"
TWO = f"claude:bbbbbbbb@{HOST}"
W1 = f"claude:cccccccc@{HOST}"
W2 = f"claude:dddddddd@{HOST}"
SIT = {"job": "j", "state": "working", "branch": "feat/x", "waiting": "-", "not_verified": "-", "shared": "-"}


class FakeChannel:
    name, mode = "fake", "push"

    def __init__(self):
        self.sent = []

    def push(self, entry, n):
        self.sent.append(entry)
        return [{"channel": "fake", "target": entry.get("to"), "ok": True, "detail": "sent"}]


class FakeCable:
    def devices(self):
        return []

    def hello(self, d):
        return {"ok": False}

    def push(self, e, n):
        return []


def nogit(args, cwd):
    return None


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    c = tmp_path / "coord"
    c.mkdir()
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(c))
    monkeypatch.setattr(net, "machine", lambda: HOST)
    monkeypatch.setattr(claims.socket, "gethostname", lambda: HOST)
    monkeypatch.setattr(net, "_key", lambda: None)
    monkeypatch.setattr(net, "ingest_cable", lambda *a, **k: 0)
    monkeypatch.setattr(ch, "default", lambda: [FakeChannel()])
    monkeypatch.setattr(ch, "Cable", lambda *a, **k: FakeCable())
    # pytest's tmp_path lives under the temp folder, which is free ground: the tests free only the coordination folder
    monkeypatch.setattr(command, "default_free", lambda: [claims.norm(c)])
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(AssertionError("asked the owner")))
    # never the owner's real inbox (2026-10-26: the first runs of this file put 70 fixture items there)
    from fieldkit.autopilot import inbox
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    return tmp_path


def W(tmp_path, *p):
    return str(tmp_path.joinpath("work", *p))


def opened(second=TWO):
    return net.open_net(ZERO, second=second, now=T0, env={"CLAUDE_CODE_SESSION_ID": "aaaaaaaa-1"})


def checked(st, sid, now=T0 + 1, sit=None):
    return net.checkin(st, sit, now=now, env={"CLAUDE_CODE_SESSION_ID": sid})


def tasked(tmp_path, station=W1, area=("a",), now=T0 + 2, **kw):
    return command.task(ZERO, station, "build the thing", [W(tmp_path, x) for x in area], "PR green", now=now,
                        git=kw.pop("git", nogit), channels=[FakeChannel()], **kw)


def call(sid, tool="Edit", path=None, command_=None, cwd=""):
    ti = {"file_path": path} if path is not None else {"command": command_}
    return {"session_id": sid, "tool_name": tool, "tool_input": ti, "cwd": cwd}


# ---------------------------------------------------------------- 1. orders

def test_only_zero_tasks_and_an_order_needs_objective_area_done(tmp_path):
    opened()
    with pytest.raises(net.Refused, match="not Zero"):
        command.task(W1, W2, "x", [W(tmp_path, "a")], "y", now=T0, git=nogit)
    with pytest.raises(net.Refused, match="--objective, --area and --done"):
        command.task(ZERO, W1, "x", [W(tmp_path, "a")], "", now=T0, git=nogit)
    with pytest.raises(net.Refused, match="stale epoch"):
        command.task(ZERO, W1, "x", [W(tmp_path, "a")], "y", epoch=9, now=T0, git=nogit)


def test_task_stores_the_order_writes_claims_logs_and_tells_the_station(tmp_path):
    opened()
    fake = FakeChannel()
    r = command.task(ZERO, W1, "build  the thing", [W(tmp_path, "a")], "PR green", now=T0 + 2, git=nogit,
                     channels=[fake])
    o = net.load_stations()["stations"][W1]["order"]
    assert o["id"].startswith("O-") and o["objective"] == "build the thing" and o["by"] == ZERO
    assert o["area"] == [claims.norm(W(tmp_path, "a"))]
    cl = json.loads((tmp_path / "coord" / "net-claims.json").read_text(encoding="utf-8"))
    assert cl["claims"][W1]["order"] == o["id"]
    kinds = [e["kind"] for _, e in net.entries()]
    assert "order" in kinds and fake.sent and fake.sent[0]["to"] == W1 and "ORDER" in fake.sent[0]["text"]
    assert r["station"] == W1


def test_every_checkin_carries_the_order_id(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    o = tasked(tmp_path)["order"]
    r = checked(W1, "cccccccc-1", now=T0 + 3)
    assert r["entry"]["data"]["order"] == o["id"]


def test_two_live_orders_never_overlap_but_a_released_one_frees_the_ground(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    checked(W2, "dddddddd-1")
    tasked(tmp_path, W1, ("a",))
    with pytest.raises(net.Refused, match="ground overlaps"):
        tasked(tmp_path, W2, ("a/sub",))
    with pytest.raises(net.Refused, match="ground overlaps"):
        command.task(ZERO, W2, "x", [str(tmp_path / "work")], "y", now=T0 + 3, git=nogit)
    tasked(tmp_path, W2, ("b",))                                            # disjoint ground is fine
    with pytest.raises(net.Refused, match="no open order"):
        command.release(ZERO, W2 + "x", now=T0 + 4)
    r = command.release(ZERO, W1, finished=True, now=T0 + 4)
    assert r["order"]["outcome"] == "done"
    assert W1 not in json.loads((tmp_path / "coord" / "net-claims.json").read_text(encoding="utf-8"))["claims"]
    tasked(tmp_path, W2, ("a/sub",), now=T0 + 5)                            # W2 re-tasked onto the freed ground


def test_cli_task_orders_release(tmp_path, monkeypatch, capsys):
    opened()
    monkeypatch.setattr(command, "_git", nogit)
    monkeypatch.setattr(command, "repos_of", lambda area, git=None: [])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "aaaaaaaa-1")
    assert net.main(["task", W1, "--objective", "obj", "--area", W(tmp_path, "a") + "," + W(tmp_path, "b"),
                     "--done", "green"]) == net.OK
    assert "ORDER O-" in capsys.readouterr().out
    assert net.main(["orders"]) == net.OK
    out = capsys.readouterr().out
    assert "OPEN" in out and "objective: obj" in out and claims.norm(W(tmp_path, "b")) in out
    assert net.main(["release", W1, "--finished"]) == net.OK
    assert "DONE" in capsys.readouterr().out
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cccccccc-1")
    assert net.main(["task", W2, "--objective", "o", "--area", "x", "--done", "d"]) == net.REFUSED


# ---------------------------------------------------------------- 2. ground claims, enforced

def test_norm_one_spelling_per_folder(tmp_path):
    a = claims.norm(str(tmp_path / "x") + os.sep)
    assert a == claims.norm(str(tmp_path / "x")) and not a.endswith("/") and "\\" not in a
    assert claims.norm("y", cwd=str(tmp_path)) == claims.norm(str(tmp_path / "y"))
    if os.name == "nt":
        assert claims.norm("/c/Data/X") == "c:/data/x"
        assert claims.norm("C:\\Data\\X\\") == "c:/data/x"
    assert claims.within("c:/a/b", "c:/a") and not claims.within("c:/ab", "c:/a") and claims.overlap("c:/a", "c:/a/b")


def test_edit_on_ground_another_station_holds_is_refused_own_ground_allowed(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    checked(W2, "dddddddd-1")
    tasked(tmp_path, W1, ("a",))
    tasked(tmp_path, W2, ("b",))
    r = claims.decide(call("dddddddd-1", path=W(tmp_path, "a", "f.py")), now=T0 + 10)
    assert r and r["holder"] == W1 and "ground held by" in r["why"]
    assert claims.decide(call("cccccccc-1", path=W(tmp_path, "a", "f.py")), now=T0 + 10) is None
    assert claims.decide(call("cccccccc-1", "MultiEdit", W(tmp_path, "a", "g.py")), now=T0 + 10) is None
    assert claims.decide(call("eeeeeeee-1", "Write", W(tmp_path, "b", "g.py")), now=T0 + 10)["holder"] == W2
    assert claims.decide(call("eeeeeeee-1", "Read", W(tmp_path, "b", "g.py")), now=T0 + 10) is None


def test_outside_the_area_on_shared_ground_is_refused_free_ground_and_other_folders_are_not(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    tasked(tmp_path, W1, ("a",))
    shared = str(tmp_path / "Documents" / "Scripts" / "Fieldkit" / "x.py")
    r = claims.decide(call("cccccccc-1", path=shared), now=T0 + 10)
    assert r and r["holder"] is None and "outside your ordered area" in r["why"]
    assert claims.decide(call("cccccccc-1", path=str(tmp_path / "coord" / "PLAN.md")), now=T0 + 10) is None
    assert claims.decide(call("cccccccc-1", path=str(tmp_path / "elsewhere" / "n.md")), now=T0 + 10) is None
    assert claims.decide(call("eeeeeeee-1", path=shared), now=T0 + 10) is None      # no order: not judged


def test_free_ground_is_writable_even_on_shared_ground_and_inside_a_claim(tmp_path, monkeypatch):
    opened()
    checked(W1, "cccccccc-1")
    checked(W2, "dddddddd-1")
    fk = tmp_path / "Documents" / "Scripts" / "Fieldkit"
    tasked(tmp_path, W1, ("a",))
    command.task(ZERO, W2, "o", [str(fk)], "d", now=T0 + 3, git=nogit, channels=[FakeChannel()])
    plan = str(fk / "state" / "coordination" / "PLAN.md")
    assert claims.decide(call("cccccccc-1", path=plan), now=T0 + 10)["holder"] == W2
    monkeypatch.setattr(command, "default_free", lambda: [claims.norm(fk / "state" / "coordination")])
    command.write_claims(net.load_stations())
    assert claims.decide(call("cccccccc-1", path=plan), now=T0 + 10) is None


def test_git_commands_are_judged_at_the_folder_they_act_in(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    checked(W2, "dddddddd-1")
    tasked(tmp_path, W1, ("repo/sub1",))
    tasked(tmp_path, W2, ("repo/sub2",))
    repo = W(tmp_path, "repo")
    # its own area is inside the repository: a commit at the repository root is its own
    assert claims.decide(call("cccccccc-1", "Bash", command_=f'git -C "{repo}" commit -m x'), now=T0 + 10) is None
    # a reset inside another station's area is refused; so is a stash run from there (cd)
    r = claims.decide(call("cccccccc-1", "Bash", command_=f'git -C "{W(tmp_path, "repo", "sub2")}" reset --hard'),
                      now=T0 + 10)
    assert r and r["holder"] == W2
    r = claims.decide(call("eeeeeeee-1", "PowerShell", command_="git stash", cwd=W(tmp_path, "repo", "sub2")),
                      now=T0 + 10)
    assert r and r["holder"] == W2
    # a station with no ground in the repository acting at its root touches both: refused
    assert claims.decide(call("eeeeeeee-1", "Bash", command_=f'git -C "{repo}" checkout -b x'), now=T0 + 10)
    # reads and non-git commands pass
    for c in (f'git -C "{repo}" status', f'git -C "{repo}" log', f'git -C "{repo}" worktree list',
              f'git -C "{repo}" stash list', f'git -C "{repo}" tag -l', f'ls "{repo}"'):
        assert claims.decide(call("eeeeeeee-1", "Bash", command_=c), now=T0 + 10) is None, c


def test_fail_open_without_net_claims_session_or_on_garbage(tmp_path):
    p = W(tmp_path, "a", "f.py")
    assert claims.decide(call("dddddddd-1", path=p), now=T0) is None                 # no net
    opened()
    assert claims.decide(call("dddddddd-1", path=p), now=T0) is None                 # no claims file
    checked(W1, "cccccccc-1")
    tasked(tmp_path, W1, ("a",))
    assert claims.decide(call(None, path=p), now=T0 + 10) is None                    # no session id
    assert claims.decide({"session_id": "x", "tool_name": "Edit", "tool_input": None}, now=T0 + 10) is None
    (tmp_path / "coord" / "net-claims.json").write_text("{not json", encoding="utf-8")
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 10) is None
    tasked(tmp_path, W1, ("a",), now=T0 + 11)
    net.close_net(ZERO, channels=[FakeChannel()], now=T0 + 12)
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 13) is None            # closed net enforces nothing


def test_a_missing_or_signed_off_holder_holds_nothing_a_pretasked_one_does(tmp_path):
    opened()
    tasked(tmp_path, W1, ("a",))                       # tasked before it ever checked in: it holds for a day
    p = W(tmp_path, "a", "f.py")
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 3 * 3600)["holder"] == W1
    checked(W1, "cccccccc-1", now=T0 + 3 * 3600)
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 3 * 3600 + 30 * 60 + 1)       # SILENT still holds
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 3 * 3600 + 60 * 60 + 1) is None  # MISSING: free
    net.checkin(W1, off=True, now=T0 + 3 * 3600 + 5, env={})
    assert claims.decide(call("dddddddd-1", path=p), now=T0 + 3 * 3600 + 6) is None          # signed off: free


def test_the_toolrouter_hook_denies_never_asks_and_logs_the_refusal(tmp_path, capsys):
    opened()
    checked(W1, "cccccccc-1")
    tasked(tmp_path, W1, ("a",))
    c = call("dddddddd-1", "Write", W(tmp_path, "a", "f.py"))
    assert toolrouter.hook(io.StringIO(json.dumps(c))) == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "GROUND CLAIM" in out["permissionDecisionReason"]
    assert out["permissionDecision"] != "ask"
    refused = [e for _, e in net.entries() if e["kind"] == "refused"]
    assert refused and refused[-1]["to"] == W1 and refused[-1]["data"]["tool"] == "Write"
    # an allowed edit is not refused (C6: it may carry the room's context, here the order sent to the station); a
    # Bash command still goes through the router's own routes
    toolrouter.hook(io.StringIO(json.dumps(call("cccccccc-1", "Edit", W(tmp_path, "a", "f.py")))))
    assert "permissionDecision" not in capsys.readouterr().out
    toolrouter.hook(io.StringIO(json.dumps(call("cccccccc-1", "Bash", command_="du -sh /"))))
    assert "TOOL ROUTER" in capsys.readouterr().out


def _big_state(tmp_path, n=40):
    opened()
    st = net.load_stations()
    for i in range(n):
        s = f"claude:{i:08d}@{HOST}"
        net.renew(st, s, net.load_net(), T0, session=f"{i:08d}-x")
        st["stations"][s]["order"] = {"id": f"O-{i}", "objective": "o" * 100, "area": [claims.norm(W(tmp_path, f"a{i}"))],
                                      "done": "d", "by": ZERO, "at": T0}
    st["meta"]["seen_ids"] = [f"M-{i:08x}" for i in range(2000)]
    net.save_stations(st)
    command.write_claims(st)


def test_hook_decision_is_well_under_100_ms_with_no_subprocess(tmp_path, monkeypatch):
    _big_state(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess")))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess")))
    calls = [call("zzzzzzzz-1", "Edit", W(tmp_path, "a7", "f.py")), call("zzzzzzzz-1", "Edit", W(tmp_path, "free.py")),
             call("zzzzzzzz-1", "Bash", command_=f'git -C "{W(tmp_path, "a3")}" commit -m x'),
             call("zzzzzzzz-1", "Bash", command_="python -m pytest -q")]
    for c in calls:
        claims.decide(c, now=T0 + 10)                                         # warm the imports once
    worst = 0.0
    for _ in range(20):
        for c in calls:
            t = time.perf_counter()
            claims.decide(c, now=T0 + 10)
            worst = max(worst, time.perf_counter() - t)
    assert claims.decide(calls[0], now=T0 + 10)["holder"] == f"claude:{7:08d}@{HOST}"
    assert worst < 0.100, f"slowest decision {worst * 1000:.1f} ms"


def test_the_whole_hook_process_measured_and_loads_no_yaml(tmp_path):
    """The full PreToolUse process (Python start + the router + the claims): measured; Python's own start on this
    laptop is ~150-200 ms, so the bar here is that the claims add no YAML parser and no net code on an allowed call.
    C6: the same process then runs the room's part (room line, net lease, hog registry), which was a second Python
    start per call; it is measured after the decision and must cost less than the start it replaces."""
    _big_state(tmp_path)
    c = json.dumps(call("zzzzzzzz-1", "Edit", W(tmp_path, "free.py")))
    code = ("import sys,json,io,time;t=time.perf_counter();from fieldkit.autopilot import toolrouter;"
            "c=json.loads(sys.argv[1]);toolrouter.verdict(c);"
            "d={'yaml':'yaml' in sys.modules,'net':'fieldkit.zero.net' in sys.modules,"
            "'ms':(time.perf_counter()-t)*1000};"
            "toolrouter._room(c);d['all_ms']=(time.perf_counter()-t)*1000;print(json.dumps(d))")
    env = dict(os.environ, FIELDKIT_COORDINATION_DIR=str(tmp_path / "coord"))
    t = time.perf_counter()
    r = subprocess.run([sys.executable, "-c", code, c], capture_output=True, text=True, env=env,
                       cwd=str(Path(__file__).resolve().parents[1]), timeout=60)
    wall = (time.perf_counter() - t) * 1000
    got = json.loads(r.stdout.strip().splitlines()[-1])
    print(f"hook process: {wall:.0f} ms wall, {got['ms']:.1f} ms decision, {got['all_ms']:.1f} ms with the room")
    assert not got["yaml"] and not got["net"]
    assert got["ms"] < 100
    assert got["all_ms"] < 150


# ---------------------------------------------------------------- 3. drift

def test_git_drift_paths_outside_the_area_from_base_and_working_tree(tmp_path):
    repo = claims.norm(W(tmp_path, "repo"))
    o = {"repos": [{"top": repo, "base": "abc"}]}
    own = [repo + "/fieldkit/zero"]

    def git(args, cwd):
        if args[0] == "diff":
            assert args[2:] == ["abc", "HEAD"]
            return "fieldkit/zero/command.py\nfieldkit/autopilot/goal.py\n"
        return " M fieldkit/zero/net.py\n?? fieldkit/core/new.py\n"
    out = command.git_drift(o, own, [], [], git)
    assert out == [repo + "/fieldkit/autopilot/goal.py", repo + "/fieldkit/core/new.py"]
    assert command.git_drift(o, own, [], [repo + "/fieldkit/core"], git) == [repo + "/fieldkit/autopilot/goal.py"]
    asked = []
    assert command.git_drift(o, [repo], [], [], lambda a, c: asked.append(a)) == [] and not asked  # top inside: no git


def _room(tmp_path, *lines):
    p = tmp_path / "ROOM.log"
    p.write_text("".join("\t".join(x) + "\n" for x in lines), encoding="utf-8")
    return p


def test_sweep_flags_drift_from_the_room_log_and_no_progress(tmp_path):
    opened()
    checked(W1, "cccccccc-1", sit=SIT)
    tasked(tmp_path, W1, ("a",))
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 60))
    room = _room(tmp_path, (at, "w1", "cccccccc-1", "edit", W(tmp_path, "b", "x.py")),
                 (at, "w1", "cccccccc-1", "edit", W(tmp_path, "a", "ok.py")),
                 (at, "other", "eeeeeeee-1", "edit", W(tmp_path, "b", "y.py")))
    zero = FakeChannel()
    r = command.sweep(net.load_net(), T0 + 120, channels_for=lambda s: [zero], git=nogit, room_log=room, board=False)
    drift = [f for f in r["findings"] if f["kind"] == "DRIFT"]
    assert len(drift) == 1 and claims.norm(W(tmp_path, "b", "x.py")) in drift[0]["text"]
    r = command.sweep(net.load_net(), T0 + 60 + 46 * 60, channels_for=lambda s: [zero], git=nogit, room_log=room,
                      board=False)
    assert any(f["kind"] == "NO PROGRESS" for f in r["findings"])


# ---------------------------------------------------------------- 4. idle without done, archive

def test_an_idle_station_is_asked_once_per_idle_period_and_its_answer_goes_on_the_board(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    tasked(tmp_path, W1, ("a",))
    (tmp_path / "coord" / "PLAN.md").write_text("# PLAN\n\n## Board\n\n| a | b | c | d |\n|---|---|---|---|\n",
                                                encoding="utf-8")
    sent = {}

    def chans(s):
        return [sent.setdefault(s, FakeChannel())]
    room = _room(tmp_path)
    late = T0 + 50 * 60
    r = command.sweep(net.load_net(), late, channels_for=chans, git=nogit, room_log=room)
    assert len(sent[W1].sent) == 1 and "why idle, what is blocking you" in sent[W1].sent[0]["text"]
    assert any(f["kind"] == "IDLE" for f in r["findings"]) and r["sent"]
    r2 = command.sweep(net.load_net(), late + 15 * 60, channels_for=chans, git=nogit, room_log=room)
    assert len(sent[W1].sent) == 1 and not r2["new"]                         # same idle period: asked once
    checked(W1, "cccccccc-1", now=late + 16 * 60, sit=dict(SIT, state="blocked", waiting="the VAIO cable"))
    command.sweep(net.load_net(), late + 17 * 60, channels_for=chans, git=nogit, room_log=room)
    board = (tmp_path / "coord" / "PLAN.md").read_text(encoding="utf-8")
    assert command.BOARD_KEY in board and "BLOCKED: waiting on the VAIO cable" in board


def test_resting_states_are_not_asked_and_a_sitrep_idle_is(tmp_path):
    opened()
    checked(W1, "cccccccc-1", sit=dict(SIT, state="waiting"))
    tasked(tmp_path, W1, ("a",))
    ch1 = FakeChannel()
    r = command.sweep(net.load_net(), T0 + 90 * 60, channels_for=lambda s: [ch1], git=nogit,
                      room_log=_room(tmp_path), board=False)
    assert not [f for f in r["findings"] if f["kind"] == "IDLE"]
    checked(W1, "cccccccc-1", now=T0 + 91 * 60, sit=dict(SIT, state="idle"))
    r = command.sweep(net.load_net(), T0 + 92 * 60, channels_for=lambda s: [ch1], git=nogit,
                      room_log=_room(tmp_path), board=False)
    assert [f for f in r["findings"] if f["kind"] == "IDLE"]


def test_a_done_station_with_no_open_pr_goes_on_the_archive_list(tmp_path):
    opened()
    checked(W1, "cccccccc-1", sit=dict(SIT, state="done"))
    checked(W2, "dddddddd-1", sit=dict(SIT, state="done"))
    tasked(tmp_path, W1, ("a",))
    tasked(tmp_path, W2, ("b",))
    fin = lambda: ([{"session": "cccccccc-1"}], [{"session": "dddddddd-1", "held_by": ["open PR"]}])
    r = command.sweep(net.load_net(), T0 + 10, channels_for=lambda s: [FakeChannel()], git=nogit,
                      room_log=_room(tmp_path), finished=fin, board=False)
    assert r["archive"] == [W1]


# ---------------------------------------------------------------- 5. the wake: only on a NEW finding

def test_quiet_net_sends_nothing_and_a_finding_wakes_zero_once(tmp_path):
    opened()
    chans = {}
    cf = lambda s: [chans.setdefault(s, FakeChannel())]
    plan = tmp_path / "coord" / "PLAN.md"
    plan.write_text("## Board\n\n| a | b |\n|---|---|\n", encoding="utf-8")
    r = command.sweep(net.load_net(), T0 + 10, channels_for=cf, git=nogit, room_log=_room(tmp_path))
    assert r["orders"] == 0 and not chans                                    # no orders: no code path sends
    assert command.BOARD_KEY not in plan.read_text(encoding="utf-8")         # and the Board is left alone
    checked(W1, "cccccccc-1", sit=SIT)
    tasked(tmp_path, W1, ("a",))
    checked(ZERO, "aaaaaaaa-1", now=T0 + 5)
    r = command.sweep(net.load_net(), T0 + 10, channels_for=cf, git=nogit, room_log=_room(tmp_path))
    assert not r["findings"] and not chans                                   # orders, nothing wrong: silence
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 20))
    room = _room(tmp_path, (at, "w1", "cccccccc-1", "edit", W(tmp_path, "b", "x.py")))
    r = command.sweep(net.load_net(), T0 + 30, channels_for=cf, git=nogit, room_log=room)
    assert r["woke"] == ZERO and len(chans[ZERO].sent) == 1 and "DRIFT" in chans[ZERO].sent[0]["text"]
    for i in range(1, 4):                                                    # the same finding: never again
        checked(ZERO, "aaaaaaaa-1", now=T0 + 30 + i * 60)
        checked(W1, "cccccccc-1", now=T0 + 30 + i * 60, sit=SIT)
        room = _room(tmp_path, (at, "w1", "cccccccc-1", "edit", W(tmp_path, "b", "x.py")))
        command.sweep(net.load_net(), T0 + 40 + i * 60, channels_for=cf, git=nogit, room_log=room)
    assert len(chans[ZERO].sent) == 1


def test_zero_silent_puts_one_inbox_item_naming_the_2ic_and_tells_the_2ic(tmp_path):
    opened()
    checked(TWO, "bbbbbbbb-1", now=T0 + 2 * 3600)
    checked(W1, "cccccccc-1", now=T0 + 2 * 3600, sit=SIT)
    tasked(tmp_path, W1, ("a",), now=T0 + 2 * 3600)
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 2 * 3600 + 10))
    room = _room(tmp_path, (at, "w1", "cccccccc-1", "edit", W(tmp_path, "b", "x.py")))
    added, chans = [], {}
    r = command.sweep(net.load_net(), T0 + 2 * 3600 + 20, channels_for=lambda s: [chans.setdefault(s, FakeChannel())],
                      inbox_add=lambda *a: added.append(a), git=nogit, room_log=room)
    assert len(added) == 1 and "MISSING" in added[0][0] and TWO in added[0][1] and "--assume" in added[0][2]
    assert ZERO not in chans and len(chans[TWO].sent) == 1 and r["woke"] == TWO


def test_the_comms_check_runs_the_sweep_and_warns_on_findings(tmp_path):
    opened()
    checked(ZERO, "aaaaaaaa-1", now=T0 + 1)
    checked(W1, "cccccccc-1", sit=dict(SIT, state="idle"))
    tasked(tmp_path, W1, ("a",))
    chans = {}
    r = net.check(heal=False, now=T0 + 60, board=False,
                  command_kw={"channels_for": lambda s: [chans.setdefault(s, FakeChannel())], "git": nogit,
                              "room_log": _room(tmp_path)})
    assert r["command"]["findings"] and r["level"] == net.WARN and W1 in chans


# ---------------------------------------------------------------- 6. rotation carries orders and ground

def test_a_rotated_station_leaves_its_order_for_its_relief_who_takes_it(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    o = tasked(tmp_path, W1, ("a",))["order"]
    assert net.on_rotate("eeeeeeee-1", now=T0 + 5) == ""                      # no order, not Zero: nothing
    line = net.on_rotate("cccccccc-1", now=T0 + 5)
    assert f"--relieve {W1}" in line and o["id"] in line
    relief = f"claude:ffffffff@{HOST}"
    r = net.checkin(relief, relieve=W1, now=T0 + 10, env={"CLAUDE_CODE_SESSION_ID": "ffffffff-1"})
    assert r["relieved"]["id"] == o["id"] and r["entry"]["data"]["order"] == o["id"]
    st = net.load_stations()["stations"]
    assert st[W1]["off"] and st[W1]["order"]["closed"] and command.active(st[relief])["area"] == o["area"]
    cl = json.loads((tmp_path / "coord" / "net-claims.json").read_text(encoding="utf-8"))["claims"]
    assert list(cl) == [relief]
    assert claims.decide(call("cccccccc-1", path=W(tmp_path, "a", "f.py")), now=T0 + 11)["holder"] == relief
    assert claims.decide(call("ffffffff-1", path=W(tmp_path, "a", "f.py")), now=T0 + 11) is None


def test_the_relief_window_holds_the_ground_and_a_live_station_cannot_be_relieved_unasked(tmp_path):
    opened()
    checked(W1, "cccccccc-1")
    tasked(tmp_path, W1, ("a",))
    with pytest.raises(net.Refused, match="left no relief"):
        net.checkin(W2, relieve=W1, now=T0 + 10, env={})
    with pytest.raises(net.Refused, match="no open order"):
        net.checkin(W1, relieve=W2, now=T0 + 10, env={})
    net.on_rotate("cccccccc-1", now=T0 + 20)
    # the rotated session went quiet long ago (MISSING), its ground still waits for the relief
    assert claims.decide(call("dddddddd-1", path=W(tmp_path, "a", "f.py")), now=T0 + 5 * 3600)["holder"] == W1


def test_zero_rotating_carries_its_order_through_handover_take(tmp_path):
    opened()
    tasked(tmp_path, ZERO, ("cp",))
    line = net.on_rotate("aaaaaaaa-1", now=T0 + 5)
    assert "handover --take" in line and "order and its ground go with it" in line
    relief = f"claude:ffffffff@{HOST}"
    net.handover(relief, take=True, channels=[FakeChannel()], now=T0 + 10, env={})
    st = net.load_stations()["stations"]
    assert command.active(st[relief]) and not command.active(st[ZERO])


# ---------------------------------------------------------------- guard: never the owner's real inbox

def test_a_silent_zero_wake_never_touches_the_real_inbox(tmp_path):
    from fieldkit.autopilot import hardlines, inbox
    real = hardlines.STATE / "inbox"
    before = sorted(p.name for p in real.glob("*.json")) if real.is_dir() else []
    assert inbox.INBOX != real and str(inbox.INBOX).startswith(str(tmp_path))
    opened()
    checked(W1, "cccccccc-1", sit=SIT)
    tasked(tmp_path, W1, ("a",))
    at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 3 * 3600))
    room = _room(tmp_path, (at, "w1", "cccccccc-1", "edit", W(tmp_path, "b", "x.py")))
    r = command.sweep(net.load_net(), T0 + 3 * 3600 + 5, channels_for=lambda s: [FakeChannel()], git=nogit,
                      room_log=room)                    # no inbox_add: the real code path, Zero MISSING
    assert r["woke"] in ("inbox", TWO) and inbox.ledger_file().is_file()   # the tmp agent ledger, not the owner
    after = sorted(p.name for p in real.glob("*.json")) if real.is_dir() else []
    assert after == before


# ---------------------------------------------------------------- doctor, defaults

def test_hook_doctor_reads_the_router_matcher_and_never_edits(tmp_path):
    s = tmp_path / "settings.json"
    hook = {"type": "command", "command": "python -m fieldkit.autopilot.toolrouter --hook"}
    s.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [hook]}]}}),
                 encoding="utf-8")
    before = s.read_text(encoding="utf-8")
    ok, detail, snip = command.hook_doctor(s)
    assert not ok and "Edit" in detail and "Write" in snip["matcher"] and s.read_text(encoding="utf-8") == before
    s.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": snip["matcher"], "hooks": [hook]}]}}), encoding="utf-8")
    assert command.hook_doctor(s)[0]
    assert not command.hook_doctor(tmp_path / "none.json")[0]


def test_default_free_ground_has_coordination_and_temp(monkeypatch, tmp_path):
    monkeypatch.undo()
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "c"))
    import importlib
    free = importlib.reload(command).default_free()
    import tempfile
    assert claims.norm(tmp_path / "c") in free and claims.norm(tempfile.gettempdir()) in free
