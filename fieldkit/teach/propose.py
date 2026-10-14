"""From a teaching log to harness material: a pipeline draft, signature stubs, card candidates.

    fieldkit teach propose LOG.md --out DIR [--name NAME]

# WHY THIS EXISTS

The owner, 2026-10-14, after being told a teaching log is "raw material a person turns into
stages by hand": "Somebody should do somethin' about it. How hard could it be? Maybe that someone
is YOU." So this is the first consumer. It reads a log written by `fieldkit teach` or by
`~/.agents/teach.sh` (both shapes), and writes three files a person edits before anything is
believed:

  pipeline-draft.yaml   one stage per command, in order, each with the command as recorded and a
                        verify row taken from a line the command really printed
  signatures-draft.yaml one stub per command that failed: the pattern is the last line it printed;
                        cause and fix are marked TO FILL, because the log knows what happened,
                        not why
  cards-draft.yaml      programs run more than once with different arguments: candidates for a
                        card, title TO FILL
  PROPOSAL.md           the counts, and the next command (`fieldkit pipeline plan` on the draft)

# WHAT IT REFUSES TO DO

It never guesses a cause, never invents a fix, never marks a stage verified. A verify row that
came from the log is marked `from-log` so a reader knows it proves only that the command once
printed that line. Paths under the home folder are rewritten to `{home}` so the draft is not
tied to one machine; everything else is copied as the person typed it.
"""
import os
import re
from pathlib import Path

import yaml

_HEAD_SH = re.compile(r"^### (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+\$ (.*)$")           # teach.sh
_HEAD_FK = re.compile(r"^### \d+\. (\S* ?\S*)\s+(\w+)(?:: (.*))?$")                   # fieldkit teach
_EXIT = re.compile(r"^exit=(\d+)$")
_SKIP_PROGRAMS = {"cd", "echo", "printf", "cat", "ls", "tail", "head", "wc", "du", "df", "stat", "true"}


def parse(text):
    """-> [{"time", "command", "output", "rc", "ok"}] from either log shape."""
    recs, cur, mode, buf = [], None, None, []
    for line in text.splitlines():
        m = _HEAD_SH.match(line) or _HEAD_FK.match(line)
        if m:
            if cur:
                recs.append(cur)
            if m.re is _HEAD_SH:
                cur = {"time": m.group(1), "command": m.group(2), "output": "", "rc": None, "ok": None, "description": ""}
            else:
                cur = {"time": m.group(1).strip(), "command": "", "output": "", "rc": None, "ok": None,
                       "description": m.group(3) or ""}
            mode, buf = None, []
            continue
        if cur is None:
            continue
        if line.startswith("```"):
            if mode is None:
                mode = line[3:].strip() or "text"
                buf = []
            else:
                body = "\n".join(buf)
                if mode in ("bash", "powershell") and not cur["command"]:
                    cur["command"] = body.strip()
                else:
                    cur["output"] = body
                mode = None
            continue
        if mode is not None:
            buf.append(line)
            continue
        e = _EXIT.match(line)
        if e:
            cur["rc"] = int(e.group(1)); cur["ok"] = cur["rc"] == 0
        elif line.strip() == "FAILED":
            cur["ok"] = False
        elif line.strip() == "ok":
            cur["ok"] = True
    if cur:
        recs.append(cur)
    for r in recs:
        if r["ok"] is None:
            r["ok"] = True
    return recs


def program(command):
    """The program a command is about: the first word after any `cd X &&` and env assignments."""
    c = command.strip()
    c = re.sub(r"^(cd\s+\S+\s*&&\s*)+", "", c)
    c = re.sub(r"^(\w+=\S+\s+)+", "", c)
    for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", c):
        w = part.strip().split(" ")[0] if part.strip() else ""
        w = os.path.basename(w)
        if w and w not in _SKIP_PROGRAMS:
            return w
    return os.path.basename(c.split(" ")[0]) if c else ""


def _portable(s, home):
    return s.replace(home, "{home}").replace("~/", "{home}/")


def _verify_line(output):
    """The last line that looks like a result, not a path or a number alone."""
    for line in reversed([l.strip() for l in output.splitlines() if l.strip()]):
        if len(line) > 6 and not line.startswith("/") and not re.fullmatch(r"[\d.,% ]+", line):
            return line[:80]
    return ""


