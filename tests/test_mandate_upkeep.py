# Version: 1.0.0 · updated 26-10-26-12-00
"""The owner-wide mandate made permanent (owner, 2026-10-26: "fa permanent 1,2,3,4"): the doctor, the gate-aware hook
installed from Fieldkit's template, renewal with the owner's yes and passphrase, the expiry warning (one line, once
per session per day), and the router that sends a hand-typed git push in a covered clone to fieldkit ship push."""
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

from fieldkit.mandate import mandate as M
from fieldkit.mandate import upkeep as U

from test_mandate_owner_wide import GOOD, clone, git, home, write_mandate  # noqa: F401 - fixtures

PW = "correct horse battery"
TODAY = date(2026, 10, 29)


def short(home, ends="2026-11-01", name="owner-wide"):
    return write_mandate(home, dict(GOOD, expires=ends), name=name)


# ---- the expiry warning --------------------------------------------------------------------------------------------
def test_three_days_before_the_end_one_line_names_the_renew_command(home):
    f = short(home)
    w = U.warning(TODAY)
    assert "owner-wide ends 2026-11-01 (3 day(s) left)" in w and f"fieldkit mandate renew {f}" in w
    assert "\n" not in w


def test_four_days_before_the_end_says_nothing(home):
    short(home)
    assert U.warning(date(2026, 10, 28)) == ""


def test_after_the_end_it_says_expired_for_two_weeks_then_nothing(home):
    short(home)
    assert "EXPIRED on 2026-11-01" in U.warning(date(2026, 11, 3))
    assert U.warning(date(2026, 11, 20)) == ""


def test_an_old_expired_mandate_is_not_news_while_another_is_in_force(home):
    short(home, ends="2026-10-25", name="trial")
    assert "trial EXPIRED" in U.warning(TODAY)
    short(home, ends="2026-11-20")
    assert U.warning(TODAY) == ""


def test_a_stopped_or_unsealed_mandate_gets_no_renew_line(home):
    f = short(home)
    M.stop(f)
    after = date(2026, 11, 3)                                   # past the end too: still nothing to renew
    assert U.warning(TODAY) == "" and U.warning(after) == ""
    write_mandate(home, dict(GOOD, expires="2026-11-01"), name="unsealed", approve=False)
    assert U.warning(TODAY) == "" and U.warning(after) == ""


def test_the_session_hook_says_it_once_per_session_per_day(home, tmp_path):
    short(home)
    seen = tmp_path / "seen"
    assert U.session_warning("s1", TODAY, seen)
    assert U.session_warning("s1", TODAY, seen) == ""            # never a nag loop
    assert U.session_warning("s2", TODAY, seen)                  # another session hears it once too
    assert U.session_warning("s1", date(2026, 10, 30), seen)     # and the next day once more


def test_status_and_start_carry_the_warning(home, capsys, monkeypatch):
    short(home)
    monkeypatch.setattr(U, "warning", lambda today=None, paths=None: "MANDATE owner-wide ends soon")
    M.main(["status"])
    assert "MANDATE owner-wide ends soon" in capsys.readouterr().out
    from fieldkit.mandate import project
    lines, _ = project.start(smoke={"ok": True})
    assert "MANDATE owner-wide ends soon" in lines


def test_the_room_hook_adds_the_warning_on_a_prompt(home, monkeypatch, capsys, tmp_path):
    import io
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setattr(U, "warning", lambda today=None, paths=None: "MANDATE owner-wide ends 2026-11-01")
    room.hook(io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "abc"})))
    out = capsys.readouterr().out
    assert "MANDATE owner-wide ends 2026-11-01" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
    room.hook(io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "abc"})))
    assert "MANDATE" not in capsys.readouterr().out


