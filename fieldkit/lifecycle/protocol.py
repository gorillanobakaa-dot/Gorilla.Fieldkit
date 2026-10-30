"""The words every brief ends with, and the standing rule every agent reads: start side jobs headless, never as a
card; when the job is done, clean up and archive yourself.

Owner, 2026-10-26 (verbatim): "along with the instructions you send, you also have to tell them (this needs to be
implemented in the harness in a deterministic way via tools and checks) that from there they also have to archive
the unused sessions to keep your workspace clean. All these idle sessions eats a lot of cycles and processor power
and ram ... We want zero friction. Long horizon tasks run unattended".

Who appends it: fieldkit rotate handover (the relief's brief), fieldkit task start (every headless job), and any other
brief writer through brief_tail(). The rule block goes into each agent's own instruction file (rule_files()), the same
way fieldkit rotate's block does: between markers, replaced in place, the file's line endings kept.
"""
import os
from pathlib import Path

RULE_BEGIN, RULE_END = "<!-- fieldkit-lifecycle-rule v1 -->", "<!-- /fieldkit-lifecycle-rule -->"


def done_protocol(task_id=None, headless=False):
    """The numbered steps a job ends with. headless: the run exits instead of archiving a desktop session."""
    report = (f"`fieldkit task report {task_id} --status done --branch <branch> [--pr <url>] --note \"<one line>\"`"
              if task_id else "a Board row in state/coordination/PLAN.md (what was done, what was NOT verified)")
    last = ("exit: the run ends here (nothing stays running)" if headless else
            "archive yourself: Claude desktop `archive_session \"self\"` (turn Remote Control off first with "
            "`set_remote_control` if it is on: it blocks the archive); any other agent ends its session")
    return ("DONE PROTOCOL (fieldkit lifecycle; do every step, in this order, without asking anyone):\n"
            "1. the tests covering your changes pass (the full suite runs once, when the project is finished); commit "
            "on your own branch (never main).\n"
            "2. push with `fieldkit ship push` (the owner-wide mandate covers it; never a GORILLA_OWNER_APPROVED line).\n"
            "3. open the pull request where your agent can (a headless run reports the branch; the command post opens "
            "it).\n"
            f"4. report: {report}.\n"
            "5. recycle what YOUR job left (test copies, scratch, your own worktrees) with `fieldkit size recycle`; "
            "unload any local model you loaded.\n"
            "6. stop your own background jobs and listeners (by PID, never by name).\n"
            f"7. {last}.\n")


def side_jobs_rule():
    return ("SIDE JOBS: start a relief or side job with `fieldkit task start BRIEF.md [--on DEVICE]` (a headless "
            "autopilot run here, or the other computer's link listener over the cable). Never propose a desktop "
            "session card or ask a person to click: a card waits for the owner, who is asleep.")


def brief_tail(task_id=None, headless=False):
    """What every brief the harness writes ends with."""
    return "\n" + side_jobs_rule() + "\n\n" + done_protocol(task_id, headless)


def rule_text():
    return (f"{RULE_BEGIN}\n"
            "## Zero friction: headless side jobs, archive when done (fieldkit lifecycle)\n\n"
            f"{side_jobs_rule()}\n\n"
            "Nothing may wait on the owner (no cards, prompts or [NEEDS YOU] for what the mandate covers): "
            "`fieldkit friction` lists what could, exit 0 = none.\n\n"
            "Idle sessions eat RAM and CPU. The command post runs `fieldkit sessions sweep` on every watchdog wake "
            "and archives what it lists.\n\n"
            + done_protocol() +
            f"{RULE_END}\n")


def rule_files(home=None, env=None):
    """Each agent's own global instruction file: Claude Code, Antigravity/Gemini, Codex."""
    env = os.environ if env is None else env
    home = Path(home or Path.home())
    return {"claude": Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude") / "CLAUDE.md",
            "antigravity": home / ".gemini" / "GEMINI.md",
            "codex": Path(env.get("CODEX_HOME") or home / ".codex") / "AGENTS.md"}


def rule_present(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return RULE_BEGIN in text and rule_text().strip() in text.replace("\r\n", "\n")


def install_rule(path):
    """Add the block, or replace an older copy in place. Keeps the file's line endings. -> added|updated|same."""
    path = Path(path)
    raw = path.read_bytes() if path.is_file() else b""
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    block = rule_text()
    if RULE_BEGIN in text and RULE_END in text:
        head, rest = text.split(RULE_BEGIN, 1)
        tail = rest.split(RULE_END, 1)[1]
        new = head + block + (tail[1:] if tail.startswith("\n") else tail)
        how = "same" if new == text else "updated"
    else:
        new = text + ("" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n") + block
        how = "added"
    if how != "same":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new.replace("\n", nl).encode("utf-8"))
    return how
