"""Machine tags (fieldkit/peers/machine.py): [MODEL-OS] made once from fixed hardware facts, stored in the household
registry, carried by task start, rotate, the net, friction and doctor; the session-title check on fixed session lists.
Every registry is a fixture in tmp_path (FIELDKIT_LINK_HOME); the link is a fake."""
import io
import json
import types

import pytest

from fieldkit.peers import link, machine

NOW = 1_800_000_000.0
VAIO = {"vendor": "Sony Corporation", "product": "SVE14A3AJ", "version": "C60BV4WG", "family": "VAIO", "model": ""}
L15 = {"vendor": "LENOVO", "product": "20U7S0KJ00", "version": "ThinkPad L15 Gen 1", "family": "ThinkPad L15 Gen 1",
       "model": ""}
AIR = {"vendor": "", "product": "", "version": "", "family": "", "model": "MacBookAir4,1"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A legacy household: link.json's one paired peer, no household.json yet (the VAIO on 2026-10-27)."""
    h = tmp_path / "link"
    h.mkdir()
    (h / "link.json").write_text(json.dumps({"me": "10.77.0.2", "peer": "10.77.0.1", "port": 47077,
                                             "host": "vaio-host", "peer_host": "L15HOST"}), encoding="utf-8")
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(h))
    monkeypatch.setattr(machine, "_CURRENT", None)
    return h


def sess(sid, title, archived=False):
    return {"id": sid, "title": title, "archived": archived}


# ---------------------------------------------------------------- the tag from fixed facts

@pytest.mark.parametrize("facts,system,host,want", [
    (VAIO, "Linux", "gorilla-sve14a3aj", "[VAIO-LNX]"),
    (L15, "Windows", "L15HOST", "[L15-WIN]"),
    (AIR, "Darwin", "air", "[AIR-MAC]"),
    (dict(L15, version="", family=""), "Linux", "think-pad.lan", "[THINKPADLA-LNX]"),   # no rule: the host name
    ({}, "Plan9", "", "[HOST-PLA]"),
])
def test_derive_is_fixed_rules_on_fixed_facts(facts, system, host, want):
    assert machine.derive(facts, system, host) == want
    assert machine.valid(want)


def test_tag_shape():
    assert machine.valid("[L15-WIN]") and not machine.valid("[l15-win]") and not machine.valid("L15-WIN")
    assert not machine.valid("") and not machine.valid(None)


def test_facts_read_the_hardware_read_only(tmp_path):
    dmi = tmp_path / "dmi"
    dmi.mkdir()
    (dmi / "sys_vendor").write_text("Sony Corporation\n")
    (dmi / "product_name").write_text("SVE14A3AJ\n")
    f = machine.facts("Linux", dmi=dmi)
    assert f["vendor"] == "Sony Corporation" and f["product"] == "SVE14A3AJ" and f["version"] == ""

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    vals = {"SystemManufacturer": "LENOVO", "SystemVersion": "ThinkPad L15 Gen 1"}

    def query(key, name):
        if name not in vals:
            raise OSError(name)
        return vals[name], 1
    wr = types.SimpleNamespace(HKEY_LOCAL_MACHINE=0, OpenKey=lambda *a: Key(), QueryValueEx=query)
    f = machine.facts("Windows", winreg_mod=wr)
    assert f["vendor"] == "LENOVO" and f["version"] == "ThinkPad L15 Gen 1" and f["product"] == ""
    run = lambda argv, **k: types.SimpleNamespace(stdout="MacBookAir4,1\n")   # noqa: E731
    assert machine.facts("Darwin", run=run)["model"] == "MacBookAir4,1"
    assert machine.facts("Darwin", run=lambda *a, **k: 1 / 0)["model"] == ""   # no facts: never an exception


# ---------------------------------------------------------------- one registry, made once, never retyped

def test_first_name_is_derived_and_stored_then_read_from_the_registry(home):
    calls = []
    fx = lambda system: calls.append(system) or VAIO   # noqa: E731
    r = machine.name(facts_fn=fx, system="Linux", hostname="vaio-host")
    assert r["tag"] == "[VAIO-LNX]" and r["source"] == "derived" and r["stored"]
    saved = json.loads((home / "household.json").read_text(encoding="utf-8"))
    me = next(d for d in saved["devices"] if d["name"] == saved["me"])
    assert me["tag"] == "[VAIO-LNX]" and me["host"] == "vaio-host" and "SVE14A3AJ" in me["model"]
    assert "legacy" not in saved and all("legacy_peer" not in d for d in saved["devices"])
    r2 = machine.name(facts_fn=lambda system: L15, system="Windows", hostname="other")
    assert r2 == dict(r2, tag="[VAIO-LNX]", source="registry") and calls == ["Linux"]   # never made again


