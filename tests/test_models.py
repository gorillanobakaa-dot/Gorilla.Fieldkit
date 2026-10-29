"""Cost guard (data: fieldkit/lifecycle/models.yaml; mutation plan: tests/models_mutations.yaml) (HARNESS-PLAN I5 follow-up, owner order 2026-10-29): no device or session starts an expensive model
without a deterministic yes. No network, no cable, no real model: fakes for the process and the clock; the mandate
files are written in a temp folder and the seal check is replaced only where the test is about the allow list."""
import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from fieldkit.lifecycle import models, tasks
from fieldkit.peers import link


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    from fieldkit.mandate import mandate as M
    monkeypatch.setattr(M, "mandates", lambda: [])


def signed(tmp_path, monkeypatch, allowed, expires="2026-11-01"):
    """One mandate whose seal check is faked as 'in force until its expires'; the allow list is the real code."""
    from fieldkit.mandate import mandate as M
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"project": "t", "models_allowed": allowed, "expires": expires}), encoding="utf-8")
    monkeypatch.setattr(M, "mandates", lambda: [p])
    monkeypatch.setattr(M, "load", lambda path: yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    monkeypatch.setattr(M, "verify", lambda path, today=None: (
        ((today or date.today()) <= date.fromisoformat(expires), "expires")))
    return p


def rows():
    return models.read_ledger()


def test_tiers_and_unknown_is_premium():
    assert [models.tier_of(m) for m in ("claude-sonnet-5-5", "haiku", "local/qwen3", "claude-opus-5-5",
                                         "claude-fable-5-1", "carbon-4", "gpt-9", "brand-new-x")] == \
        ["cheap", "cheap", "cheap", "review", "premium", "premium", "premium", "premium"]


def test_cheap_allowed_default_is_cheap_and_cheap_is_not_ledgered():
    r = models.check_model(None, "worker", "t")
    assert r["ok"] and r["model"] == "sonnet" and not rows()


def test_premium_and_unknown_refused_and_logged(tmp_path):
    for m in ("claude-fable-5-1", "mythos-2", "totally-new-flagship"):
        r = models.check_model(m, "worker", "link from vaio", device="[VAIO-LNX]")
        assert not r["ok"] and r["verdict"] == "refused"
    got = rows()
    assert len(got) == 3 and got[0]["device"] == "[VAIO-LNX]" and got[0]["origin"] == "link from vaio" and \
        got[0]["role"] == "worker" and got[0]["verdict"] == "refused" and got[0]["model"] == "claude-fable-5-1"


def test_opus_only_for_the_reviewer_role():
    assert models.check_model("claude-opus-5-5", "reviewer", "autopilot")["ok"]
    assert not models.check_model("claude-opus-5-5", "worker", "autopilot")["ok"]
    assert rows()[0]["verdict"] == "allowed" and rows()[0]["tier"] == "review"      # an allowed expensive start is kept


def test_odd_model_names_are_refused():
    assert not models.check_model("sonnet; rm -rf /", "worker", "link")["ok"]


def test_owner_signed_allow_works_and_expires(tmp_path, monkeypatch):
    signed(tmp_path, monkeypatch, ["fable"])
    ok = models.check_model("claude-fable-5-1", "worker", "t", today=date(2026, 10, 30))
    assert ok["ok"] and "mandate" in ok["why"] and rows()[-1]["verdict"] == "allowed"
    late = models.check_model("claude-fable-5-1", "worker", "t", today=date(2026, 11, 2))
    assert not late["ok"]
    assert not models.check_model("mythos-2", "worker", "t", today=date(2026, 10, 30))["ok"]    # only what is named


def test_an_unsigned_mandate_allows_nothing(tmp_path, monkeypatch):
    from fieldkit.mandate import mandate as M
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"project": "t", "models_allowed": ["fable"], "expires": "2999-01-01"}))
    monkeypatch.setattr(M, "mandates", lambda: [p])                  # the real verify: no seal, no key, not in force
    assert not models.check_model("claude-fable-5-1", "worker", "t")["ok"]


def test_guard_launch_rewrites_flag_and_env():
    argv, env = models.guard_launch(["claude", "-p"], {}, "worker", "t")
    assert argv[-2:] == ["--model", "sonnet"] and env["ANTHROPIC_MODEL"] == "sonnet"
    argv, env = models.guard_launch(["claude", "--model=haiku"], {"ANTHROPIC_MODEL": "haiku"}, "worker", "t")
    assert "--model=haiku" in argv
    with pytest.raises(models.Refused):
        models.guard_launch(["claude", "--model", "claude-fable-5-1"], {}, "worker", "t")
    with pytest.raises(models.Refused):                                # the env model cannot slip past the flag
        models.guard_launch(["claude", "--model", "sonnet"], {"ANTHROPIC_MODEL": "claude-fable-5-1"}, "worker", "t")
    with pytest.raises(models.Refused):
        models.guard_launch(["claude"], {"ANTHROPIC_MODEL": "claude-fable-5-1"}, "worker", "t")