def propose(recs, name="from-teaching-log", home=None):
    home = home or str(Path.home())
    stages, sigs, by_program = [], [], {}
    group = None
    for r in recs:
        prog = program(r["command"]) or "shell"
        cmd = _portable(r["command"], home)
        by_program.setdefault(prog, []).append(cmd)
        if not r["ok"]:
            last = _verify_line(r["output"]) or "(no output)"
            sigs.append({"id": f"{re.sub(r'[^a-z0-9]+', '-', prog.lower())}-{len(sigs) + 1}",
                         "pattern": re.escape(last)[:160],
                         "check": None,
                         "cause": "TO FILL: what the log cannot know. The command was: " + cmd[:120],
                         "fix": "TO FILL"})
            continue
        # one stage per command, in order: a stage is the unit a person reads, edits and verifies. Chaining
        # a run of git commands into one stage (the first draft did) hid eleven steps behind one id.
        stages.append({"program": prog, "commands": [cmd], "verify_lines": [v for v in [_verify_line(r["output"])] if v]})
    out_stages = []
    for i, g in enumerate(stages, 1):
        sid = f"{re.sub(r'[^a-z0-9]+', '-', g['program'].lower())}-{i}"
        st = {"id": sid, "platforms": ["linux"],
              "run": {"cmd": ["bash", "-lc", " && ".join(g["commands"]) if len(g["commands"]) > 1 else g["commands"][0]]}}
        if g["verify_lines"]:
            st["verify"] = [{"output_contains": re.escape(g["verify_lines"][-1])}]
            st["verify_from_log"] = True
        out_stages.append(st)
    pipeline = {"name": name,
                "description": "DRAFT from a teaching log: every stage is a command that was run once; "
                               "every verify row is a line it printed that time. Edit before believing.",
                "vars": {"home": "${HOME}"},
                "stages": out_stages}
    cards = [{"id": re.sub(r"[^a-z0-9]+", "-", p.lower()), "title": "TO FILL", "harness": "desk",
              "entry": ["bash", "-lc", cmds[0]], "seen": len(cmds), "distinct": len(set(cmds))}
             for p, cmds in by_program.items() if len(set(cmds)) >= 2 and p not in ("bash", "python3", "python")]
    return {"pipeline": pipeline, "signatures": {"signatures": sigs}, "cards": {"candidates": cards},
            "counts": {"commands": len(recs), "failed": sum(1 for r in recs if not r["ok"]),
                       "stages": len(out_stages), "signatures": len(sigs), "cards": len(cards)}}


def write(log_path, out_dir, name=None, home=None):
    p = Path(log_path)
    if not p.is_file():
        return {"ok": False, "why": f"no such log: {p}"}
    recs = parse(p.read_text(encoding="utf-8", errors="replace"))
    if not recs:
        return {"ok": False, "why": f"{p} holds no commands in either log shape"}
    name = name or re.sub(r"[^a-z0-9]+", "-", p.stem.lower()).strip("-")[:40]
    d = propose(recs, name=name, home=home)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "pipeline-draft.yaml").write_text(yaml.safe_dump(d["pipeline"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    (out / "signatures-draft.yaml").write_text(yaml.safe_dump(d["signatures"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    (out / "cards-draft.yaml").write_text(yaml.safe_dump(d["cards"], sort_keys=False, allow_unicode=True), encoding="utf-8")
    c = d["counts"]
    (out / "PROPOSAL.md").write_text(
        f"# Proposal from {p.name}\n\n"
        f"- commands read: {c['commands']}, failed: {c['failed']}\n"
        f"- pipeline-draft.yaml: {c['stages']} stage(s); every verify row is marked verify_from_log: it proves only "
        f"that the command printed that line once\n"
        f"- signatures-draft.yaml: {c['signatures']} stub(s), cause and fix TO FILL\n"
        f"- cards-draft.yaml: {c['cards']} candidate(s), title TO FILL\n\n"
        f"Next: edit the drafts, copy pipeline-draft.yaml to fieldkit/build/pipelines/{name}.yaml, then\n"
        f"`fieldkit pipeline plan {name}` (changes nothing) before any `run`.\n", encoding="utf-8")
    return {"ok": True, "out": str(out), **c}
