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


def _p(key):
    """A part of the LIFECYCLE block, from rules/rules.yaml (the one file every rule lives in, A4 2026-11-01)."""
    from .. import rules_lint
    return rules_lint.parts("LIFECYCLE")[key]


def __getattr__(name):                                # ZERO_ARCHIVE stays importable for the fieldkit-net skill
    if name == "ZERO_ARCHIVE":
        return _p("zero_archive")
    raise AttributeError(name)


def done_protocol(task_id=None, headless=False, zero=False):
    """The numbered steps a job ends with. headless: the run exits instead of archiving a desktop session. zero: the
    full Zero hand-over text (the fieldkit-net skill); every other agent gets a one-line pointer (owner 2026-10-31:
    rules only where they are needed). B1 (2026-10-31): a headless run pushes and opens its PR like a session."""
    report = _p("report_task").replace("{task_id}", str(task_id)) if task_id else _p("report_board")
    last = _p("last_headless") if headless else \
        _p("last_session").replace("{zero}", _p("zero_archive") if zero else _p("zero_pointer"))
    return _p("done_head") + "\n" + _p("steps").replace("{report}", report).replace("{last}", last) + "\n"


def side_jobs_rule():
    return _p("side_jobs")


def brief_tail(task_id=None, headless=False):
    """What every brief the harness writes ends with."""
    return "\n" + side_jobs_rule() + "\n\n" + done_protocol(task_id, headless)


def rule_text():
    return (f"{RULE_BEGIN}\n"
            f"{_p('heading')}\n\n"
            f"{side_jobs_rule()}\n\n"
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