def test_read_only_name_and_no_household_write_nothing(home, tmp_path, monkeypatch):
    assert machine.name(save=False, facts_fn=lambda system: VAIO, system="Linux")["stored"] is False
    assert not (home / "household.json").exists()
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "empty"))
    r = machine.name(facts_fn=lambda system: VAIO, system="Linux", hostname="h")
    assert r["tag"] == "[VAIO-LNX]" and not r["stored"] and "pair" in r["note"]
    assert not (tmp_path / "empty").exists()                  # refusing over inventing state


def test_current_never_writes_and_never_raises(home, monkeypatch):
    monkeypatch.setattr(machine, "facts", lambda system=None: VAIO)
    assert machine.current().startswith("[VAIO-") and machine.valid(machine.current())
    assert not (home / "household.json").exists()
    monkeypatch.setattr(machine, "_CURRENT", None)
    monkeypatch.setattr(machine, "name", lambda **k: 1 / 0)
    assert machine.current() == ""


def test_devices_add_keeps_the_learned_tag(home):
    machine.name(facts_fn=lambda system: VAIO, system="Linux", hostname="vaio-host")
    h = link.household()
    machine.learn(h, "L15HOST", {"ok": True, "tag": "[L15-WIN]", "host": "L15HOST", "os": "Windows 11"}, now=NOW)
    link.save_household(h)
    link.device_add("L15HOST", "10.77.0.1", role="boss", os_name="Windows", via="cable", allow_loopback=True)
    d = link.device(link.household(), "L15HOST")
    assert d["tag"] == "[L15-WIN]" and d["last_seen"] == NOW and d["role"] == "boss"


def test_refresh_learns_each_peer_from_its_hello(home):
    h = link.household()
    seen = []

    def call(req, timeout, device):
        seen.append((req["verb"], device))
        return {"ok": True, "host": "L15HOST", "os": "Windows 10", "tag": "[L15-WIN]"}
    assert machine.refresh(h, call=call, now=NOW) == {"L15HOST": "ok"} and seen == [("hello", "L15HOST")]
    d = link.device(link.household(), "L15HOST")
    assert d["tag"] == "[L15-WIN]" and d["os"] == "Windows" and d["last_seen"] == NOW
    assert machine.tag_of("L15HOST") == "[L15-WIN]" and machine.tag_of("nobody") == ""


def test_refresh_down_peer_writes_nothing_and_a_bad_tag_is_not_stored(home):
    assert machine.refresh(call=lambda *a, **k: {"ok": False, "error": "timed out"}) == {"L15HOST": "timed out"}
    assert not (home / "household.json").exists()
    h = link.household()
    machine.learn(h, "L15HOST", {"ok": True, "tag": "L15 WIN"}, now=NOW)
    assert "tag" not in link.device(h, "L15HOST")


def test_list_rows_and_lines(home):
    machine.name(facts_fn=lambda system: VAIO, system="Linux", hostname="vaio-host")
    rows = machine.devices(now=NOW)
    assert [(r["name"], r["tag"], r["me"]) for r in rows] == [("vaio-host", "[VAIO-LNX]", True), ("L15HOST", "", False)]
    assert rows[0]["address"] == ["10.77.0.2"] and rows[0]["last_seen"] == NOW and rows[1]["last_seen"] is None
    text = "\n".join(machine.device_lines(rows, {"L15HOST": "timed out"}))
    assert "[VAIO-LNX]" in text and "now (this machine)" in text and "never  (now: timed out)" in text


