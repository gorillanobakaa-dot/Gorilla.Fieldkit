"""Agent-model guard: keep Claude Code's built-in subagents on a cheap model, on every computer Fieldkit runs on.

WHY (owner 2026-11-22): Claude Code subagents inherit the parent session's model unless a user agent file pins one.
Measured on the L15 before this tool: 3 Explore runs on Opus 5.5 (21.9M tokens) and 1 on Sonnet 5.5 for read-only
searches that Haiku 5.5 does at a fraction of the price; general-purpose ran on Opus 2 times in 30 days. Pins:
  Explore          -> haiku   ~/.claude/agents/explore.md          (read-only searches)
  general-purpose  -> sonnet  ~/.claude/agents/general-purpose.md  (multi-step research and tasks, all tools;
                              owner: "do we need them to have an Opus or a fable reasoning power? If not, pin
                              general-purpose agents to sonnet too"). Design-heavy work stays in the main session.
A file can be deleted, edited or shadowed by an update; this guard puts it back.

What it checks per pin (deterministic, offline, no model, no network):
  1. the pin's file exists and its bytes equal the template (CRLF and a BOM are not counted as changes).
     MISSING / WRONG_MODEL / ALTERED -> enforce writes the template back; the old file is copied aside first.
  2. No other file in ~/.claude/agents declares the same `name:` (a second definition could win) -> CONFLICT;
     enforce moves it aside (never deleted).
  3. Report only (Fieldkit does not edit these): a project's .claude/agents file that names a pinned agent with
     another model, CLAUDE_CODE_SUBAGENT_MODEL set at all (process env or settings.json env; it can put every
     subagent on one model), the main session model in settings.json.
  4. audit: the model every recent subagent really ran on, read from its own transcript (agent-*.jsonl, the first
     assistant message's `model`), per agent type. A pinned agent's run on another model AFTER its template was
     last written means Claude Code ignored the override -> OVERRIDE IGNORED.

The room hook (fieldkit.autopilot.room) calls hook_line() ONCE, at SessionStart (never at a prompt: no file I/O per
prompt). A mismatch is REPORTED as one line naming the repair command; ~/.claude is never rewritten by the hook. An
error in the guard is one line too, never silence. `doctor --install` is the one door that writes: the templates, and
the settings.json env (CLAUDE_CODE_SUBAGENT_MODEL=SUBAGENT_MODEL, the one source) merged with a backup.

Usage:
  fieldkit agent-models check [--json]        exit 0 OK, 1 something to repair or report
  fieldkit agent-models enforce               repair (templates back, conflicts moved aside); exit 0, 3 read-back failed
  fieldkit agent-models audit [--days 7] [--json]   models subagents really ran on; exit 1 when an override was ignored
  fieldkit agent-models template              print the templates
  fieldkit agent-models doctor [--install]    kit state on this machine; --install writes templates + settings env
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

#: the enforced files, byte for byte (LF, no BOM). Change them here, nowhere else.
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
GP_TEMPLATE = (
    "---\n"
    "name: general-purpose\n"
    "description: General-purpose agent for researching complex questions, searching for code, and executing "
    "multi-step tasks. When you are searching for a keyword or file and are not confident that you will find the "
    "right match in the first few tries use this agent to perform the search for you.\n"
    "model: sonnet\n"
    "---\n"
    "Do the task you were given completely and report the result concisely: what you found or changed, with "
    "file:line references, and what you could not verify. Read excerpts (line ranges) rather than whole files.\n"
)
FILENAME = "explore.md"
AGENT = "Explore"
WANT_MODEL = "haiku"
#: the ONE source for CLAUDE_CODE_SUBAGENT_MODEL (owner fallback O2: haiku stays): the Explore pin, the installer
#: and check() all read it. A settings.json / environment value equal to it is the kit's own and is not reported.
SUBAGENT_MODEL = WANT_MODEL
#: what `doctor --install` merges into settings.json env (ported from claude-quota-fix Install.ps1). Only the first is
#: enforced; the others are written when absent and never overwritten.
SETTINGS_ENV = {"CLAUDE_CODE_SUBAGENT_MODEL": SUBAGENT_MODEL, "MAX_THINKING_TOKENS": "8000",
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "60"}
BOM = b"\xef\xbb\xbf"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


#: every pinned agent: name, file under ~/.claude/agents, model alias, template.
PINS = [
    {"agent": AGENT, "file": FILENAME, "model": WANT_MODEL, "template": TEMPLATE},
    {"agent": "general-purpose", "file": "general-purpose.md", "model": "sonnet", "template": GP_TEMPLATE},
]
for _p in PINS:
    _p["sha"] = _sha(_p["template"].encode("utf-8"))
TEMPLATE_SHA = PINS[0]["sha"]


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


def write_templates(agents_dir):
    """Write every pinned template into agents_dir (used by tests and fresh machines)."""
    agents_dir = Path(agents_dir)
    agents_dir.mkdir(parents=True, exist_ok=True)
    for pin in PINS:
        (agents_dir / pin["file"]).write_bytes(pin["template"].encode("utf-8"))


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
    """Project agent files (cwd and its parents) that define a pinned agent on another model. Report only."""
    rows = []
    try:
        here = Path(cwd or os.getcwd()).resolve()
    except OSError:
        return rows
    home = claude_home().resolve() if claude_home().exists() else None
    want = {p["agent"].lower(): p["model"] for p in PINS}
    for d in (here, *here.parents):
        ad = d / ".claude" / "agents"
        if home and ad.resolve() == (home / "agents").resolve():
            continue
        if not ad.is_dir():
            continue
        for f in sorted(ad.glob("*.md")):
            fm = frontmatter((_read(f) or b"").decode("utf-8", "replace"))
            name = fm.get("name", "").lower()
            if name in want and fm.get("model", "").lower() != want[name]:
                rows.append({"path": str(f), "agent": fm.get("name"), "model": fm.get("model") or "(inherit)"})
    return rows


def _check_pin(pin, ad):
    p = ad / pin["file"]
    r = {"agent": pin["agent"], "want": pin["model"], "path": str(p), "verdict": "OK", "problems": [],
         "conflicts": [], "template_sha": pin["sha"]}
    raw = _read(p)
    if raw is None:
        r["verdict"] = "MISSING"
        r["problems"].append(f"{p} is missing: {pin['agent']} inherits the session's model")
    else:
        fm = frontmatter(raw.decode("utf-8", "replace"))
        r["model"] = fm.get("model") or "(inherit)"
        if fm.get("model", "").lower() != pin["model"] or fm.get("name", "").lower() != pin["agent"].lower():
            r["verdict"] = "WRONG_MODEL"
            r["problems"].append(f"{p}: name {fm.get('name') or '(none)'}, model {r['model']} "
                                 f"(want {pin['agent']}, {pin['model']})")
        elif _sha(_norm(raw)) != pin["sha"]:
            r["verdict"] = "ALTERED"
            r["problems"].append(f"{p} differs from the template (sha256 {_sha(_norm(raw))[:12]})")
    if ad.is_dir():
        for f in sorted(ad.iterdir()):
            if f.name.lower() == pin["file"] or f.suffix.lower() != ".md" or not f.is_file():
                continue
            fm = frontmatter((_read(f) or b"").decode("utf-8", "replace"))
            if fm.get("name", "").lower() == pin["agent"].lower():
                r["conflicts"].append(str(f))
    if r["conflicts"]:
        r["problems"].append(f"another file also defines {pin['agent']}: " + ", ".join(r["conflicts"]))
        if r["verdict"] == "OK":
            r["verdict"] = "CONFLICT"
    return r


def check(cwd=None):
    """-> {verdict, path, model, problems[], conflicts[], reports[], pins[]}. verdict: OK, or the first failing pin's
    MISSING, WRONG_MODEL, ALTERED, CONFLICT (path and model are that pin's; the Explore pin's when all are OK)."""
    ad = claude_home() / "agents"
    pins = [_check_pin(pin, ad) for pin in PINS]
    bad = next((x for x in pins if x["verdict"] != "OK"), pins[0])
    r = {"verdict": bad["verdict"], "path": bad["path"], "agent": bad["agent"], "template_sha": bad["template_sha"],
         "problems": [m for x in pins for m in x["problems"]], "conflicts": [c for x in pins for c in x["conflicts"]],
         "reports": [], "pins": pins}
    if "model" in bad:
        r["model"] = bad["model"]
    for o in _project_overrides(cwd):
        r["reports"].append(f"project agent {o['path']} defines {o['agent']} on {o['model']} (wins inside that project)")
    env = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or (_settings().get("env") or {}).get("CLAUDE_CODE_SUBAGENT_MODEL")
    if env and str(env).strip().lower() != SUBAGENT_MODEL:
        r["reports"].append(f"CLAUDE_CODE_SUBAGENT_MODEL={env}: may put every subagent on that model")
    r["session_model"] = _settings().get("model") or "(app default)"
    return r


def _pinned_summary():
    return ", ".join(f"{p['agent']} on {p['model']}" for p in PINS)


def enforce(cwd=None):
    """Repair what check found: each old file and any conflicting definition copied/moved to the trash folder, the
    templates written, read back. -> (code, lines). Never deletes."""
    r = check(cwd)
    if r["verdict"] == "OK":
        return 0, [f"AGENT MODELS OK  ({_pinned_summary()})"] + [f"REPORT  {x}" for x in r["reports"]]
    keep = trash_dir() / time.strftime("%Y%m%d-%H%M%S")
    lines, fixed = [], []
    for pin, pr in zip(PINS, r["pins"]):
        if pr["verdict"] == "OK":
            continue
        fixed.append(f"{pin['file']} was {pr['verdict']}")
        p = Path(pr["path"])
        if p.exists():
            keep.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, keep / pin["file"])
            lines.append(f"previous {pin['file']} copied to {keep}")
        for c in pr["conflicts"]:
            keep.mkdir(parents=True, exist_ok=True)
            shutil.move(c, str(keep / Path(c).name))
            lines.append(f"conflicting {c} moved to {keep}")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(pin["template"].encode("utf-8"))
        os.replace(tmp, p)
    back = check(cwd)
    ok = back["verdict"] == "OK"
    lines.insert(0, f"AGENT MODELS {'REPAIRED' if ok else 'REPAIR FAILED'}  {'; '.join(fixed)}; template(s) written in"
                    f" {claude_home() / 'agents'}, read back {back['verdict']}")
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
    """-> {rows: [{agent, model, runs}], ignored: [{agent, path, model, t}], since}. Reads subagent transcripts only.
    since: a pinned run started after it on another model is an ignored override; default each pin's file mtime."""
    now = time.time()
    floor = now - days * 86400
    pins = {}
    for pin in PINS:
        f = claude_home() / "agents" / pin["file"]
        pins[pin["agent"].lower()] = (pin["model"], since if since is not None else
                                      (f.stat().st_mtime if f.exists() else None))
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
        want, t0 = pins.get(agent.lower(), (None, None))
        if want and model != "(unknown)" and want not in model.lower():
            try:
                started = jl.stat().st_ctime if os.name == "nt" else mt
            except OSError:
                started = mt
            if t0 is not None and started > t0:
                ignored.append({"agent": agent, "path": str(jl), "model": model, "t": started})
    rows = [{"agent": a, "model": m, "runs": n} for (a, m), n in sorted(counts.items())]
    # F2.3: every Explore run in the window not on Haiku, whenever it started (a run before the file existed counts)
    explore_off = [r for r in rows if r["agent"].lower() == AGENT.lower() and r["model"] != "(unknown)"
                   and WANT_MODEL not in r["model"].lower()]
    return {"rows": rows, "ignored": ignored, "explore_off": explore_off, "since": since, "days": days}