# ---- renew -----------------------------------------------------------------------------------------------------------
def test_renew_moves_only_the_date_and_seals_it_again(home):
    f = short(home)
    before = f.read_text(encoding="utf-8")
    code, msg = U.renew(f, PW, True, days=7, today=TODAY)
    assert code == 0, msg
    after = f.read_text(encoding="utf-8")
    assert "expires: 2026-11-05" in after.splitlines()
    assert [x for x in before.splitlines() if not x.startswith("expires:")] == \
        [x for x in after.splitlines() if not x.startswith("expires:")]
    assert M.verify(f, TODAY)[0] and M.verify(f, date(2026, 11, 5))[0] and not M.verify(f, date(2026, 11, 6))[0]
    assert any(p.name.startswith("owner-wide.yaml.") and p.name.endswith(".prev") for p in f.parent.iterdir())
    assert not any(p.suffix == ".yaml" and p != f for p in f.parent.iterdir())    # a backup is never a mandate
    assert any(e["what"] == "renewed" for e in M.read_log(f))


def test_renew_with_a_wrong_passphrase_changes_nothing(home):
    f = short(home)
    before, seal = f.read_bytes(), f.with_suffix(".seal").read_bytes()
    code, msg = U.renew(f, "the wrong passphrase", True, today=TODAY)
    assert code == 3 and "nothing changed" in msg
    assert f.read_bytes() == before and f.with_suffix(".seal").read_bytes() == seal and M.verify(f, TODAY)[0]


def test_a_seal_that_fails_puts_the_old_mandate_back(home, monkeypatch):
    f = short(home)
    before = f.read_bytes()
    monkeypatch.setattr(M, "approve", lambda *a, **k: (3, "the seal failed"))
    code, msg = U.renew(f, PW, True, today=TODAY)
    assert code == 3 and "old mandate and seal are back" in msg
    assert f.read_bytes() == before and M.verify(f, TODAY)[0]


def test_a_stopped_mandate_is_never_renewed(home):
    f = short(home)
    M.stop(f)
    before = f.read_bytes()
    assert U.renew(f, PW, True, today=TODAY)[0] == 4
    assert not M.verify(f, TODAY)[0]
    assert f.read_bytes() == before and not any(p.suffix == ".prev" for p in f.parent.iterdir())   # never touched


def test_renew_without_the_typed_yes_does_nothing(home, monkeypatch, capsys):
    f = short(home)
    before = f.read_bytes()
    assert U.renew(f, PW, False, today=TODAY)[0] == 1
    assert M.main(["renew", str(f), "--days", "7"]) == 3          # pytest's stdin is no terminal: refused
    assert "real terminal" in capsys.readouterr().out and f.read_bytes() == before


def test_the_renew_screen_is_the_approve_screen(home, monkeypatch, capsys):
    f = short(home)
    monkeypatch.setattr(M, "_typed_yes", lambda prompt, isatty=None: False)
    M.main(["renew", str(f)])
    out = capsys.readouterr().out
    assert "NOT COVERED" in out and "THREAT MODEL" in out and "branches of Gorilla.Opencode:" in out


# ---- the hooks ---------------------------------------------------------------------------------------------------------
def hook(repo, text):
    p = repo / ".git" / "hooks" / "pre-push"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("text,name,state,ok", [
    (None, "pfind", "none", True),
    ("#!/bin/sh\nfieldkit privacy push-check .\n", "pfind", "no-flag", True),
    ('#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" != "1" ] && exit 1\n', "pfind", "NEEDS-GATE", False),
    ('#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" != "1" ] && fieldkit mandate gate --repo . || exit 1\n', "pfind",
     "honors", True),
    ('#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" != "1" ] && exit 1\n', "gorilla-firefox", "by-design", True),
])
def test_each_kind_of_hook_is_told_apart(clone, text, name, state, ok):
    if text is not None:
        hook(clone, text)
    row = U.classify(clone, name)
    assert (row["state"], row["ok"]) == (state, ok), row