def test_machine_command_name_and_list_json(home, monkeypatch, capsys):
    monkeypatch.setattr(machine, "facts", lambda system=None: VAIO)
    monkeypatch.setattr(link, "call", lambda req, timeout=15, device=None: {"ok": True, "tag": "[L15-WIN]",
                                                                           "host": "L15HOST", "os": "Windows 11"})
    assert machine.main(["list", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [d["tag"] for d in out["devices"]] == [machine.name()["tag"], "[L15-WIN]"]
    assert out["reached"] == {"L15HOST": "ok"}
    assert machine.main(["name"]) == 0 and capsys.readouterr().out.strip() == machine.name()["tag"]


# ---------------------------------------------------------------- the session-title check

def test_tagged_and_prefix():
    assert machine.tagged("  [VAIO-LNX] job", "[VAIO-LNX]") and not machine.tagged("job [VAIO-LNX]", "[VAIO-LNX]")
    assert not machine.tagged("[L15-WIN] job", "[VAIO-LNX]") and not machine.tagged("x", "")
    assert machine.prefix("job  one", "[VAIO-LNX]") == "[VAIO-LNX] job one"
    assert machine.prefix("[L15-WIN] job", "[VAIO-LNX]") == "[L15-WIN] job"      # its own tag is kept
    assert machine.prefix("job", "") == "job"


def test_check_lists_untagged_here_and_on_the_peer(home):
    here = [sess("a", "[TEST-LNX] fine"), sess("b", "no tag"), sess("c", "", archived=True), sess("d", "")]
    peer = {"ok": True, "tag": "[L15-WIN]", "sessions": 2,
            "rows": [{"where": "L15HOST", "tag": "[L15-WIN]", "id": "p1", "title": "Command post"}]}
    r = machine.check(sessions=here, tag="[TEST-LNX]", call=lambda req, timeout, device: peer, host="vaio")
    assert [(x["where"], x["id"]) for x in r["rows"]] == [("vaio", "b"), ("vaio", "d"), ("L15HOST", "p1")]
    assert [m["state"] for m in r["machines"]] == ["checked", "checked"] and r["machines"][0]["sessions"] == 3
    assert machine.check_code(r) == 1
    text = "\n".join(machine.check_lines(r))
    assert 'rename it "[L15-WIN] Command post"' in text and "VERDICT  3 session(s)" in text


def test_check_passes_only_when_every_machine_was_checked(home):
    ok = lambda req, timeout, device: {"ok": True, "tag": "[L15-WIN]", "sessions": 1, "rows": []}   # noqa: E731
    r = machine.check(sessions=[sess("a", "[TEST-LNX] x")], tag="[TEST-LNX]", call=ok)
    assert machine.check_code(r) == 0 and "every live session carries" in machine.check_lines(r)[-1]
    old = lambda req, timeout, device: {"ok": False, "error": "unknown verb 'tags'"}   # noqa: E731
    r = machine.check(sessions=[sess("a", "[TEST-LNX] x")], tag="[TEST-LNX]", call=old)
    assert r["machines"][1]["state"] == "not checked" and "sync --apply" in r["machines"][1]["why"]
    assert machine.check_code(r) == 1 and "NOT CHECKED" in machine.check_lines(r)[-1]
    r = machine.check(sessions=[sess("a", "x")], tag="[TEST-LNX]", call=old, peers=False)
    assert len(r["machines"]) == 1 and machine.check_code(r) == 1


def test_sessions_tag_command_exit_codes(home, monkeypatch, capsys):
    import fieldkit.lifecycle as lc
    monkeypatch.setattr(machine, "check", lambda peers=True: {"machines": [
        {"where": "h", "tag": "[T-LNX]", "sessions": 1, "state": "checked", "why": ""}], "rows": []})
    assert lc.main(["tag", "--check", "--here"]) == 0
    monkeypatch.setattr(machine, "check", lambda peers=True: {"machines": [
        {"where": "h", "tag": "[T-LNX]", "sessions": 1, "state": "checked", "why": ""}],
        "rows": [{"where": "h", "tag": "[T-LNX]", "id": "s1", "title": "job"}]})
    capsys.readouterr()
    assert lc.main(["tag", "--check", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["rows"][0]["id"] == "s1"


def test_link_answers_tags_and_hello_carries_the_tag(home, monkeypatch):
    monkeypatch.setattr(machine, "_CURRENT", "[VAIO-LNX]")
    monkeypatch.setattr(machine, "local_check", lambda: {"tag": "[VAIO-LNX]", "host": "h", "sessions": 0, "rows": []})
    ok = types.SimpleNamespace(check=lambda req: None)
    r = link.handle({"verb": "tags"}, ok)
    assert r["ok"] and r["tag"] == "[VAIO-LNX]" and r["rows"] == []
    r = link.handle({"verb": "hello"}, ok, agents_fn=lambda: [], toolset_fn=lambda: None)
    assert r["ok"] and r["tag"] == "[VAIO-LNX]"


# ---------------------------------------------------------------- wired in: friction, doctor, hook, net, tasks

def test_friction_flags_an_untagged_session():
    from fieldkit.lifecycle import friction
    rows = friction.untagged_rows([sess("s1", "Command post"), sess("s2", "[TEST-LNX] ok")])
    assert [(r["kind"], r["key"]) for r in rows] == [("untagged", "untagged:s1")]
    assert '"[TEST-LNX] Command post"' in rows[0]["fix"]


def test_friction_local_rows_include_untagged(tmp_path, monkeypatch):
    from fieldkit.lifecycle import friction
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    for n in ("desktop_rows", "remote_rows", "link_rows", "prereq_rows", "needs_you_rows", "inbox_rows"):
        monkeypatch.setattr(friction, n, lambda *a, **k: [])
    monkeypatch.setattr(friction.desktop, "sessions", lambda dirs=None: [sess("s1", "job")])
    assert [r["kind"] for r in friction.local_rows()] == ["untagged"]


def test_doctor_carries_the_session_tag_row():
    from fieldkit import cli
    base = {"ok": True, "rows": [], "next": "fieldkit next PIPELINE"}
    good = lambda peers: {"machines": [{"tag": "[T-LNX]"}], "rows": []}   # noqa: E731
    bad = lambda peers: {"machines": [{"tag": "[T-LNX]"}], "rows": [{"title": "job", "id": "s1"}]}   # noqa: E731
    r = cli._with_session_tags(base, good)
    assert r["ok"] and r["rows"][-1]["name"] == "machine-tag" and r["rows"][-1]["ok"]
    r = cli._with_session_tags(base, bad)
    assert not r["ok"] and "1 live session(s)" in r["rows"][-1]["detail"] and "sessions tag" in r["next"]
    r = cli._with_session_tags(base, lambda peers: 1 / 0)
    assert not r["ok"] and "not checked" in r["rows"][-1]["detail"]


def test_hook_line_at_session_start_and_once_at_the_first_prompt():
    call = {"session_id": "abc-1"}
    assert machine.hook_line(call, "SessionStart").startswith("MACHINE: this machine is [TEST-LNX]: title yourself "
                                                              "[TEST-LNX] <job>")
    assert machine.hook_line(call, "UserPromptSubmit") == ""          # already said at SessionStart
    other = {"session_id": "abc-2"}
    assert "[TEST-LNX]" in machine.hook_line(other, "UserPromptSubmit")
    assert machine.hook_line(other, "UserPromptSubmit") == "" and machine.hook_line(other, "PostToolUse") == ""


def test_standalone_hook_prints_additional_context(capsys):
    machine.hook(io.StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "s9"})))
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "[TEST-LNX]" in out["hookSpecificOutput"]["additionalContext"]


def test_net_lines_carry_the_station_machine_tag(monkeypatch):
    from fieldkit.zero import net
    monkeypatch.setattr(machine, "tag_of", lambda host, h=None: {"vaio": "[VAIO-LNX]"}.get(host, ""))
    assert net.tagged_station("claude:ab12cd34@vaio") == "[VAIO-LNX] claude:ab12cd34@vaio"
    assert net.tagged_station("claude:ab12cd34@unknown") == "claude:ab12cd34@unknown"
    lines = net.roll_lines({"rollcall": {"id": "R-1", "at": "x", "deadline": None},
                            "rows": [{"status": "ANSWERED", "station": "claude:ab12cd34@vaio", "sitrep": None}]})
    assert "ANSWERED     [VAIO-LNX] claude:ab12cd34@vaio" in lines[1]
    row = net.board_row({"net": "open", "zero": {"station": "claude:ab12cd34@vaio"}, "epoch": 3}, [], NOW)
    assert "Zero [VAIO-LNX] claude:ab12cd34@vaio" in row


def test_task_start_titles_with_the_tag_of_where_it_runs(tmp_path, monkeypatch):
    from fieldkit.lifecycle import tasks
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.setattr(machine, "tag_of", lambda host, h=None: {"L15HOST": "[L15-WIN]"}.get(host, ""))
    (tmp_path / "coord").mkdir()
    (tmp_path / "coord" / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    b = tmp_path / "brief.md"
    b.write_text("Fix the thing.\nmore", encoding="utf-8")
    sent = []
    r = tasks.start(b, on="L15HOST", call=lambda req, timeout, device: sent.append(req) or {"ok": True, "started": 1},
                    now=NOW)
    assert r["title"] == "[L15-WIN] Fix the thing." and r["tag"] == "[L15-WIN]"
    assert "SESSION TITLE: [L15-WIN] Fix the thing." in sent[0]["brief"]
    here = tasks.start(b, launch=lambda a: {"pid": 1, "log": "L"}, now=NOW + 1, cwd=tmp_path)
    assert here["title"] == "[TEST-LNX] Fix the thing."
    tasks.report(r["id"], "done", branch="feat/x")
    assert "| [L15-WIN] Fix the thing. |" in (tmp_path / "coord" / "PLAN.md").read_text(encoding="utf-8")
