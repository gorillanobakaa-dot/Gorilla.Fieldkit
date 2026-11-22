"""Agent-model guard: keep Claude Code's Explore subagent on Haiku, on every computer Fieldkit runs on.

WHY (owner 2026-11-22): Claude Code subagents inherit the parent session's model unless a user agent file pins one.
Measured on the L15 before this tool: 3 Explore runs on Opus 5.5 (21.9M tokens) and 1 on Sonnet 5.5 for read-only
searches that Haiku 5.5 does at a fraction of the price. The fix is one file, ~/.claude/agents/explore.md with
`model: haiku`. A file can be deleted, edited or shadowed by an update; this guard puts it back.

What it checks (deterministic, offline, no model, no network):
  1. ~/.claude/agents/explore.md exists and its bytes equal the template (CRLF and a BOM are not counted as changes).
     MISSING / WRONG_MODEL / ALTERED -> enforce writes the template back; the old file is copied aside first.
  2. No other file in ~/.claude/agents declares `name: Explore` (a second definition could win) -> CONFLICT;
     enforce moves it aside (never deleted).
  3. Report only (Fieldkit does not edit these): a project's .claude/agents file that names Explore with another
     model, CLAUDE_CODE_SUBAGENT_MODEL set to something other than haiku (process env or settings.json env), the
     main session model in settings.json.
  4. audit: the model every recent subagent really ran on, read from its own transcript (agent-*.jsonl, the first
     assistant message's `model`), per agent type. An Explore run on a non-Haiku model AFTER the template was last
     written means Claude Code ignored the override -> OVERRIDE IGNORED.

The room hook (fieldkit.autopilot.room) calls hook_line() at every prompt: a file read and a hash, silent when OK,
so it costs no tokens; a repair or a finding adds one line to the session's context. The audit runs once per session.

Usage:
  fieldkit agent-models check [--json]        exit 0 OK, 1 something to repair or report
  fieldkit agent-models enforce               repair (template back, conflicts moved aside); exit 0, 3 read-back failed
  fieldkit agent-models audit [--days 7] [--json]   models subagents really ran on; exit 1 when an override was ignored
  fieldkit agent-models template              print the template
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

#: the enforced file, byte for byte (LF, no BOM). Change it here, nowhere else.
TEMPLATE = (
    "---\n"
    "name: Explore\n"
    "description: Read-only search agent for broad fan-out searches across many files, directories, or naming "
    "conventions when only the conclusion is needed. Locates code; does not review or audit it.\n"
    "model: haiku\n"
    "tools: Read, Grep, Glob, Bash\n"
    "---\n"
    "Read-only search. Never edit or create files. Run independent searches in parallel, read excerpts (line ranges) "
    "not whole files, and report findings concisely with file:line references.\n"
)
FILENAME = "explore.md"
AGENT = "Explore"
WANT_MODEL = "haiku"
BOM = b"\xef\xbb\xbf"


def claude_home():
    return Path(os.environ.get("FIELDKIT_CLAUDE_HOME") or (Path.home() / ".claude"))


def trash_dir():
    return Path(os.environ.get("FIELDKIT_AGENT_MODELS_TRASH") or (Path.home() / "Agents.Work.Trash" / "claude-agents"))


def state_dir():
    d = os.environ.get("FIELDKIT_AGENT_MODELS_DIR")
    if not d:
        from ..core.settings import state_root
        d = state_root() / "state" / "agent-models"
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(b):
    """CRLF and a UTF-8 BOM are an editor's doing, not a change of content."""
    if b.startswith(BOM):
        b = b[len(BOM):]
    return b.replace(b"\r\n", b"\n")


def _sha(b):
    return hashlib.sha256(b).hexdigest()


TEMPLATE_SHA = _sha(TEMPLATE.encode("utf-8"))


def frontmatter(text):
    """-> {key: value} from a leading '---' block; {} when there is none. Keys lower-cased."""
    lines = text.replace("\r\n", "\n").lstrip("\ufeff").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    out = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            return out
        if ":" in ln and not ln.startswith((" ", "\t")):
            k, v = ln.split(":", 1)
            out[k.strip().lower()] = v.strip().strip("'\"")
    return {}


def _read(p):
    try:
        return p.read_bytes()
    except OSError:
        return None


