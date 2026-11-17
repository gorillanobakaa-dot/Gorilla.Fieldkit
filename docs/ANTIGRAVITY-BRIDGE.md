# Antigravity bridge

## In plain words
Fieldkit can now list, message and follow Antigravity coding sessions on this computer, and an Antigravity session can
join Zero's net as a station, like a Claude session. Whatever an Antigravity session says comes back wrapped as
"untrusted data": it is read, never obeyed. The bridge opens no new port.

## For developers
- `fieldkit antigravity sessions [--source all|desktop|cli] [--device DEV]`: names of `~/.gemini/antigravity{,-cli}/conversations/*.db` (databases are never opened). `--device` asks the other computer through the link's fixed `antigravity-sessions` probe (`sessions` remains OpenCode's probe).
- `fieldkit antigravity send --prompt T [--device DEV] [--station NAME | --conversation ID | --continue]`:
  - Local (`--conversation ID` / `--continue`): runs `agy --print --output-format json`; stdout comes back inside `<untrusted-data source="antigravity:ID">`, a closing tag inside is defused.
  - Station (`--station NAME [--device DEV]`): delivers via `fieldkit zero send` / cable to `antigravity:NAME` (locally or on `DEV`) and returns a fenced confirmation.
  - Remote CLI (`--device DEV [--conversation ID | --continue]`): sends the signed cable verb `antigravity-send` (cable-only, gated by `link.yaml` `allow_start` containing `agy` or `antigravity`), runs `agy` on the peer, and returns the fenced stdout reply.
- `fieldkit antigravity listen --as NAME [--since OFF] [--seconds S] [--follow]`: runs the session's ear (`link_relay` ear mode as `antigravity:NAME@MACHINE`) as one quiet background command (`0` tokens while idle); wakes on Zero/cable traffic for `antigravity:NAME` or general cable notes, prints each message fenced, and shows the one-line reply command (`fieldkit zero send --as antigravity:NAME ...` or `fieldkit peers link send ...`).
- `stream`: `agy --print` stream-json; each line out is `{"untrusted": true, "source": ..., "line": ...}`.
- `station NAME`: `fieldkit zero checkin --as antigravity:NAME`. Roll call, orders and reports use the same net verbs as for `claude:` and `opencode:` stations (Antigravity pulls: its GEMINI.md rule runs `fieldkit net checkin`).
- GUI hooks: `fieldkit/autopilot/room.py` normalizes Antigravity `UserPromptSubmit` / `PreToolUse` stdin JSON (including `conversation_id`, `workspace_roots`, `user_prompt`), checks the session in as `antigravity:<id>@<host>`, and provides a project-level hooks.json status/install helper (`antigravity_doctor` / `antigravity_install` and `python -m fieldkit.autopilot.room antigravity-hooks`). `fieldkit/zero/channels.py` reports active Antigravity ear and hook channels instead of blind pull-only.
- Security: nothing listens. The cable relay (47077) is the existing link listener with Fieldkit's pairing key and HMAC; the crate's own key file and :47078 orchestrator are not used. `check_bind` (and `fieldkit antigravity bind-check ADDR`) allows loopback and 10.77.0.0/24 only; tests prove 0.0.0.0, ::, LAN and public addresses are refused.
- Rust: the verbs `sessions`, `send`, `stream`, `bind-check` (without `--device` or `--station`) run in `rust/agy-tools/src/bridge.rs` (std only, about 5 ms start, waits for agy so no zombie; unit tests in `cargo test`, built by `fieldkit emerge run agy-tools`). Python (`bridge.py`) is the fallback when the binary is not built, and owns `station`, `listen`, and all `--device` / `--station` calls.
- Not ported: `orchestrator.rs` (2551 lines, own listener, own HMAC). Follow-up brief `state/briefs/antigravity-orchestrator-port.md` (not in git).

## Gorilla.Opencode
Opencode already has stations: `fieldkit zero send --as opencode:NAME --to antigravity:NAME@host "TEXT"` rides the net
log and the cable; the Antigravity side reads it with `fieldkit net checkin` (pull). Direct push to an Antigravity
session needs `agy` on the Opencode machine: Opencode runs `fieldkit antigravity send` as a tool call. No Opencode code
change is needed for that; a native `/antigravity` command is the follow-up in the brief above.