def test_a_flag_hook_whose_script_asks_the_gate_honors_it(clone):
    s = clone / "scripts" / "git-hooks"
    s.mkdir(parents=True)
    (s / "check.py").write_text('if env.get("GORILLA_OWNER_APPROVED") != "1":\n    run(["mandate", "gate"])\n')
    hook(clone, "#!/bin/sh\nexec python scripts/git-hooks/check.py pre-push\n")
    assert U.classify(clone, "pfind")["state"] == "honors"


def test_the_opencode_clone_without_the_template_needs_it(clone):
    hook(clone, '#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" = "1" ] || exit 1\n')
    row = U.classify(clone, "Gorilla.Opencode")
    assert row["state"] == "template-old" and not row["ok"] and "install-hooks --yes" in row["why"]


def test_install_hooks_previews_without_yes_and_writes_nothing(clone):
    old = hook(clone, "#!/bin/sh\nexit 1\n")
    said = []
    assert U.install_hooks(False, {"Gorilla.Opencode": [clone]}, {"Gorilla.Opencode": "x"}, said.append) == 1
    assert "WOULD INSTALL" in said[0] and old.read_text() == "#!/bin/sh\nexit 1\n"
    assert not (clone / "scripts").exists()


def test_install_hooks_with_yes_installs_the_template_keeps_the_old_and_verifies(clone, monkeypatch):
    old = hook(clone, "#!/bin/sh\nexit 1\n")
    monkeypatch.setattr(U, "_verify_installed", lambda repo: (True, "verified"))
    said = []
    assert U.install_hooks(True, {"Gorilla.Opencode": [clone]}, {"Gorilla.Opencode": "x"}, said.append,
                           today=TODAY) == 0
    t = U.template_dir("Gorilla.Opencode")
    assert (clone / "scripts" / "git-hooks" / "hooks.py").read_bytes() == U._norm((t / "hooks.py").read_bytes())
    assert old.read_bytes() == U._norm((t / "pre-push").read_bytes())
    assert (old.parent / "pre-push.bak-2026-10-29").read_text() == "#!/bin/sh\nexit 1\n"
    assert "INSTALLED" in said[0]
    assert U.classify(clone, "Gorilla.Opencode")["state"] == "honors"
    said.clear()                                                    # a second run changes nothing
    assert U.install_hooks(True, {"Gorilla.Opencode": [clone]}, {"Gorilla.Opencode": "x"}, said.append) == 0
    assert "the template is installed" in said[0]


def test_install_hooks_never_writes_a_hook_it_has_no_template_for(clone):
    hook(clone, '#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" = "1" ] || exit 1\n')
    said = []
    assert U.install_hooks(True, {"pfind": [clone]}, {"pfind": "x"}, said.append) == 1
    assert said[0].startswith("  NEEDS") and not (clone / "scripts").exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh to run the hook with")
def test_the_installed_hook_is_verified_by_running_it(clone, monkeypatch):
    for src, dst in U._install_targets(clone, "Gorilla.Opencode").items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(U._norm(U._src(src).read_bytes()))
    # the gate this hook calls is THIS checkout's (fieldkit on PATH imports from PYTHONPATH first)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(Path(M.__file__).parents[2]),
                                                      os.environ.get("PYTHONPATH", "")]))
    ok, why = U._verify_installed(clone)
    assert ok, why
    hp = clone / "scripts" / "git-hooks" / "hooks.py"
    hp.write_text("import sys\nsys.exit(0)\n")                     # a hook that lets every push go
    assert not U._verify_installed(clone)[0]
    hp.write_text("import os, sys\nif os.environ.get('FIELDKIT_SHIP_PUSH') == '1':\n"
                  "    sys.stderr.write('no push lines\\n'); sys.exit(1)\nsys.exit(0)\n")   # the gate, but no flag check
    assert not U._verify_installed(clone)[0]