def hook_line(call, event):
    """One line for the session's context, or "". ONLY at SessionStart (F2.1): any other event, UserPromptSubmit
    included, returns "" without touching a file. A mismatch is reported with the repair command, never repaired here
    (the hook does not rewrite ~/.claude); the audit for an ignored override is added once. An error in the guard is
    one line, not silence."""
    if event != "SessionStart":
        return ""
    try:
        out = []
        r = check(call.get("cwd"))
        if r["verdict"] != "OK":
            out.append(f"AGENT MODELS {r['verdict']}  {'; '.join(r['problems'])[:300]}. Fieldkit agent-models guard: "
                       f"{_pinned_summary()}. Repair (the hook does not rewrite ~/.claude): fieldkit agent-models enforce")
        for x in r["reports"]:
            out.append("AGENT MODELS REPORT  " + x)
        a = audit(days=2)
        if a["ignored"]:
            runs = sorted({f"{x['agent']} on {x['model']}" for x in a["ignored"]})
            out.append(f"AGENT MODELS: OVERRIDE IGNORED  {len(a['ignored'])} run(s) since the templates were"
                       f" written ran off their pin: {', '.join(runs)}. Details: fieldkit agent-models audit")
        return "\n".join(out)
    except Exception as e:  # noqa: BLE001 - say it in one line: a silent guard is a guard nobody knows is down
        return f"AGENT MODELS GUARD ERROR  {type(e).__name__}: {str(e)[:160]}".replace("\n", " ")