def _settings():
    try:
        return json.loads((claude_home() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _project_overrides(cwd=None):
    """Project agent files (cwd and its parents) that define Explore on another model. Report only."""
    rows = []
    try:
        here = Path(cwd or os.getcwd()).resolve()
    except OSError:
        return rows
    home = claude_home().resolve() if claude_home().exists() else None
    for d in (here, *here.parents):
        ad = d / ".claude" / "agents"
        if home and ad.resolve() == (home / "agents").resolve():
            continue
        if not ad.is_dir():
            continue
        for f in sorted(ad.glob("*.md")):
            fm = frontmatter((_read(f) or b"").decode("utf-8", "replace"))
            if fm.get("name", "").lower() == AGENT.lower() and fm.get("model", "").lower() != WANT_MODEL:
                rows.append({"path": str(f), "model": fm.get("model") or "(inherit)"})
    return rows


def check(cwd=None):
    """-> {verdict, path, problems[], conflicts[], reports[]}. verdict: OK, MISSING, WRONG_MODEL, ALTERED, CONFLICT."""
    ad = claude_home() / "agents"
    p = ad / FILENAME
    r = {"path": str(p), "verdict": "OK", "problems": [], "conflicts": [], "reports": [], "template_sha": TEMPLATE_SHA}
    raw = _read(p)
    if raw is None:
        r["verdict"] = "MISSING"
        r["problems"].append(f"{p} is missing: Explore inherits the session's model")
    else:
        fm = frontmatter(raw.decode("utf-8", "replace"))
        r["model"] = fm.get("model") or "(inherit)"
        if fm.get("model", "").lower() != WANT_MODEL or fm.get("name", "").lower() != AGENT.lower():
            r["verdict"] = "WRONG_MODEL"
            r["problems"].append(f"{p}: name {fm.get('name') or '(none)'}, model {r['model']} (want {AGENT}, haiku)")
        elif _sha(_norm(raw)) != TEMPLATE_SHA:
            r["verdict"] = "ALTERED"
            r["problems"].append(f"{p} differs from the template (sha256 {_sha(_norm(raw))[:12]})")
    if ad.is_dir():
        for f in sorted(ad.iterdir()):
            if f.name.lower() == FILENAME or f.suffix.lower() != ".md" or not f.is_file():
                continue
            fm = frontmatter((_read(f) or b"").decode("utf-8", "replace"))
            if fm.get("name", "").lower() == AGENT.lower():
                r["conflicts"].append(str(f))
    if r["conflicts"]:
        r["problems"].append("another file also defines Explore: " + ", ".join(r["conflicts"]))
        if r["verdict"] == "OK":
            r["verdict"] = "CONFLICT"
    for o in _project_overrides(cwd):
        r["reports"].append(f"project agent {o['path']} defines Explore on {o['model']} (wins inside that project)")
    env = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or (_settings().get("env") or {}).get("CLAUDE_CODE_SUBAGENT_MODEL")
    if env and env.strip().lower() != WANT_MODEL:
        r["reports"].append(f"CLAUDE_CODE_SUBAGENT_MODEL={env}: may put every subagent on that model")
    r["session_model"] = _settings().get("model") or "(app default)"
    return r


def enforce(cwd=None):
    """Repair what check found: the old file and any conflicting definition copied/moved to the trash folder, the
    template written, read back. -> (code, lines). Never deletes."""
    r = check(cwd)
    if r["verdict"] == "OK":
        return 0, [f"AGENT MODELS OK  {r['path']} (Explore on haiku)"] + [f"REPORT  {x}" for x in r["reports"]]
    p = Path(r["path"])
    keep = trash_dir() / time.strftime("%Y%m%d-%H%M%S")
    lines = []
    if p.exists():
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, keep / FILENAME)
        lines.append(f"previous {FILENAME} copied to {keep}")
    for c in r["conflicts"]:
        keep.mkdir(parents=True, exist_ok=True)
        shutil.move(c, str(keep / Path(c).name))
        lines.append(f"conflicting {c} moved to {keep}")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(TEMPLATE.encode("utf-8"))
    os.replace(tmp, p)
    back = check(cwd)
    ok = back["verdict"] == "OK"
    lines.insert(0, f"AGENT MODELS {'REPAIRED' if ok else 'REPAIR FAILED'}  {p} was {r['verdict']}; template written"
                    f" (sha256 {TEMPLATE_SHA[:12]}), read back {back['verdict']}")
    _log({"t": time.time(), "was": r["verdict"], "problems": r["problems"], "after": back["verdict"]})
    return (0 if ok else 3), lines + [f"REPORT  {x}" for x in back["reports"]]


def _log(row):
    try:
        with open(state_dir() / "repairs.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _first_model(path, max_lines=400):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, ln in enumerate(fh):
                if i >= max_lines:
                    break
                if '"assistant"' not in ln:
                    continue
                try:
                    m = (json.loads(ln).get("message") or {}).get("model")
                except ValueError:
                    continue
                if m and m != "<synthetic>":
                    return m
    except OSError:
        pass
    return None


def audit(days=7.0, since=None):
    """-> {rows: [{agent, model, runs}], ignored: [{path, model, t}], since}. Reads subagent transcripts only."""
    now = time.time()
    floor = now - days * 86400
    p = claude_home() / "agents" / FILENAME
    since = since if since is not None else (p.stat().st_mtime if p.exists() else None)
    counts, ignored = {}, []
    for meta in (claude_home() / "projects").glob("*/*/subagents/agent-*.meta.json"):
        try:
            mt = meta.stat().st_mtime
        except OSError:
            continue
        if mt < floor:
            continue
        try:
            agent = json.loads(meta.read_text(encoding="utf-8")).get("agentType") or "(untyped)"
        except (OSError, ValueError):
            agent = "(untyped)"
        jl = meta.with_name(meta.name[: -len(".meta.json")] + ".jsonl")
        model = _first_model(jl) or "(unknown)"
        counts[(agent, model)] = counts.get((agent, model), 0) + 1
        if agent.lower() == AGENT.lower() and model != "(unknown)" and WANT_MODEL not in model.lower():
            try:
                started = jl.stat().st_ctime if os.name == "nt" else mt
            except OSError:
                started = mt
            if since is not None and started > since:
                ignored.append({"path": str(jl), "model": model, "t": started})
    rows = [{"agent": a, "model": m, "runs": n} for (a, m), n in sorted(counts.items())]
    return {"rows": rows, "ignored": ignored, "since": since, "days": days}


def hook_line(call, event):
    """One line for the session's context, or "". Every prompt: check, repair when needed (silent when OK). Once per
    session: the reports and the audit for an ignored override. Never raises."""
    try:
        if event not in ("UserPromptSubmit", "SessionStart"):
            return ""
        out = []
        r = check(call.get("cwd"))
        if r["verdict"] != "OK":
            code, lines = enforce(call.get("cwd"))
            out.append(lines[0] + ". Fieldkit agent-models guard: Explore subagents run on Haiku."
                       + ("" if code == 0 else " Run: fieldkit agent-models check"))
        sid = call.get("session_id") or Path(call.get("transcript_path") or "none").stem
        mark = state_dir() / "seen" / (sid + ".json")
        if not mark.exists():
            mark.parent.mkdir(parents=True, exist_ok=True)
            mark.write_text(json.dumps({"t": time.time()}), encoding="utf-8")
            for x in check(call.get("cwd"))["reports"]:
                out.append("AGENT MODELS REPORT  " + x)
            a = audit(days=2)
            if a["ignored"]:
                models = sorted({x["model"] for x in a["ignored"]})
                out.append(f"AGENT MODELS: OVERRIDE IGNORED  {len(a['ignored'])} Explore run(s) since the template was"
                           f" written ran on {', '.join(models)}, not haiku. Details: fieldkit agent-models audit")
        return "\n".join(out)
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(prog="fieldkit agent-models", description="Keep Claude Code's Explore subagent on Haiku.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Usage:")[0])
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    sub.add_parser("enforce")
    a = sub.add_parser("audit")
    a.add_argument("--days", type=float, default=7.0)
    a.add_argument("--json", action="store_true")
    sub.add_parser("template")
    args = p.parse_args(argv)
    if args.cmd == "template":
        sys.stdout.write(TEMPLATE)
        return 0
    if args.cmd == "enforce":
        code, lines = enforce()
        print("\n".join(lines))
        return code
    if args.cmd == "audit":
        r = audit(args.days)
        if args.json:
            print(json.dumps(r, indent=1))
        else:
            print(f"subagent runs in the last {args.days:g} days (model from each run's own transcript):")
            for row in r["rows"]:
                print(f"  {row['agent']:<20} {row['model']:<28} {row['runs']}")
            if not r["rows"]:
                print("  (none)")
            if r["ignored"]:
                print(f"OVERRIDE IGNORED  {len(r['ignored'])} Explore run(s) after the template was written, not on haiku:")
                for x in r["ignored"]:
                    print(f"  {x['model']}  {x['path']}")
            else:
                print("AUDIT OK  no Explore run on a non-haiku model since the template was written")
        return 1 if r["ignored"] else 0
    r = check()
    if getattr(args, "json", False):
        print(json.dumps(r, indent=1))
    else:
        print(f"{'AGENT MODELS OK' if r['verdict'] == 'OK' else 'AGENT MODELS ' + r['verdict']}  {r['path']}")
        for x in r["problems"]:
            print("  PROBLEM  " + x)
        for x in r["reports"]:
            print("  REPORT   " + x)
        print(f"  session model in settings.json: {r['session_model']} (the app's model picker overrides it)")
        if r["verdict"] != "OK":
            print("  repair: fieldkit agent-models enforce")
    return 0 if r["verdict"] == "OK" and not r["reports"] else 1


if __name__ == "__main__":
    sys.exit(main())
