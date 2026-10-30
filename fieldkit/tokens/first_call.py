"""cost firstcall: what a Claude Code session's FIRST call carries, in tokens, measured on this machine by a real
headless call, and split by part. Every later call re-sends this base, so it is paid on every call (O1).

usage:
    fieldkit cost firstcall                       one headless call in this folder: total tokens, tools, skills, MCP
    fieldkit cost firstcall --cwd DIR             the same in another project's folder
    fieldkit cost firstcall --parts               four calls: full, no MCP, no skills, no tools; the rules files by
                                                  their size; the rest is the system prompt (with hook text)
    fieldkit cost firstcall --json                one JSON object, nothing else on stdout

Each call is `claude -p "Reply with the single word OK." --output-format stream-json --verbose --max-turns 1
--no-session-persistence` (no transcript is left behind). The first call's tokens are input + cache creation + cache
read of its usage, the number the API bills. Parts by switching one thing off: --strict-mcp-config (MCP servers),
--disable-slash-commands (skills), --tools "" (built-in tools). The rules files (CLAUDE.md, AGENTS.md, the memory
index) are estimated at 4 bytes per token and marked est. The parts overlap (switching skills off also
drops the Skill tool's text, switching tools off drops tool-specific prompt text), so the rest can be negative. Each call costs money (about $0.25 on Opus at 30k
tokens): this tool is run by hand to measure a change, never in a loop.
O1 of docs/HARNESS-PLAN.md, 2026-10-28.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROMPT = "Reply with the single word OK."
VARIANTS = {"full": [], "no-mcp": ["--strict-mcp-config"], "no-skills": ["--disable-slash-commands"],
            "no-tools": ["--tools", ""]}
BYTES_PER_TOKEN = 4


def command(extra=(), model=None):
    cmd = ["claude", "-p", PROMPT, "--output-format", "stream-json", "--verbose", "--max-turns", "1",
           "--no-session-persistence"]
    if model:
        cmd += ["--model", model]
    return cmd + list(extra)


def parse(lines):
    """stream-json lines -> {tokens, tools, skills, mcp, model, usd}; tokens None when no call was made."""
    out = {"tokens": None, "tools": [], "skills": [], "mcp": [], "model": "", "usd": None}
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "system" and d.get("subtype") == "init":
            out.update(tools=d.get("tools") or [], skills=d.get("skills") or [], model=d.get("model") or "",
                       mcp=[m.get("name", "") for m in d.get("mcp_servers") or []])
        elif d.get("type") == "assistant" and out["tokens"] is None:
            u = (d.get("message") or {}).get("usage") or {}
            if u:
                out["tokens"] = sum(int(u.get(k) or 0) for k in
                                    ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        elif d.get("type") == "result":
            out["usd"] = d.get("total_cost_usd")
    return out


def run(cwd, extra=(), model=None, runner=subprocess.run):
    r = runner(command(extra, model), cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=600)
    out = parse((r.stdout or "").splitlines())
    if out["tokens"] is None:
        out["error"] = ((r.stderr or "") + (r.stdout or "")).strip()[-300:] or f"exit {r.returncode}"
    return out


def project_slug(cwd):
    """Claude Code's projects folder name for cwd: every character that is not a letter or digit becomes -."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def rules_files(cwd, home=None):
    """The rules files Claude Code reads for cwd: the global CLAUDE.md, CLAUDE.md/AGENTS.md from cwd up to the
    filesystem root, and the project's memory index."""
    home = Path(home or Path.home())
    out = [home / ".claude" / "CLAUDE.md"]
    d = Path(cwd).resolve()
    for p in [d, *d.parents]:
        out += [p / "CLAUDE.md", p / "AGENTS.md", p / ".claude" / "CLAUDE.md"]
    out.append(home / ".claude" / "projects" / project_slug(cwd) / "memory" / "MEMORY.md")
    seen, found = set(), []
    for f in out:
        if f.is_file() and f.resolve() not in seen:
            seen.add(f.resolve())
            found.append(f)
    return found


def measure(cwd, parts=False, model=None, runner=subprocess.run, home=None):
    res = {"cwd": str(Path(cwd).resolve()), "calls": {}}
    for name, extra in VARIANTS.items():
        if name != "full" and not parts:
            break
        res["calls"][name] = run(cwd, extra, model, runner)
    full = res["calls"]["full"]
    res.update(tokens=full["tokens"], model=full["model"], tools=len(full["tools"]), skills=len(full["skills"]),
               mcp=full["mcp"])
    if parts and all(c["tokens"] is not None for c in res["calls"].values()):
        t = full["tokens"]
        rules = [{"file": str(f), "bytes": f.stat().st_size} for f in rules_files(cwd, home)]
        est = sum(r["bytes"] for r in rules) // BYTES_PER_TOKEN
        p = {"mcp": t - res["calls"]["no-mcp"]["tokens"], "skills": t - res["calls"]["no-skills"]["tokens"],
             "tools": t - res["calls"]["no-tools"]["tokens"], "rules (est)": est}
        p["rest (system, hooks; parts overlap)"] = t - sum(p.values())
        res["parts"], res["rules_files"] = p, rules
    return res


def render(r):
    if r["tokens"] is None:
        return f"firstcall: no call made in {r['cwd']}: {r['calls']['full'].get('error', '')}\n" \
               "NEXT: check that `claude -p` works here (signed in, not rate-limited)"
    usd = sum(c.get("usd") or 0 for c in r["calls"].values())
    lines = [f"first call {r['tokens']:,} tokens in {r['cwd']} ({r['model']}; {r['tools']} tools, {r['skills']} skills, "
             f"MCP: {', '.join(r['mcp']) or 'none'})"]
    for k, v in (r.get("parts") or {}).items():
        lines.append(f"  {k:<36} {v:>7,}")
    lines.append(f"measured with {len(r['calls'])} call(s), ${usd:.2f}")
    lines.append("NEXT: change one thing, measure again, compare" if r.get("parts") else
                 "NEXT: fieldkit cost firstcall --parts (four calls) to see where the tokens go")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(prog="fieldkit cost firstcall", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cwd", default=os.getcwd(), help="the project folder to measure in (default: here)")
    p.add_argument("--parts", action="store_true", help="four calls: the split by MCP, skills, tools")
    p.add_argument("--model", help="the model (default: what a session here would use)")
    p.add_argument("--json", action="store_true", help="one JSON object, nothing else on stdout")
    a = p.parse_args(argv)
    if not os.path.isdir(a.cwd):
        print(f"firstcall: not a folder: {a.cwd}", file=sys.stderr)
        return 2
    r = measure(a.cwd, a.parts, a.model)
    print(json.dumps(r) if a.json else render(r))
    return 0 if r["tokens"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