def _merge_settings(want_env=None):
    """Merge SETTINGS_ENV (and `model: sonnet` when absent) into settings.json: keep every other key (hooks, ...), back the
    old file up to the trash folder, write atomically, read back. -> (ok, lines). A file that is not valid JSON is
    left alone and said."""
    want_env = SETTINGS_ENV if want_env is None else want_env
    sp = claude_home() / "settings.json"
    raw = _read(sp)
    data = {}
    if raw is not None:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("top level is not an object")
        except ValueError as e:
            return False, [f"SETTINGS NOT MERGED  {sp} is not valid JSON ({e}); fix it, then rerun"]
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    changes = []
    for k, v in want_env.items():
        if k == "CLAUDE_CODE_SUBAGENT_MODEL":
            if str(env.get(k, "")).strip().lower() != v:
                changes.append(f"env.{k}: {env.get(k) or '(unset)'} -> {v}")
                env[k] = v
        elif k not in env:
            changes.append(f"env.{k}: (unset) -> {v}")
            env[k] = v
    data["env"] = env
    if "model" not in data:
        changes.append("model: (unset) -> sonnet")
        data["model"] = "sonnet"
    if not changes:
        return True, [f"SETTINGS OK  {sp} already carries the env"]
    lines = []
    if raw is not None:
        keep = trash_dir() / time.strftime("%Y%m%d-%H%M%S")
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sp, keep / "settings.json")
        lines.append(f"previous settings.json copied to {keep}")
    sp.parent.mkdir(parents=True, exist_ok=True)
    tmp = sp.with_name(sp.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, sp)
    back = (_settings().get("env") or {}).get("CLAUDE_CODE_SUBAGENT_MODEL")
    ok = str(back or "").lower() == SUBAGENT_MODEL
    return ok, [f"SETTINGS {'MERGED' if ok else 'MERGE FAILED'}  {'; '.join(changes)}"] + lines