def test_the_stick_folder_carries_the_templates_and_the_clones_to_check(tmp_path):
    rows = [{"repo": "Gorilla.Opencode", "clone": "C:/x", "state": "honors", "ok": True, "why": ""}]
    out = U.stick(tmp_path / "stick", rows=rows, covered={"Gorilla.Opencode": "m", "debian-kernel": "m"})
    sums = json.loads((out / "SHA256SUMS.json").read_text())
    assert "templates/gorilla-opencode/hooks.py" in sums
    clones = (out / "CLONES-TO-CHECK.txt").read_text()
    assert "debian-kernel" in clones and "gorilla-opencode" in clones
    assert "install-hooks --yes" in (out / "README.txt").read_text()
    with pytest.raises(FileExistsError):
        U.stick(out, rows=rows, covered={})


def test_the_template_hook_is_committed_executable():
    t = U.template_dir("Gorilla.Opencode") / "pre-push"
    r = subprocess.run(["git", "-C", str(t.parent), "ls-files", "--stage", t.name], capture_output=True, text=True)
    if r.returncode or not r.stdout:
        pytest.skip("not a git checkout")
    assert r.stdout.split()[0] == "100755", r.stdout              # 100644: Linux and macOS git skip the hook


def test_git_skips_a_hook_that_is_not_executable_except_on_windows():
    assert U.runnable("x", "win32", access=lambda *a: False)
    assert not U.runnable("x", "linux", access=lambda *a: False)
    assert not U.runnable("x", "darwin", access=lambda *a: False)
    assert U.runnable("x", "linux", access=lambda *a: True)


def test_a_hook_git_would_skip_is_reported_and_never_verified(clone, monkeypatch):
    hook(clone, '#!/bin/sh\n[ "$GORILLA_OWNER_APPROVED" = "1" ] || exit 1\n')
    monkeypatch.setattr(U, "runnable", lambda p, platform=None, access=None: False)
    row = U.classify(clone, "pfind")
    assert row["state"] == "not-executable" and not row["ok"] and "SKIPS" in row["why"]
    assert not U._verify_installed(clone)[0]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit; git runs a hook anyway")
def test_install_hooks_makes_the_hook_executable(clone, monkeypatch):
    old = hook(clone, "#!/bin/sh\nexit 1\n")
    old.chmod(0o644)
    monkeypatch.setattr(U, "_verify_installed", lambda repo: (True, "verified"))
    assert U.install_hooks(True, {"Gorilla.Opencode": [clone]}, {"Gorilla.Opencode": "x"}, lambda s: None) == 0
    assert os.access(old, os.X_OK)
    old.chmod(0o644)                                               # same bytes, not executable: installed again
    said = []
    U.install_hooks(True, {"Gorilla.Opencode": [clone]}, {"Gorilla.Opencode": "x"}, said.append)
    assert "INSTALLED" in said[0] and os.access(old, os.X_OK)


def test_the_template_is_the_hook_that_asks_the_gate():
    t = U.template_dir("Gorilla.Opencode")
    text = (t / "hooks.py").read_text(encoding="utf-8")
    assert '"mandate", "gate"' in text and "GORILLA_OWNER_APPROVED" in text
    assert b"\r\n" not in (t / "pre-push").read_bytes()              # sh reads LF only


# ---- the doctor ----------------------------------------------------------------------------------------------------------
def test_the_doctor_reports_force_days_keys_reviewer_and_each_hook(home):
    short(home)
    rows = [{"repo": "Gorilla.Opencode", "clone": "C:/oc", "state": "NEEDS-GATE", "ok": False, "why": "wants the flag"},
            {"repo": "debian-kernel", "clone": None, "state": "not-here", "ok": True, "why": "not cloned"}]
    f = U.doctor_facts(TODAY, rows=rows)
    text = "\n".join(U.doctor_lines(f))
    assert "IN FORCE" in text and "3 day(s) left" in text
    assert "owner signing key on this computer: yes" in text and "trusted here:" in text
    assert "review key pinned here (VAIO): no" in text
    assert "NEEDS hook Gorilla.Opencode" in text and "ok    hook debian-kernel" in text
    assert f["warning"] and not U.doctor_ok(f)                      # a hook that needs the gate fails the doctor
    rows[0].update(ok=True)
    assert U.doctor_ok(U.doctor_facts(TODAY, rows=rows))


