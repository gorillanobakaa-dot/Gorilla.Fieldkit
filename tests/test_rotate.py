"""Session rotation (fieldkit/tokens/rotate.py): fixture transcripts written here, every number worked out by hand.

Prices (prices.json, anthropic): Opus 5.5 input $4, cache read $0.20, output $20 per million; Haiku 5.5 input $0.10.
A call with 400k cache read + 1k output on Opus 5.5 = 400_000 x 0.20 / 1e6 + 1_000 x 20 / 1e6 = $0.08 + $0.02 = $0.10.
"""
import io
import json
import os
import re
import time
from pathlib import Path

import pytest

from fieldkit.autopilot import budget, checkpoint, hardlines, inbox, job, room
from fieldkit.tokens import rotate, session_cost as sc

CFG = dict(rotate.DEFAULTS)


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.delenv("FIELDKIT_ROTATE", raising=False)
    monkeypatch.setattr(rotate, "config", lambda: dict(CFG))


def line(kind="assistant", mid=None, read=0, inp=0, out=0, model="claude-opus-5-5", stop="end_turn", ts=None,
         **extra):
    d = {"type": kind, "timestamp": ts or "2026-10-26T10:00:00Z", "cwd": "C:/work/repo", "gitBranch": "feat/x",
         "entrypoint": "claude-desktop", **extra}
    if kind == "assistant":
        d["message"] = {"id": mid, "model": model, "stop_reason": stop, "content": [],
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": read, "output_tokens": out}}
    return d


def write(path, rows, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


def monster(tmp_path, read=400_000, n=1, stop="end_turn", ts=None, name="sess-big"):
    rows = [line("user", message={"content": "<system-reminder>noise</system-reminder>\nBuild the rotation tool. "
                                             "See https://github.com/o/r/pull/7"})]
    rows += [line(mid=f"m{i}", read=read, out=1_000, stop=stop, ts=ts) for i in range(n)]
    return write(tmp_path / "projects" / "proj" / f"{name}.jsonl", rows)


# ---------------------------------------------------------------- the verdict

@pytest.mark.parametrize("ctx,usd,level", [
    (249_999, 39.99, "OK"), (250_000, 0, "WARN"), (0, 40.0, "WARN"), (349_999, 74.99, "WARN"),
    (350_000, 0, "ROTATE"), (0, 75.0, "ROTATE"), (350_000, 40.0, "ROTATE")])
def test_verdict_lines(ctx, usd, level):
    got, why = rotate.verdict(ctx, usd, CFG)
    assert got == level
    assert (why == []) == (level == "OK")


def test_verdict_names_both_rules():
    _, why = rotate.verdict(400_000, 80.0, CFG)
    assert why == ["context per call 400k >= ROTATE 350k", "session dollars $80.00 >= ROTATE $75.00"]


# ---------------------------------------------------------------- measuring

def test_measure_prices_calls_and_reads_the_facts(tmp_path):
    p = monster(tmp_path, n=3)
    r = rotate.judged(rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p), CFG)
    assert r["calls"] == 3 and r["usd"] == pytest.approx(0.30)
    assert r["context_last"] == 400_000 and r["level"] == "ROTATE"
    assert (r["cwd"], r["branch"], r["entrypoint"], r["stop_reason"]) == ("C:/work/repo", "feat/x", "claude-desktop",
                                                                          "end_turn")
    assert r["prs"] == ["https://github.com/o/r/pull/7"]
    assert r["task"].startswith("Build the rotation tool") and "noise" not in r["task"]


def test_subagent_calls_add_dollars_but_not_the_sessions_context(tmp_path):
    p = monster(tmp_path, read=100_000)
    write(p.with_suffix("") / "subagents" / "a.jsonl",
          [line(mid="s1", inp=1_000_000, model="claude-haiku-5-5", isSidechain=True)])
    r = rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p)
    assert r["context_last"] == 100_000                           # the main transcript's last call
    assert r["usd"] == pytest.approx(0.02 + 0.02 + 0.50)          # Haiku 1M prompt: over 100k, $0.50 per M


def test_measure_is_incremental_and_starts_over_on_a_rewrite(tmp_path):
    p = monster(tmp_path, read=100_000)
    a = sc.ClaudeCode(tmp_path / "projects")
    assert rotate.measure(a, p)["calls"] == 1
    write(p, [line(mid="m0", read=100_000, out=1_000), line(mid="m9", read=360_000)], mode="a")  # m0 again: same reply
    r = rotate.measure(a, p)
    assert r["calls"] == 2 and r["context_last"] == 360_000
    meter = json.loads((tmp_path / "rot" / "meters" / "sess-big.json").read_text(encoding="utf-8"))
    assert meter["calls"] == 2 and meter["size"] == p.stat().st_size
    write(p, [line(mid="n1", read=1_000)])                         # rewritten, shorter: measured from the start
    r = rotate.measure(a, p)
    assert r["calls"] == 1 and r["context_last"] == 1_000