def doctor(install=False, cwd=None):
    """-> (code, lines). Without install: the state of this machine (templates, settings env), nothing written.
    With install: enforce() the templates, merge the settings env, read back. 0 clean, 1 something missing, 3 failed."""
    r = check(cwd)
    env = (_settings().get("env") or {}).get("CLAUDE_CODE_SUBAGENT_MODEL")
    env_ok = str(env or "").strip().lower() == SUBAGENT_MODEL
    if not install:
        lines = [f"AGENT MODELS {x['verdict']}  {x['agent']} -> {x['want']}  {x['path']}" for x in r["pins"]]
        lines.append(f"SETTINGS {'OK' if env_ok else 'MISSING'}  env.CLAUDE_CODE_SUBAGENT_MODEL={env or '(unset)'} "
                     f"(want {SUBAGENT_MODEL})")
        bad = r["verdict"] != "OK" or not env_ok
        if bad:
            lines.append("FIX: fieldkit agent-models doctor --install")
        return (1 if bad else 0), lines
    code, lines = enforce(cwd)
    ok, more = _merge_settings()
    return (0 if code == 0 and ok else 3), lines + more


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    p = argparse.ArgumentParser(prog="fieldkit agent-models",
                                description="Keep Claude Code's Explore subagent on Haiku and general-purpose on Sonnet.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Usage:")[0])
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--json", action="store_true")
    sub.add_parser("enforce")
    a = sub.add_parser("audit")
    a.add_argument("--days", type=float, default=7.0)
    a.add_argument("--json", action="store_true")
    sub.add_parser("template")
    d = sub.add_parser("doctor")
    d.add_argument("--install", action="store_true", help="write the templates and merge settings.json env")
    args = p.parse_args(argv)
    if args.cmd == "template":
        for pin in PINS:
            sys.stdout.write(f"# ~/.claude/agents/{pin['file']}\n{pin['template']}\n")
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
            for x in r["explore_off"]:
                print(f"FAIL  {x['runs']} {AGENT} run(s) on {x['model']}, not {WANT_MODEL}")
            if r["ignored"]:
                print(f"OVERRIDE IGNORED  {len(r['ignored'])} pinned run(s) after the template was written, off the pin:")
                for x in r["ignored"]:
                    print(f"  {x['agent']}  {x['model']}  {x['path']}")
            elif not r["explore_off"]:
                print(f"AUDIT OK  no pinned run off its model since the templates were written ({_pinned_summary()})")
        return 1 if r["ignored"] or r["explore_off"] else 0
    if args.cmd == "doctor":
        code, lines = doctor(install=args.install)
        print("\n".join(lines))
        return code
    r = check()
    if getattr(args, "json", False):
        print(json.dumps(r, indent=1))
    else:
        for x in r["pins"]:
            print(f"{'AGENT MODELS OK' if x['verdict'] == 'OK' else 'AGENT MODELS ' + x['verdict']}  {x['agent']} -> "
                  f"{x['want']}  {x['path']}")
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
