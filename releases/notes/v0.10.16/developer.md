# v0.10.16, for developers

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

## Scope

`v0.10.15..main`: PRs 713, 714.

- **PR 713, Session Mode enforcement for Antigravity spawn tools.** Added `AGY_SPAWN_TOOLS` (`invoke_subagent`, `run_workflow`, `swarm`), `requested_spawns()` (counting `swarm` `action=add` `tasks` lists and `n` worker counts while ignoring read-only status checks), and `antigravity_spawn_refusal()` in `fieldkit/lifecycle/sessionmode.py`, wired into `fieldkit/autopilot/toolrouter.py` (`verdict()`). Covered by `tests/test_session_mode.py`.
- **PR 714, portable Antigravity global hooks, 150k context gate, and P39 multi-agent session-launch hook.**
  - Added `fieldkit/antigravity/hooks.py` (`GUARD_VERSION = "1.7.0"`): `doctor()`, `apply()`, `render_guard_py()`, `context_tokens()`, `context_warning()`, `pre_invocation()`, and `midturn_context_gate()`. Merges `owner-guard` (`PreToolUse` and `PreInvocation`) into global `hooks.json` while preserving user hooks and backing up prior files, resolves Fieldkit paths dynamically at runtime with zero personal paths, updates `--selftest` (17 built-in checks), and emits `/compact` or new-session rotation warnings at `>= 150,000` context tokens (resetting automatically after `/compact` and exempting handover/sitrep commands).
  - Wired `check_antigravity_hooks()` and `hooks.apply()` into `fieldkit/rules.py` (`check()` and `install_all()`), recognizing `gemini`, `agy`, and `antigravity` executables on `PATH`.
  - Added `fieldkit/lifecycle/session_choice.py` and `--session-agent` / `--session-provider` / `--session-model` / `--session-thinking` in `fieldkit/lifecycle/routing.py`, enforced in `toolrouter.verdict()` and `guard.py` so interactive `peers link ask` and `task start` calls require explicit owner agent/provider/model selection rather than silently defaulting to Claude.
  - Updated `fieldkit/lifecycle/models.yaml`, `models.py` (`cheap_exact`), `choice.yaml`, `choice.py` (`None` runner guard in `command_models()`), and `rules/rules.yaml` / `rules/CORE.md` so Anthropic Claude (`haiku`, `sonnet`) and worker models for Antigravity, `agy`, Gemini CLI, and OpenAI Codex appear side-by-side in `fieldkit models menu` while `opus`, `fable`, `gemini-4-neon`, `gemini-4-hydrogen`, `gpt-5.6-terra`, and unknown models stay gated by the spend guard. Covered by `tests/test_antigravity_hooks_and_choice.py`.

### Verification

- CI green on Linux and Windows across 100 check runs on PR #714 (`a2f80e35f2`) and PR #713 (`350b20a693`).
- Static gates passed locally before tagging: `fieldkit rules lint`, `fieldkit rules check --repo-only`, `fieldkit models menu --check`, `fieldkit integration check --changed`, `fieldkit release-page compose` + `check`, `fieldkit ship release-ready v0.10.16`, and `fieldkit privacy scan . --git`.
- Not verified: live network PyPI install on an unconfigured third-party workstation (covered offline by unit and E2E tests in CI).
