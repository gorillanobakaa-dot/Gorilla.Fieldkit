# Version: 1.0.0 · updated 26-11-22-18-10
"""The hard lines in front of every shell command of Gorilla OpenCode and Kiro (FIX-PLAN F0.4, finding 3.1).

Claude Code has claude_hook.py. The other agents had no door: a Qwen run in Gorilla OpenCode created a release, moved
a tag and forced a push because nothing called `hardlines`. This is the same decision, from the same file, for any
agent that can run a command before a shell tool: it reads the call (Kiro / Claude-style JSON on stdin, or
`--command CMD`), asks hardlines.decide, and exits 2 with the reason on stderr for a deny (the code Kiro's preToolUse
hook and an OpenCode plugin both treat as "block"). Anything else exits 0. A crash denies (fail closed).

  python -m fieldkit.autopilot.shell_hook [--command CMD]        the hook itself
  python -m fieldkit.autopilot.shell_hook --print kiro|opencode  the config that installs it
"""
import json
import sys

SHELL_TOOLS = {"bash", "execute_bash", "shell", "run_command", "powershell", "execute_powershell", "terminal"}


def verdict(command, cwd=None, tool="Bash"):
    from fieldkit.autopilot import hardlines
    return hardlines.decide(tool, {"command": command}, cwd=cwd)


def config(agent, python="python"):
    """The hook configuration for `agent` ('kiro' or 'opencode'), as text to put in its config."""
    cmd = f"{python} -m fieldkit.autopilot.shell_hook"
    if agent == "kiro":     # .kiro/agents/<name>.json "hooks"
        return json.dumps({"hooks": {"preToolUse": [{"matcher": "execute_bash", "command": cmd}]}}, indent=2)
    if agent == "opencode":  # .opencode/plugin/hardlines.js
        return ("export const Hardlines = async ({ $ }) => ({\n"
                "  'tool.execute.before': async (input, output) => {\n"
                "    if (input.tool !== 'bash') return\n"
                f"    const r = await $`{cmd} --command ${{output.args.command}}`.nothrow().quiet()\n"
                "    if (r.exitCode !== 0) throw new Error(r.stderr.toString().trim() || 'hard line')\n"
                "  },\n})\n")
    raise ValueError(f"unknown agent {agent!r}")


def main(argv=None, stdin=sys.stdin, stderr=sys.stderr):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--print" in argv:
        sys.stdout.write(config(argv[argv.index("--print") + 1]) + "\n")
        return 0
    try:
        cwd = None
        if "--command" in argv:
            command = " ".join(argv[argv.index("--command") + 1:])
        else:
            payload = json.load(stdin)
            if str(payload.get("tool_name") or "bash").lower() not in SHELL_TOOLS:
                return 0
            ti = payload.get("tool_input") or {}
            command, cwd = ti.get("command") or ti.get("cmd") or "", payload.get("cwd")
        decision, why = verdict(command, cwd)
    except Exception as e:  # noqa: BLE001 - a broken hook must not open the door
        decision, why = "deny", f"hard-line hook error ({e}); denied to be safe"
    if decision == "deny":
        stderr.write(why + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
