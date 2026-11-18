# v0.10.0, for developers

## Scope

`v0.9.4..origin/main`: 242 non-merge commits in 166 merged pull requests (`#423` to `#597`, `2026-11-10` to `2026-11-18`), plan rows P9, P18, P26-P29, P34-P41a and ideas I-04 to I-30.

## The main pieces

- `fieldkit/emerge/`: content-addressed stages, offload to the other machine, fix judge (a commit on a detached build tree, a side worktree or another repository is found and cherry-picked), `emerge repair` (one front-end file straight into the installed omni archive, checked on the recipe's pages, put back on FAIL), recipes `firefox-windows` (patchset stage replays the public set onto a pristine tag clone), `firefox-debian` (deps and fonts stages), `gorilla-opencode`, `gorilla-size`. Never pinned: `latest:` reads the version.
- `fieldkit/firefox/` and `docs/FIREFOX-COOKBOOK.md`: one procedure for three systems (`fieldkit firefox status|next|release|handover`), naming as data (`cookbook.yaml naming`, including the batch release `v{version}` / `{name} {version}`).
- `fieldkit/method/releasegate.py` & `fieldkit/buildh/releasepublish.py`: one release per operating system per window (`system_of(tag)` reads the tag suffix, aliases in `releasegate.yaml`); `--hotfix WHY` logged, one per system per window; a drift test proves every publisher calls the gate; Windows releases require an installer artifact and `installer-test` check row (`#590`, `#594`).
- `fieldkit/lifecycle/`: `choice.yaml` (agent > provider > model > reasoning, measured live), quota guard (`classify_failure` matches error phrases only; a run ending `exit 0` never blocks), `counsel.py` (advise, review, tie-break, climb in one module), keeper (`keeper tick` moves work on with no model), strikes redesign (3 rounds per model, unproven never counted, escalation before park).
- `fieldkit/core/`: `childrun` (every patch/git child gets a deadline and a stall stop), `contain` (process tree tracking; the Windows walk no longer asks psutil for status: 2.1 s to 0.02 s per walk), `concepts` (one definition of product name, default branch, platform groups), `prepare`.
- Cross-OS & E2E: `fieldkit parity os`, `crossos` dispatch/offload/not-this-platform, Linux readers for the leak gate, installer test (.deb round trip), bh-capture, night-pack (systemd timers); `leakgate-ci` on a GitHub ubuntu runner; `e2e.yml` and `e2e-l15.yml` end-to-end tool batches on Linux and Windows (`#587`–`#589`); `fieldkit/desk/os_parity.yaml` updated for `_win_known_folder` in `fieldkit/peers/link.py`.
- CI and shipping: `ship batch` (many PRs, one CI, one merge), CI sharded per OS, `ci-wait` re-runs infrastructure reds with backoff (never a strike), `ship ci-local` when GitHub's runner is down, the tool router refuses a test runner typed in a shell (TESTS ONCE), permanence check at turn end.
- Net & Antigravity bridge: `relay listen` per-session ear, ear supervisor WAKE, OpenCode mirror, Rust bridge in `rust/agy-tools`, bidirectional cable bridge (`fieldkit/peers/agentfind.py` separating `agy` CLI from Electron GUI, `fieldkit/peers/link_probe.py` `antigravity-sessions` probe, signed `antigravity-send` cable verb, `fieldkit/antigravity/bridge.py` `sessions/send --device` and `listen`, `fieldkit/autopilot/room.py` Antigravity `UserPromptSubmit`/`PreToolUse` normalization & `.agents/hooks.json` helpers, `fieldkit/zero/channels.py` active channels, `fieldkit/autopilot/run.py` `--print=<prompt>` for `agy`, `fieldkit/peers/link.py` & `link_transfer.py` `--to-desktop`/`--to-downloads`/`--to-documents` with `accept_to_*` approvals).
- Capture: per-project capture and distillation, age-based capture cleanup & scoped Codex/Claude lesson lookback (`fieldkit/capture/{cleanslate,lesson,retention,agy,codex_scan}.py`), adopt gate (`adopt_rules.yaml`: placeholders, mixed toolchains, never-replayed candidates refused).

## Not verified

- The release is cut from `main` after its CI is green on Linux and Windows; open WIP PRs `#496`, `#598`, and `#599` are excluded from `v0.10.0`.
- Parked I-30 items remain open: emerge keys ignore commits in a port tree, a ported tree never receives port fixes made on the other machine, the cookbook's `v{version}-{os}` tag form is refused by the tag guard, and `release-page compose` `_body()` strips lines from a `# ` title up to the first `## ` heading while `release-page check` still passes.