def test_title_from_the_desktop_custom_title_line(tmp_path):
    p = monster(tmp_path)
    write(p, [{"type": "custom-title", "customTitle": "Old"}, {"type": "custom-title", "customTitle": "Job 1"}],
          mode="a")
    assert rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p)["title"] == "Job 1"


def test_check_lists_recent_sessions_worst_first_and_logs(tmp_path):
    monster(tmp_path, read=10_000, name="small")
    monster(tmp_path, read=400_000, name="big")
    rows = rotate.check(hours=1, adapters=[sc.ClaudeCode(tmp_path / "projects")], cfg=CFG)
    assert [(r["session"], r["level"]) for r in rows] == [("big", "ROTATE"), ("small", "OK")]
    log = (tmp_path / "rot" / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 2 and json.loads(log[0])["by"] == "check"
    old = monster(tmp_path, name="old")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    assert "old" not in [r["session"] for r in rotate.check(hours=1, adapters=[sc.ClaudeCode(tmp_path / "projects")])]


# ---------------------------------------------------------------- the hook

def call(p, event):
    return {"transcript_path": str(p), "session_id": p.stem, "hook_event_name": event}


def test_hook_rotate_at_every_prompt_and_once_mid_turn(tmp_path):
    p = monster(tmp_path)
    first = rotate.hook_line(call(p, "PostToolUse"), "PostToolUse")
    assert first.startswith("SESSION ROTATION") and "rotate handover --transcript" in first
    assert "archive_session" in first                              # a desktop session is told to archive itself
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse") == ""          # said once mid-turn
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit").startswith("SESSION ROTATION")
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit").startswith("SESSION ROTATION")
    log = (tmp_path / "rot" / "verdicts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log) == 1                                           # logged when the level changed, not every time


def test_hook_warn_once_and_quiet_when_ok(tmp_path):
    p = monster(tmp_path, read=300_000)
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit").startswith("session rotation: WARN")
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit") == ""
    q = monster(tmp_path, read=10_000, name="fine")
    assert rotate.hook_line(call(q, "UserPromptSubmit"), "UserPromptSubmit") == ""


def test_hook_post_tool_use_is_throttled(tmp_path):
    p = monster(tmp_path, read=10_000)
    t = time.time()
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse", now=t) == ""
    write(p, [line(mid="x", read=400_000)], mode="a")
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse", now=t + 10) == ""      # inside hook_every_s
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse", now=t + 61).startswith("SESSION ROTATION")


def test_hook_silent_in_a_job_when_off_and_without_a_transcript(tmp_path, monkeypatch):
    p = monster(tmp_path)
    assert rotate.hook_line({}, "UserPromptSubmit") == ""
    monkeypatch.setenv("FIELDKIT_ROTATE", "off")
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit") == ""
    monkeypatch.delenv("FIELDKIT_ROTATE")
    monkeypatch.setenv("FIELDKIT_JOB_DIR", str(tmp_path))
    assert rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit") == ""


def test_room_hook_carries_the_line(tmp_path):
    p = monster(tmp_path)
    assert "SESSION ROTATION" in room._rotate(call(p, "UserPromptSubmit"), "UserPromptSubmit", "ROOM: x")
    assert room._rotate({}, "UserPromptSubmit", "ROOM: x") == "ROOM: x"


def test_standalone_hook_prints_additional_context(tmp_path, capsys):
    p = monster(tmp_path)
    rotate.hook(io.StringIO(json.dumps(call(p, "UserPromptSubmit"))))
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert out["hookSpecificOutput"]["additionalContext"].startswith("SESSION ROTATION")


CARD = re.compile(r"initiation \"(own_initiative|offer)\"|(?<!NEVER )propose a (new )?(session|card)|start a card", re.I)


@pytest.mark.parametrize("entry", ["claude-desktop", "cli"])
@pytest.mark.parametrize("owner", [True, False])
def test_no_rotation_path_falls_back_to_a_card(tmp_path, entry, owner):
    """2026-10-26 20:2x: a command post rotated on an event turn, the app refused start_session and the model put an
    own_initiative card in front of the owner. No rotation text may lead to a card, on any path."""
    row = {"why": ["x"], "context_last": 400_000, "entrypoint": entry, "cwd": "C:/w", "path": "t.jsonl"}
    text = rotate.next_step(row, owner=owner)
    assert not CARD.search(text)
    if entry == "claude-desktop":
        assert "fieldkit task start" in text
        if not owner:
            assert "do NOT call start_session" in text and "start_session, initiation" not in text
        else:
            assert "initiation \"user_asked\"" in text and "NEVER propose a card" in text
        assert "set_remote_control off" in text


def test_hook_knows_an_event_turn_from_the_owners_words(tmp_path):
    p = monster(tmp_path)
    ev = dict(call(p, "UserPromptSubmit"), prompt="<cross-session-message from=\"local_x\">rotate now")
    assert "do NOT call start_session" in rotate.hook_line(ev, "UserPromptSubmit")
    own = dict(call(p, "UserPromptSubmit"), prompt="carry on")
    assert "initiation \"user_asked\"" in rotate.hook_line(own, "UserPromptSubmit")
    # mid-turn, the line crossed during a turn keeps the kind of the prompt that started the turn
    for prompt, want in (("carry on", "user_asked"), ("<task-notification> done", "do NOT call start_session")):
        q = monster(tmp_path, read=300_000, name=f"mid-{len(prompt)}")
        t = time.time()
        rotate.hook_line(dict(call(q, "UserPromptSubmit"), prompt=prompt), "UserPromptSubmit", now=t)
        write(q, [line(mid="y", read=400_000)], mode="a")
        assert want in rotate.hook_line(call(q, "PostToolUse"), "PostToolUse", now=t + 120)
    for tag in ("<task-notification>", "[SYSTEM NOTIFICATION - NOT USER INPUT]", "<side-sessions-event>",
                "<ci-monitor-event>"):
        assert not rotate.owner_turn({"prompt": tag + " x"})
    assert rotate.owner_turn({"prompt": "please rotate"}) and not rotate.owner_turn({})


def test_cli_session_is_told_to_start_claude_not_archive(tmp_path):
    p = write(tmp_path / "projects" / "proj" / "cli.jsonl", [line(mid="a", read=400_000, entrypoint="cli")])
    text = rotate.hook_line(call(p, "UserPromptSubmit"), "UserPromptSubmit")
    assert "fresh `claude` in C:/work/repo" in text and "archive_session" not in text


# ---------------------------------------------------------------- handover

def test_handover_writes_note_and_plan_section(tmp_path):
    p = monster(tmp_path)
    (tmp_path / "coord").mkdir()
    plan = tmp_path / "coord" / "PLAN.md"
    plan.write_text("# PLAN\n", encoding="utf-8")
    r = rotate.handover(p, "Rotation | job", "write tests", done="module", not_verified="CI", now=1_800_000_000)
    assert r["plan_written"]
    text = plan.read_text(encoding="utf-8")
    assert text.startswith("# PLAN\n") and "session rotated: Rotation / job (sess-big)" in text
    assert "| ROTATE: context per call 400k >= ROTATE 350k | module | write tests | CI |" in text
    note = Path(r["note"]).read_text(encoding="utf-8")
    assert Path(r["note"]).name.endswith("-sess-big.md")
    assert "## Next\n\nwrite tests" in note and "feat/x" in note and "https://github.com/o/r/pull/7" in note
    assert "Do NOT start over" in r["brief"] and r["note"] in r["brief"] and "Build the rotation tool" in r["brief"]
    # the relief starts side jobs headless and archives itself when done (fieldkit/lifecycle)
    assert "fieldkit task start" in r["brief"] and "archive_session \"self\"" in r["brief"]
    h = (tmp_path / "rot" / "handovers.jsonl").read_text(encoding="utf-8")
    assert json.loads(h)["plan_written"] is True


def test_handover_never_creates_a_missing_plan(tmp_path):
    p = monster(tmp_path)
    r = rotate.handover(p, "t", "n")
    assert not r["plan_written"] and not (tmp_path / "coord" / "PLAN.md").exists() and Path(r["note"]).is_file()
    assert rotate.main(["handover", "--transcript", str(p), "--title", "t", "--next", "n"]) == 1


# ---------------------------------------------------------------- finished side sessions

def test_finished_needs_end_turn_idle_and_no_open_pr(tmp_path):
    old = "2026-10-26T08:00:00Z"
    now = rotate._ago_h(old, 0) * -3600 + 3 * 3600                # three hours after the last line
    ad = [sc.ClaudeCode(tmp_path / "projects")]
    write(tmp_path / "projects" / "proj" / "done.jsonl", [line(mid="a", read=10, ts=old)])
    write(tmp_path / "projects" / "proj" / "busy.jsonl", [line(mid="b", read=10, ts=old, stop="tool_use")])
    write(tmp_path / "projects" / "proj" / "fresh.jsonl",
          [line(mid="c", read=10, ts="2026-10-26T10:59:00Z")])
    monster(tmp_path, ts=old, name="pr")
    for f in (tmp_path / "projects" / "proj").glob("*.jsonl"):
        os.utime(f, (now - 60, now - 60))
    done, held = rotate.finished(hours=24, cfg=CFG, now=now, adapters=ad)
    assert [r["session"] for r in done] == ["done"]
    why = {r["session"]: " ".join(r["held_by"]) for r in held}
    assert "did not end its turn" in why["busy"] and "active" in why["fresh"] and "not checked" in why["pr"]
    done, held = rotate.finished(hours=24, use_gh=True, cfg=CFG, now=now, adapters=ad, gh=lambda u: "MERGED")
    assert sorted(r["session"] for r in done) == ["done", "pr"]
    done, held = rotate.finished(hours=24, use_gh=True, cfg=CFG, now=now, adapters=ad, gh=lambda u: "OPEN",
                                 exclude=("done",))
    assert done == [] and "open PR" in " ".join(next(r for r in held if r["session"] == "pr")["held_by"])


def test_pr_state_from_gh():
    class R:
        returncode, stdout = 0, "MERGED\n"
    assert rotate.pr_state("u", run=lambda *a, **k: R()) == "MERGED"
    R.returncode = 1
    assert rotate.pr_state("u", run=lambda *a, **k: R()) is None


# ---------------------------------------------------------------- autopilot: the job's session rotates

@pytest.fixture()
def ap(tmp_path, monkeypatch):
    st = tmp_path / "ap"
    st.mkdir()
    monkeypatch.setattr(hardlines, "STATE", st)
    monkeypatch.setattr(hardlines, "STOP_FILE", st / "STOP")
    monkeypatch.setattr(inbox, "INBOX", st / "inbox")
    return st


def test_budget_hook_denies_all_but_the_checkpoint_over_the_rotate_line(tmp_path, ap):
    jd = tmp_path / "job"
    budget.start(jd, budget.merge(None))
    p = monster(tmp_path)
    why = budget.hook_pre(jd, "Bash", {"command": "make"}, transcript=str(p))
    assert why.startswith("SESSION ROTATION") and "HANDOFF.md" in why
    assert budget.hook_pre(jd, "Write", {"file_path": str(jd / "HANDOFF.md")}, transcript=str(p)) is None
    assert budget.hook_pre(jd, "Bash", {"command": "git commit -m wip"}, transcript=str(p)) is None
    m = json.loads((jd / "meter.json").read_text(encoding="utf-8"))
    assert m["exhausted"] == "rotate"
    assert budget.classify({"rc": 0, "result": {"is_error": False, "result": "ok"}}, m)[0] == "rotate"


def test_budget_hook_lets_a_small_session_work(tmp_path, ap):
    jd = tmp_path / "job"
    budget.start(jd, budget.merge(None))
    p = monster(tmp_path, read=10_000)
    assert budget.hook_pre(jd, "Bash", {"command": "make"}, transcript=str(p)) is None


def fake_claude(jd, ends):
    seen = []

    def claude(prompt, cwd=None, max_turns=None, timeout=None, env_extra=None, resume=None, model=None):
        seen.append({"prompt": prompt, "resume": resume})
        end = ends[len(seen) - 1]
        if end == "rotate":
            m = json.loads((jd / "meter.json").read_text(encoding="utf-8"))
            m.update(exhausted="rotate", rotate_why="context per call 400k >= ROTATE 350k")
            (jd / "meter.json").write_text(json.dumps(m), encoding="utf-8")
        return {"rc": 0, "seconds": 1, "result": {"is_error": False, "result": "ended", "session_id": "S1",
                                                  "num_turns": 3, "subtype": "success"}}
    return claude, seen


def test_job_restarts_a_rotated_session_from_the_checkpoint_never_resumes_it(tmp_path, ap):
    jd = tmp_path / "job"
    claude, seen = fake_claude(jd, ["rotate", "success"])
    out = job._run_job(jd, "BRIEF", tmp_path, claude=claude, say=lambda m: None, max_rotations=5)
    assert out["status"] == "done"
    assert seen[1]["resume"] is None and "Do NOT start over" in seen[1]["prompt"]
    cp = checkpoint.load(jd)
    assert [a["end"] for a in cp["attempts"]] == ["rotate", "success"] and cp["rotations"] == 1
    assert "rotate_next" not in cp


def test_job_past_max_rotations_goes_to_the_inbox(tmp_path, ap):
    jd = tmp_path / "job"
    claude, seen = fake_claude(jd, ["rotate", "rotate", "rotate"])
    added = []
    out = job._run_job(jd, "BRIEF", tmp_path, claude=claude, say=lambda m: None, max_rotations=1,
                       inbox_add=lambda *a, **k: added.append(a))
    assert out["rc"] == budget.EXIT_PAUSED and out["why"] == "rotate x2" and len(seen) == 2
    assert "rotated 2 times" in added[0][0]


# ---------------------------------------------------------------- CLI

def test_cli_check_exit_codes_and_log(tmp_path, capsys, monkeypatch):
    p = monster(tmp_path)
    monkeypatch.setattr(sc, "adapters_for", lambda s: [sc.ClaudeCode(tmp_path / "projects")])
    assert rotate.main(["check", "--hours", "1"]) == 2
    assert "1 ROTATE" in capsys.readouterr().out
    assert rotate.main(["check", "--session", "nope"]) == 3
    q = monster(tmp_path, read=300_000, name="w")
    assert rotate.main(["check", "--session", str(q), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)[0]["level"] == "WARN"
    assert rotate.main(["log", "1"]) == 0 and '"WARN"' in capsys.readouterr().out
    assert rotate.main(["doctor"]) in (0, 1)


def test_fieldkit_cli_dispatches(tmp_path, capsys, monkeypatch):
    from fieldkit import cli
    monkeypatch.setattr(sc, "adapters_for", lambda s: [sc.ClaudeCode(tmp_path / "projects")])
    monster(tmp_path)
    assert cli.main(["rotate", "check", "--hours", "1"]) == 2
    assert cli.main(["session-cost", "--doctor"]) == 0
    assert cli.main(["token-audit", "--doctor"]) in (0, 1)


# ---------------------------------------------------------------- the old local cards do not break the registry

def test_a_moved_local_kit_card_is_superseded_not_a_duplicate(tmp_path, monkeypatch):
    from fieldkit.desk import registry
    local = tmp_path / "tools.yaml"
    local.write_text("tools:\n"
                     "- {id: session-cost, title: old, path: '${FIELDKIT}/local/kits/TokenAudit/session_cost.py'}\n"
                     "- {id: my-own, title: mine, path: '${FIELDKIT}/local/kits/Mine/mine.py'}\n", encoding="utf-8")
    monkeypatch.setattr(registry, "LOCAL_REGISTRY", local)
    monkeypatch.setattr(registry, "SUPERSEDED", set())
    tools = registry.load()
    paths = [str(t["path"]).replace("\\", "/") for t in tools if t["id"] == "session-cost"]
    assert len(paths) == 1 and paths[0].endswith("fieldkit/tokens/session_cost.py")
    assert registry.SUPERSEDED == {"session-cost"} and any(t["id"] == "my-own" for t in tools)
    local.write_text("tools:\n- {id: rotate, title: clash, path: '${FIELDKIT}/elsewhere/rotate.py'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        registry.load()                                            # anything else is still a duplicate


def test_room_hook_prints_the_rotation_line_at_a_prompt(tmp_path, monkeypatch, capsys):
    p = monster(tmp_path)
    monkeypatch.setattr(room, "unseen", lambda sid: [])
    monkeypatch.setattr(room, "context", lambda lines: "")
    monkeypatch.setattr(room, "_hog", lambda c, e, ctx: ctx)
    room.hook(io.StringIO(json.dumps(call(p, "UserPromptSubmit"))))
    out = json.loads(capsys.readouterr().out)
    assert "SESSION ROTATION" in out["hookSpecificOutput"]["additionalContext"]


def test_mid_turn_rotate_is_said_once_even_after_the_throttle(tmp_path):
    p = monster(tmp_path)
    t = time.time()
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse", now=t).startswith("SESSION ROTATION")
    assert rotate.hook_line(call(p, "PostToolUse"), "PostToolUse", now=t + 120) == ""


def test_facts_ignore_sidechain_lines_in_the_main_transcript(tmp_path):
    p = monster(tmp_path)
    write(p, [{**line(mid="sc", read=10), "isSidechain": True, "cwd": "C:/elsewhere", "gitBranch": "other"}],
          mode="a")
    r = rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p)
    assert (r["cwd"], r["branch"]) == ("C:/work/repo", "feat/x")