def test_the_doctor_without_any_mandate_fails(home):
    f = U.doctor_facts(TODAY, rows=[])
    assert "NO MANDATE" in "\n".join(U.doctor_lines(f)) and not U.doctor_ok(f)


def test_every_repository_of_the_packaged_list_is_covered_even_with_another_mandate_here(home):
    write_mandate(home, dict(GOOD, repos={"only-this": {}}), name="trial")
    names = U.covered_repos()
    assert "only-this" in names and "Gorilla.Opencode" in names and "debian-kernel" in names


# ---- the router -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "git push origin feat/x",
    "cd {c} && git push -u origin feat/x",
    "git -C {c} push origin feat/x",
    'GIT_TRACE=1 "C:/Program Files/Git/cmd/git.exe" push',
    "git -c core.askPass=x push --tags",
])
def test_a_hand_typed_push_in_a_covered_clone_goes_to_ship_push(clone, cmd):
    from fieldkit.autopilot import toolrouter as tr
    r = tr.route(cmd.format(c=clone), cwd=str(clone))
    assert r and r["route"] == "git-push-by-hand" and "fieldkit ship push" in r["use"]
    assert "GORILLA_OWNER_APPROVED stays the owner's" in r["use"]


@pytest.mark.parametrize("cmd", [
    "python -m fieldkit ship push --repo .",
    "fieldkit ship push --tag v1.2.3",
    "git log --oneline origin/main..HEAD",
    "git status && git fetch origin",
    "echo 'run git push later'",
])
def test_ship_push_and_other_git_commands_pass(clone, cmd):
    from fieldkit.autopilot import toolrouter as tr
    assert tr.route(cmd, cwd=str(clone)) is None


def test_cd_into_a_covered_clone_then_push_is_judged_there(clone, tmp_path):
    from fieldkit.autopilot import toolrouter as tr
    elsewhere = tmp_path / "not-a-clone"
    elsewhere.mkdir()
    assert tr.route("git push", cwd=str(elsewhere)) is None
    r = tr.route(f'cd "{clone}" && git push origin feat/x', cwd=str(elsewhere))
    assert r and r["route"] == "git-push-by-hand"


def test_a_push_from_a_clone_no_mandate_covers_passes(clone):
    from fieldkit.autopilot import toolrouter as tr
    git(clone, "remote", "set-url", "origin", "https://example.invalid/someone/elses-repo.git")
    assert tr.route("git push origin main", cwd=str(clone)) is None


def test_the_router_hook_reads_the_calls_cwd(clone, capsys, tmp_path, monkeypatch):
    import io
    from fieldkit.autopilot import toolrouter as tr
    monkeypatch.chdir(tmp_path)                                  # the hook's own folder is no clone: the call's is
    tr.hook(io.StringIO(json.dumps({"tool_name": "Bash", "cwd": str(clone),
                                    "tool_input": {"command": "git push origin feat/x"}})))
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ---- the hard lines ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", ["fieldkit mandate renew x.yaml", "python -m fieldkit mandate install-hooks --yes"])
def test_renew_and_installing_hooks_are_the_owners_in_an_unattended_run(cmd):
    from fieldkit.autopilot import hardlines as H
    assert any(__import__("re").search(p, cmd, __import__("re").I) for p, _ in H.OWNER_ONLY)
    assert H.NEVER_OPENED.search(cmd)


def test_the_install_hooks_preview_and_the_doctor_are_not_hard_lined():
    import re
    from fieldkit.autopilot import hardlines as H
    for cmd in ("fieldkit mandate install-hooks", "fieldkit mandate doctor", "fieldkit mandate install-hooks --stick D"):
        assert not any(re.search(p, cmd, re.I) for p, _ in H.OWNER_ONLY), cmd