def test_autopilot_claude_spawn_refuses_premium_before_any_process():
    from fieldkit.autopilot import run
    called = []
    r = run.claude("hi", model="claude-fable-5-1", popen=lambda *a, **k: called.append(a))
    assert r.get("refused") and not called


def test_autopilot_claude_spawn_passes_the_cheap_default(monkeypatch):
    from fieldkit.autopilot import run
    seen = {}
    monkeypatch.setattr(run, "claude_cmd", lambda: ["claude"])
    from fieldkit.core import contain

    def fake(args, **kw):
        seen.update(args=args, env=kw["env"])
        raise RuntimeError("stop here")
    monkeypatch.setattr(contain, "run", fake)
    with pytest.raises(RuntimeError):
        run.claude("hi")
    assert seen["args"][-2:] == ["--model", "sonnet"] and seen["env"]["ANTHROPIC_MODEL"] == "sonnet"


def test_task_start_refuses_premium_even_with_allow_expensive(tmp_path):
    b = tmp_path / "b.md"
    b.write_text("do it\n")
    with pytest.raises(ValueError):
        tasks.start(b, model="claude-fable-5-1", allow_expensive=True, worktree=lambda *a: (None, ""),
                    call=lambda *a, **k: {}, launch=lambda *a, **k: None)
    assert rows()[-1]["origin"].startswith("task start") and rows()[-1]["verdict"] == "refused"


def start_remote(tmp_path, monkeypatch, model, sender_says=None):
    (tmp_path / "link").mkdir(exist_ok=True)
    (tmp_path / "link" / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") +
                                                 "\nwall_minutes: 30\nstart_commands: {}\n")
    link.approve(confirm=True, say=lambda m: None)
    started = []

    class Live:
        pid = 1

        def poll(self):
            return None

    def pop(argv, **kw):
        started.append((argv, kw.get("env")))
        return Live()
    from fieldkit.lifecycle import worktrees
    monkeypatch.setattr(worktrees, "reap", lambda *a, **k: None)
    monkeypatch.setattr(worktrees, "own", lambda *a, **k: None)
    ok, why = link.start_session("claude", "work", "vaio", popen=pop, inbox_add=lambda *a, **k: None,
                                 flags_fn=lambda: {"--model", "--cwd"}, sleep=lambda s: None,
                                 check_fn=lambda a: (True, ""), follow=lambda *a, **k: None,
                                 worktree=lambda *a: (None, "no worktree"), model=model)
    return ok, why, started


def test_listener_refuses_a_remote_premium_or_unknown_model(tmp_path, monkeypatch):
    for m in ("claude-fable-5-1", "some-unknown-flagship"):
        ok, why, started = start_remote(tmp_path, monkeypatch, m)
        assert not ok and "model refused" in why and not started
    assert [r["origin"] for r in rows()] == ["link from vaio"] * 2


def test_listener_starts_cheap_and_defaults_when_none_sent(tmp_path, monkeypatch):
    ok, why, started = start_remote(tmp_path, monkeypatch, None)
    assert ok and started[0][0][-2:] == ["--model", "sonnet"] and started[0][1]["ANTHROPIC_MODEL"] == "sonnet"


def test_sender_cannot_override_the_listeners_own_data(tmp_path, monkeypatch):
    """The sender's claim about the tier (a model string made to look cheap) is not read: the listener's own list rules."""
    ok, why, started = start_remote(tmp_path, monkeypatch, "claude-fable-5-1-sonnet-haiku-local")
    assert not ok and not started                                      # 'fable' wins over the cheap words


def test_listener_honors_its_own_owner_signed_allow(tmp_path, monkeypatch):
    signed(tmp_path, monkeypatch, ["fable"], expires="2999-01-01")
    ok, why, started = start_remote(tmp_path, monkeypatch, "claude-fable-5-1")
    assert ok and started[0][1]["ANTHROPIC_MODEL"] == "claude-fable-5-1" and rows()[-1]["verdict"] == "allowed"


def test_report_counts_per_device_and_day(capsys):
    models.check_model("claude-fable-5-1", "worker", "t", device="A")
    models.check_model("claude-fable-5-1", "worker", "t", device="A")
    models.check_model("mythos", "worker", "t", device="B")
    assert models.main(["report"]) == 0
    out = capsys.readouterr().out
    assert " A " in out and out.count("refused") == 2 and "2\n" in out


def test_session_line_is_advisory_for_premium_only():
    assert models.session_line("claude-fable-5-1") == "model claude-fable-5-1 is premium; jobs default to sonnet"
    assert models.session_line("claude-sonnet-5-5") == "" and models.session_line(None) == ""


def test_missing_data_is_an_error_not_defaults(tmp_path):
    with pytest.raises(ValueError):
        models.load(tmp_path / "nope.yaml")
