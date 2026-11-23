## What this is

Fieldkit v0.10.13 adds `fieldkit escalate`: it runs a job as a cheap, low-effort AI session, checks the result with a plain pass/fail signal, and only on a failure starts a fresh, stronger session. It also adds a check that fails when the one-hour prompt cache is not switched on.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux and run AI jobs. It works the same on both systems.

## Why this matters

Anthropic's cost guide reports that low effort with only the failures re-run at high effort solved about 97% of tasks at about $0.17 each, against 95.3% at $0.29 when everything runs at high effort. When the first try passes, no extra model call is made.

v0.10.13 also carries everything v0.10.11 and v0.10.12 were meant to ship: neither tag was published (v0.10.11 failed the full test suite; v0.10.12 passed but its release candidate job was skipped by a workflow bug, fixed here).

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->



---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.10.9..main`. The new part is PR 689; the rest is the v0.10.11 scope (Gorilla Trash menu, fail-closed ps-compat-check, CI cost work, `fkproc` hidden consoles) that never got a GitHub release.

- **`fieldkit/escalate/`.** `run.py` is the ladder: RUNGS low, high, then xhigh on Opus. Each rung is a new headless session through `autopilot.run.claude(..., effort=..., resume=None)`, never a resume. The signal is `check_argv` (a test command, a verifier command, or `ship ci-wait PR`), run through `fkproc`. A red adds the last `TAIL_LINES` lines to the original brief for the next rung. `models.check_model` gates xhigh (TWO STRIKES). `--skip-if-green` makes zero model calls. Each attempt is a line in `state_dir()/escalate.jsonl` (effort, model, seconds, result, tokens, cost).
- **`cachecheck.py` and `fieldkit escalate cache`, `fieldkit integration cache`.** Exit 3 unless `promptCacheTtl` and `subagentPromptCacheTtl` are `"1h"` and `CLAUDE_CODE_PROMPT_CACHE_TTL`, `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, `ENABLE_PROMPT_CACHING_1H` are set. Child sessions inherit the env vars.
- **Integration.** Card in `tools.yaml` with e2e jobs on both OSes (`.github/e2e/escalate.py`), METHODS row 58, skill section, 5 mutations (`tests/escalate_mutations.yaml`), `tests/test_escalate.py`.
- **Windows fix found by the e2e.** `shlex.split(posix=False)` keeps the quotes, so a quoted path made the verifier fail with `FileNotFoundError`; the quotes are now stripped.

#### Verification

- CI green on PR 689 (Linux and Windows, both e2e jobs).
- Not verified: the cost figures with real sessions; the xhigh rung against a live spend guard.
