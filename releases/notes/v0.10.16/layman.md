# v0.10.16, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **Automatic Antigravity hook setup on any computer:** running `fieldkit rules install` (or passing `-Rules` to `install.ps1` / `--rules` to `install.sh`) now installs and verifies Antigravity's global `hooks.json` and `guard.py` (`v1.7.0`) automatically whenever Antigravity, `agy`, or Gemini is present. Pre-existing user hooks in `hooks.json` are preserved and backed up before any update, and zero machine-specific paths are hardcoded.
- **Mechanical Session Mode limits in Antigravity:** Session Mode caps (`1` Economy with zero sub-agents, `2` Time-Sensitive up to 6, `3` Fast up to 1000, plus Single Player `4a-4c` and Multiplayer `5a-5c`) now block `invoke_subagent`, `run_workflow`, and `swarm` (`action=add`, counting requested worker batches) directly inside the hook, failing closed to Economy when no mode file exists.
- **150k-token context warning (`/compact` or new session):** when an Antigravity conversation reaches 150,000 context tokens (`ROTATE`), `PreInvocation` displays an inline warning banner and `PreToolUse` prompts at crossing without blocking handover commands, clearly explaining that you should either run `/compact` to compress the conversation or run `fieldkit rotate handover` and open a new session. Once `/compact` lowers context below 150k, the warning resets automatically.
- **Multi-agent choice hook instead of silent Claude defaults:** Anthropic Claude models (`haiku`, `sonnet`, `opus`, `fable`) remain available alongside everyday worker models for Antigravity IDE, `agy`, Gemini CLI, OpenAI Codex, and Gorilla OpenCode in `fieldkit models menu` (while flagship and reviewer models remain gated by the spend guard). Starting a new coding session via `fieldkit peers link ask` or `fieldkit task start` from inside an interactive AI session is now blocked by the hook until the agent asks you which coding agent, provider, and model you want and records your answer via `fieldkit route choose --session-agent ...`.

### What did not change

No tool or existing safety rule was weakened or removed. Headless autopilot runs continue to use your recorded choices (`fieldkit models remember`), and default runs of `install.ps1` and `install.sh` without `-Rules` / `--rules` still modify nothing outside the target Fieldkit directory.

### What was not checked

Automated tests ran on Linux and Windows in GitHub CI, including running `guard.py --selftest` in an isolated home directory. A live end-to-end download from PyPI onto a brand-new physical workstation was not run during this release.
