"""Zero friction (fieldkit/lifecycle): the friction audit, headless tasking, the session sweep and the done protocol.
Fixture desktop session stores and transcripts are written here; the link, the launcher and psutil are fakes."""
import json
import time
from pathlib import Path

import pytest

from fieldkit.lifecycle import desktop, friction, protocol, sweep, tasks
import fieldkit.lifecycle as lc

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_ROOM_DIR", str(tmp_path / "room"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    (tmp_path / "room").mkdir()
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setenv("FIELDKIT_CLAUDE_APP_DIRS", str(tmp_path / "app"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv(desktop.HOST_ENV, raising=False)


def rec(sid, title="t", archived=False, mode="bypassPermissions", last=NOW - 3600, prs=(), cli="", parent="",
        resolved=None, remote=False, awaiting=None, exempt=False):
    d = {"sessionId": sid, "title": title, "isArchived": archived, "permissionMode": mode, "lastActivityAt": last * 1000,
         "prs": list(prs), "cliSessionId": cli, "resolvedBackgroundTaskSuggestions": resolved or {},
         "bridgeSessionIds": ["session_x"] if remote else [], "interruptedAwaitingPerson": awaiting}
    if parent:
        d["spawnedFrom"] = {"sessionId": parent}
    if exempt:
        d["autoArchiveExempt"] = True
    return d


def store(tmp_path, *records, archived_idx=()):
    d = tmp_path / "app" / "claude-code-sessions" / "acct" / "org"
    d.mkdir(parents=True, exist_ok=True)
    for r in records:
        (d / f"{r['sessionId']}.json").write_text(json.dumps(r), encoding="utf-8")
    (d / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": list(archived_idx)}), encoding="utf-8")
    return d


def iso_ts(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def use(uid, name, inp, t=NOW):
    return {"timestamp": iso_ts(t), "message": {"content": [{"type": "tool_use", "id": uid, "name": name, "input": inp}]}}


def result(uid, text, t=NOW):
    return {"timestamp": iso_ts(t), "message": {"content": [{"type": "tool_result", "tool_use_id": uid,
                                                             "content": [{"type": "text", "text": text}]}]}}


def transcript(tmp_path, cli, rows):
    p = tmp_path / "cfg" / "projects" / "proj" / f"{cli}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


SS = "mcp__ccd_session__start_session"


def card_rows(tmp_path):
    return [
        use("u1", SS, {"initiation": "own_initiative", "title": "Open card"}),
        result("u1", 'Proposed "Open card" (task_id: task_aaaa1111). The user sees a card.'),
        use("u2", SS, {"title": "Dismissed"}),
        result("u2", 'Proposed "Dismissed" (task_id: task_bbbb2222). The user sees a card.'),
        use("u3", "mcp__ccd_session__dismiss_task", {"task_id": "task_bbbb2222"}),
        use("u4", SS, {"title": "Started"}),
        result("u4", 'Proposed "Started" (task_id: task_cccc3333).'),
        use("u5", SS, {"proposal_id": "task_cccc3333"}),
        use("u6", SS, {"title": "Old"}, t=NOW - 8 * 86400),
        result("u6", 'Proposed "Old" (task_id: task_dddd4444).', t=NOW - 8 * 86400),
        use("u7", SS, {"title": "Resolved"}),
        result("u7", 'Proposed "Resolved" (task_id: task_eeee5555).'),
        use("u8", SS, {"initiation": "user_asked"}),
        result("u8", 'Started session "x" (session_id: local_1).'),
    ]


# ---------------------------------------------------------------- the desktop store

def test_sessions_read_records_and_the_archive_index(tmp_path):
    store(tmp_path, rec("local_a", prs=[{"url": "u", "state": "merged", "branch": "b"}], remote=True),
          rec("local_b"), archived_idx=["local_b"])
    s = {x["id"]: x for x in desktop.sessions()}
    assert s["local_a"]["prs"] == [{"url": "u", "state": "MERGED", "branch": "b"}] and s["local_a"]["remote"]
    assert not s["local_a"]["archived"] and s["local_b"]["archived"]
    assert s["local_a"]["last"] == NOW - 3600


def test_scan_finds_only_cards_nobody_started_dismissed_or_let_expire(tmp_path):
    p = transcript(tmp_path, "cli1", card_rows(tmp_path))
    got = desktop.scan(p, now=NOW)
    assert sorted(c["id"] for c in got["cards"]) == ["task_aaaa1111", "task_eeee5555"]
    assert got["waiting"] == ""


def test_scan_sees_an_unanswered_question_only_when_it_is_the_last_call(tmp_path):
    rows = [use("q1", "AskUserQuestion", {}), result("q1", "answered"), use("q2", "AskUserQuestion", {})]
    assert desktop.scan(transcript(tmp_path, "c", rows), now=NOW)["waiting"] == "AskUserQuestion"
    rows.append(result("q2", "yes"))
    assert desktop.scan(transcript(tmp_path, "c", rows), now=NOW)["waiting"] == ""


def test_transcript_found_under_the_claude_config_dir(tmp_path):
    p = transcript(tmp_path, "abc", [])
    assert desktop.transcript("abc") == p and desktop.transcript("") is None


def test_processes_read_only_the_host_session_variable():
    class P:
        def __init__(self, pid, name, env, rss=2**30, kids=()):
            self.pid, self.info, self._env, self._rss, self._kids = pid, {"name": name}, env, rss, list(kids)

        def environ(self):
            return self._env

        def children(self, recursive=False):
            return self._kids

        def oneshot(self):
            import contextlib
            return contextlib.nullcontext()

        def memory_info(self):
            return type("M", (), {"rss": self._rss})()

        def cpu_times(self):
            return type("T", (), {"user": 1.0, "system": 0.5})()

    kid = P(9, "python.exe", {}, rss=2**29)
    procs = [P(1, "claude.exe", {desktop.HOST_ENV: "local_a", "CLAUDE_CODE_OAUTH_TOKEN": "secret"}, kids=[kid]),
             P(2, "claude.exe", {}), P(3, "node.exe", {desktop.HOST_ENV: "local_z"})]
    fake = type("PS", (), {"process_iter": staticmethod(lambda attrs: procs), "Error": Exception})
    out = desktop.processes(0, psutil_mod=fake)
    assert set(out) == {"local_a"} and out["local_a"]["rss"] == 2**30 + 2**29 and out["local_a"]["pids"] == [1, 9]
    assert "secret" not in json.dumps(out) and out["local_a"]["cpu_pct"] is None


# ---------------------------------------------------------------- the friction audit

def test_desktop_rows_cards_waiting_and_mode(tmp_path):
    store(tmp_path, rec("local_a", title="A", cli="cli1", resolved={"task_eeee5555": "started"}),
          rec("local_b", title="B", mode="auto", awaiting={"why": "x"}),
          rec("local_me", title="Me", mode="default"), rec("local_old", archived=True, mode="default"))
    transcript(tmp_path, "cli1", card_rows(tmp_path))
    rows = friction.desktop_rows(desktop.sessions(), me="local_me",
                                 scan=lambda p: desktop.scan(p, now=NOW))
    kinds = sorted((r["kind"], r["key"].split(":")[1]) for r in rows)
    assert kinds == [("card", "task_aaaa1111"), ("mode", "local_b"), ("waiting", "local_b")]
    card = next(r for r in rows if r["kind"] == "card")
    assert "dismiss_task task_aaaa1111" in card["fix"] and "fieldkit task start" in card["fix"]


def test_remote_control_pending_actions(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    (d / "remote-control-state.json").write_text(json.dumps(
        {"identities": {"x": {"pendingDelete": ["local_q"], "pendingUnarchive": []}}}), encoding="utf-8")
    rows = friction.remote_rows([d])
    assert [r["key"] for r in rows] == ["remote:pendingDelete:local_q"]


def test_link_rows_only_when_a_peer_exists_and_claude_may_not_start(monkeypatch):
    from fieldkit.peers import link
    monkeypatch.setattr(link, "others", lambda h: h.get("devices", []))
    peer = lambda: {"devices": [{"name": "vaio"}]}             # noqa: E731
    assert friction.link_rows(lambda: ({}, "no approved link.yaml"), peer)[0]["kind"] == "link-start"
    assert friction.link_rows(lambda: ({"allow_start": ["claude"], "cwd": "/x"}, "approved"), peer) == []
    assert friction.link_rows(lambda: ({}, "no"), lambda: {"devices": []}) == []


def test_prereq_rows_proposed_but_not_sealed_on_this_os():
    proposed = [{"id": "a", "os": "windows", "admin": True}, {"id": "b", "os": "windows"},
                {"id": "c", "os": "linux", "admin": True}, {"id": "d"}]
    rows = friction.prereq_rows(proposed, {"b"}, platform="windows")
    assert [r["key"] for r in rows] == ["prereq:a", "prereq:d"]
    assert "UAC" in rows[0]["line"] and "owner yes" in rows[1]["line"]


def test_sealed_prereqs_verifies_once_per_file_version(tmp_path):
    m = tmp_path / "m.yaml"
    m.write_text("x", encoding="utf-8")
    calls = []

    class M:
        @staticmethod
        def mandates():
            return [m]

        @staticmethod
        def verify(p):
            calls.append(p)
            return (True, "")

        @staticmethod
        def load(p):
            return {"machine_prerequisites": [{"id": "a"}]}

    assert friction.sealed_prereqs(M) == {"a"} and friction.sealed_prereqs(M) == {"a"} and len(calls) == 1
    m.write_text("changed", encoding="utf-8")
    assert friction.sealed_prereqs(M) == {"a"} and len(calls) == 2


def test_needs_you_rows_in_the_window_only():
    at = lambda h: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - h * 3600))   # noqa: E731
    items = [{"n": 1, "from_host": "vaio", "at": at(1), "text": "[FROM LINUX #3] [NEEDS YOU] approve O4"},
             {"n": 2, "from_host": "vaio", "at": at(60), "text": "[NEEDS YOU] old"},
             {"n": 3, "from_host": "vaio", "at": at(1), "text": "plain note"}]
    rows = friction.needs_you_rows(items, now=NOW)
    assert [r["key"] for r in rows] == ["needs-you:vaio:1"] and "approve O4" in rows[0]["line"]


def test_inbox_rows_and_ack_filter(monkeypatch):
    inbox_rows = friction.inbox_rows
    rows = inbox_rows([{"n": 4, "title": "decide"}])
    assert rows[0]["key"] == "inbox:4"
    for name in ("desktop_rows", "remote_rows", "link_rows", "prereq_rows", "needs_you_rows"):
        monkeypatch.setattr(friction, name, lambda *a, **k: [])
    monkeypatch.setattr(friction, "inbox_rows", lambda *a, **k: rows + inbox_rows([{"n": 5, "title": "b"}]))
    monkeypatch.setattr(desktop, "sessions", lambda dirs=None: [])
    assert [r["key"] for r in friction.local_rows()] == ["inbox:4", "inbox:5"]
    friction.ack(["inbox:4"])
    assert [r["key"] for r in friction.local_rows()] == ["inbox:5"]


def test_peer_rows_answer_old_and_down(monkeypatch):
    from fieldkit.peers import link
    devs = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
    monkeypatch.setattr(link, "others", lambda h: devs)
    answers = {"a": {"ok": True, "rows": [{"kind": "inbox", "key": "inbox:1", "line": "l", "fix": "f"}]},
               "b": {"ok": False, "error": "unknown verb 'friction'"}, "c": {"ok": False, "error": "timed out"}}
    out = dict(friction.peer_rows(lambda req, timeout, device: answers[device], lambda: {"devices": devs}))
    assert out["a"][0]["key"] == "inbox:1" and out["b"][0]["key"] == "peer:b:old" and out["c"][0]["key"] == "peer:c:down"
    lines = friction.lines(list(out.items()))
    assert lines[-1] == "VERDICT  3 thing(s) can wait on a person"
    assert friction.lines([("x", [])])[-1] == "VERDICT  zero friction on 1 computer(s)"


def test_link_answers_the_friction_verb(monkeypatch):
    from fieldkit.peers import link
    monkeypatch.setattr(friction, "local_rows", lambda: [{"kind": "inbox"}])
    ok = type("V", (), {"check": lambda self, req: None})()
    assert link.handle({"verb": "friction"}, ok, via="lan") == {"ok": True, "rows": [{"kind": "inbox"}]}


def test_cli_friction_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(friction, "audit", lambda peers=True: [("here", [])])
    assert lc.main(["friction", "--here"]) == 0
    monkeypatch.setattr(friction, "audit", lambda peers=True: [("here", [{"kind": "k", "key": "k", "line": "l",
                                                                          "fix": "f"}])])
    capsys.readouterr()
    assert lc.main(["friction", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["rows"][0]["key"] == "k"
    assert lc.main(["friction", "ack", "k"]) == 0 and "k" in friction.acked()


# ---------------------------------------------------------------- the sweep

def sessions_for_sweep():
    merged = [{"url": "https://g/pull/1", "state": "MERGED", "branch": "b"}]
    open_ = [{"url": "https://g/pull/2", "state": "OPEN", "branch": "c"}]
    base = [rec("local_fin", title="fin", prs=merged, remote=True), rec("local_nopr", title="nopr"),
            rec("local_idle", title="idle", prs=open_), rec("local_act", title="act", last=NOW - 60),
            rec("local_par", title="parent"), rec("local_kid", title="kid", parent="local_par", last=NOW - 60),
            rec("local_busy", title="busy"), rec("local_zero", title="zero", cli="abcdef1234"),
            rec("local_ex", title="ex", exempt=True), rec("local_me", title="me"), rec("local_arch", archived=True)]
    return base


def test_sweep_classifies_every_live_session(tmp_path):
    store(tmp_path, *sessions_for_sweep())
    procs = {"local_fin": {"rss": 3 * 2**30, "cpu_pct": 0.0, "pids": [1]}, "local_busy": {"rss": 1, "cpu_pct": 40.0},
             "local_idle": {"rss": 2**30, "cpu_pct": 0.0}}
    rows = sweep.classify(desktop.sessions(), procs, now=NOW, cfg=dict(sweep.DEFAULTS), me="local_me",
                          zero={"abcdef12"})
    st = {r["id"]: r for r in rows}
    assert "local_me" not in st and "local_arch" not in st
    assert st["local_fin"]["state"] == "finished" and "merged https://g/pull/1" in st["local_fin"]["why"]
    assert st["local_fin"]["fix"] == "set_remote_control off, then archive_session local_fin"
    assert st["local_nopr"]["state"] == "finished" and st["local_nopr"]["fix"] == "archive_session local_nopr"
    assert st["local_idle"]["state"] == "idle" and "open PR" in st["local_idle"]["why"]
    assert st["local_act"]["state"] == "active"
    assert st["local_par"]["state"] == "held" and "still live" in st["local_par"]["why"]
    assert st["local_busy"]["state"] == "held" and "40%" in st["local_busy"]["why"]
    assert st["local_zero"]["state"] == "held" and "command post" in st["local_zero"]["why"]
    assert st["local_ex"]["state"] == "held"
    assert rows[0]["id"] == "local_fin"                          # finished first, biggest first
    t = sweep.totals(rows)
    assert t == {"finished": 2, "idle": 1, "rss": 4 * 2**30, "rss_finished": 3 * 2**30}
    assert sweep.summary(rows).startswith("lifecycle: idle agent sessions holding 4.0 GB (2 finished")
    kept = sweep.classify(desktop.sessions(), procs, now=NOW, cfg=dict(sweep.DEFAULTS), me="local_me",
                          keep=("local_fin",), zero=set())
    assert next(r for r in kept if r["id"] == "local_fin")["state"] == "held"


def test_sweep_idle_line_is_the_threshold(tmp_path):
    cfg = dict(sweep.DEFAULTS, idle_minutes=30)
    store(tmp_path, rec("local_29", last=NOW - 29 * 60), rec("local_30", last=NOW - 30 * 60))
    st = {r["id"]: r["state"] for r in sweep.classify(desktop.sessions(), {}, now=NOW, cfg=cfg, zero=set())}
    assert st == {"local_29": "active", "local_30": "finished"}


def test_command_post_from_net_yaml(tmp_path):
    p = tmp_path / "NET.yaml"
    p.write_text("net: open\nzero: {station: 'claude:f7386dec@HOST', host_session: local_z, session: f7386dec-1}\n"
                 "2ic: {station: 'claude:11112222@HOST'}\n", encoding="utf-8")
    assert sweep.command_post(p) == {"local_z", "f7386dec-1", "f7386dec", "11112222"}
    p.write_text("net: closed\nzero: {host_session: local_z}\n", encoding="utf-8")
    assert sweep.command_post(p) == set() and sweep.command_post(tmp_path / "none.yaml") == set()


def test_hog_row(tmp_path):
    rows = [{"state": "finished", "rss": 2 * 2**30}, {"state": "idle", "rss": 0}]
    f = sweep.hog_findings(dict(sweep.DEFAULTS), rows)
    assert f[0]["check"] == "sessions" and f[0]["severity"] == "warn" and "holding 2.0 GB" in f[0]["line"]
    assert sweep.hog_findings(dict(sweep.DEFAULTS), [{"state": "idle", "rss": 5 * 2**30}])[0]["severity"] == "ok"
    assert sweep.hog_findings(dict(sweep.DEFAULTS), [{"state": "active", "rss": 9}]) == []


def test_cli_sweep_exit_one_when_something_is_to_archive(monkeypatch, capsys):
    monkeypatch.setattr(sweep, "sweep", lambda **k: [{"id": "local_x", "title": "x", "state": "finished", "why": "w",
                                                      "rss": 2**30, "cpu_pct": 0.0, "fix": "archive_session local_x"}])
    assert lc.main(["sweep"]) == 1 and "archive_session local_x" in capsys.readouterr().out
    monkeypatch.setattr(sweep, "sweep", lambda **k: [])
    assert lc.main(["sweep", "--json"]) == 0


# ---------------------------------------------------------------- headless tasking

def brief(tmp_path, text="Do the job.\nMore."):
    p = tmp_path / "brief.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_task_start_here_writes_a_runbook_and_launches_it_headless(tmp_path):
    seen = []
    rec_ = tasks.start(brief(tmp_path), cwd=tmp_path, wall=45, launch=lambda a: seen.append(a) or {"pid": 7, "log": "L"},
                       now=NOW)
    assert rec_["status"] == "started" and rec_["on"] == "here" and rec_["pid"] == 7
    assert seen == [["runbook", "run", rec_["runbook"]]]
    from fieldkit.autopilot import runbook
    steps = runbook.load(rec_["runbook"])["steps"] if hasattr(runbook, "load") else None
    text = Path(rec_["runbook"]).read_text(encoding="utf-8")
    assert "```runbook" in text and "wall_minutes: 45" in text
    if steps:
        b = steps[0]["agent"]
        assert b.startswith("Do the job.") and f"fieldkit task report {rec_['id']}" in b and "exit: the run ends" in b
        assert "never a desktop" not in b or "Never propose a desktop session card" in b
    assert tasks.ledger()[rec_["id"]]["status"] == "started"
    assert f"task:{rec_['id']}" in (tmp_path / "room" / "ROOM.log").read_text(encoding="utf-8")


def test_task_start_here_refused_is_recorded(tmp_path):
    def boom(a):
        raise RuntimeError("no window")
    r = tasks.start(brief(tmp_path), launch=boom, now=NOW)
    assert r["status"] == "not-started" and "no window" in r["answer"]


def test_task_start_on_a_peer_asks_its_link_listener(tmp_path):
    sent = []
    call = lambda req, timeout, device: sent.append((req, device)) or {"ok": True, "started": True,   # noqa: E731
                                                                        "why": "started claude (PID 5)"}
    r = tasks.start(brief(tmp_path), on="vaio", call=call, now=NOW)
    req, dev = sent[0]
    assert dev == "vaio" and req["verb"] == "ask" and req["agent"] == "claude"
    assert f"fieldkit-task {r['id']} done" in req["brief"] and "fieldkit peers link send" in req["brief"]
    assert r["status"] == "started" and "PID 5" in r["answer"]
    r2 = tasks.start(brief(tmp_path, "Other"), on="vaio", now=NOW + 1,
                     call=lambda req, timeout, device: {"ok": True, "started": False, "why": "not started: x"})
    assert r2["status"] == "not-started"


def test_empty_brief_is_refused(tmp_path):
    with pytest.raises(ValueError):
        tasks.start(brief(tmp_path, "  "), launch=lambda a: {})
    assert lc.main(["task", "start", str(tmp_path / "missing.md")]) == 2


def test_report_writes_ledger_room_and_board(tmp_path):
    (tmp_path / "coord").mkdir()
    (tmp_path / "coord" / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    r = tasks.start(brief(tmp_path), launch=lambda a: {"pid": 1}, now=NOW)
    tasks.report(r["id"], "done", branch="feat/x", pr="https://g/pull/9", note="all | green")
    t = tasks.ledger()[r["id"]]
    assert t["status"] == "done" and t["pr"] == "https://g/pull/9" and t["title"] == "Do the job."
    plan = (tmp_path / "coord" / "PLAN.md").read_text(encoding="utf-8")
    assert f"| Task (fieldkit task) {r['id']} | here | done |" in plan and "all / green" in plan
    with pytest.raises(ValueError):
        tasks.report(r["id"], "started")


def test_ingest_reads_cable_reports_once(tmp_path):
    r = tasks.start(brief(tmp_path), on="vaio", now=NOW,
                    call=lambda req, timeout, device: {"ok": True, "started": True, "why": "ok"})
    items = [{"n": 3, "from_host": "vaio", "at": "x",
              "text": f"[FROM LINUX #4] fieldkit-task {r['id']} done branch=feat/o4 hog: RAM fine"},
             {"n": 4, "from_host": "vaio", "at": "x", "text": "fieldkit-task T-unknown done"}]
    got = tasks.ingest(items)
    assert len(got) == 1 and got[0]["branch"] == "feat/o4" and got[0]["note"] == "hog: RAM fine"
    assert tasks.ingest(items) == []
    assert tasks.ledger()[r["id"]]["status"] == "done"
    assert "done" in tasks.lines(tasks.ledger())[0]


# ---------------------------------------------------------------- the protocol and the rule

def test_done_protocol_has_every_step():
    d = protocol.done_protocol("T-1")
    for must in ("fieldkit ship push", "fieldkit task report T-1", "fieldkit size recycle", "by PID",
                 "archive_session \"self\"", "set_remote_control"):
        assert must in d
    assert "exit: the run ends" in protocol.done_protocol(headless=True)
    tail = protocol.brief_tail()
    assert "fieldkit task start" in tail and "Never propose a desktop session card" in tail


def test_install_rule_add_update_same_and_line_endings(tmp_path):
    p = tmp_path / "CLAUDE.md"
    p.write_bytes(b"# mine\r\nkeep\r\n")
    assert protocol.install_rule(p) == "added" and protocol.rule_present(p)
    raw = p.read_bytes()
    assert raw.startswith(b"# mine\r\nkeep\r\n") and b"\n" not in raw.replace(b"\r\n", b"")
    assert protocol.install_rule(p) == "same"
    p.write_bytes(raw.replace(b"Idle sessions eat", b"Old words"))
    assert not protocol.rule_present(p)
    assert protocol.install_rule(p) == "updated" and protocol.rule_present(p)
    assert p.read_bytes().count(protocol.RULE_BEGIN.encode()) == 1
    q = tmp_path / "new" / "AGENTS.md"
    assert protocol.install_rule(q) == "added" and q.read_text(encoding="utf-8").startswith(protocol.RULE_BEGIN)


def test_rule_files_follow_the_agents_own_dirs(tmp_path):
    f = protocol.rule_files(home=tmp_path, env={"CODEX_HOME": str(tmp_path / "cx")})
    assert f["claude"] == tmp_path / ".claude" / "CLAUDE.md" and f["codex"] == tmp_path / "cx" / "AGENTS.md"
    assert f["antigravity"] == tmp_path / ".gemini" / "GEMINI.md"


def test_repo_agents_md_carries_the_rule():
    assert protocol.rule_present(Path(__file__).resolve().parents[1] / "AGENTS.md")


def test_cli_rule_install_and_protocol(tmp_path, capsys):
    p = tmp_path / "x.md"
    assert lc.main(["rule", "--install", str(p)]) == 0 and protocol.rule_present(p)
    assert lc.main(["protocol", "--task", "T-9"]) == 0 and "fieldkit task report T-9" in capsys.readouterr().out


def test_fieldkit_cli_dispatches(monkeypatch):
    from fieldkit import cli
    seen = []
    monkeypatch.setattr(lc, "main", lambda argv: seen.append(argv) or 0)
    cli.main(["friction", "--here"])
    cli.main(["task", "status"])
    cli.main(["sessions", "sweep"])
    assert seen == [["friction", "--here"], ["task", "status"], ["sweep"]]
