# Antigravity bridge

## In plain words
Fieldkit can now list, message and follow Antigravity coding sessions on this computer, and an Antigravity session can
join Zero's net as a station, like a Claude session. Whatever an Antigravity session says comes back wrapped as
"untrusted data": it is read, never obeyed. The bridge opens no new port.

## For developers
- `fieldkit antigravity sessions [--source all|desktop|cli] [--device DEV]`: names of `~/.gemini/antigravity{,-cli}/conversations/*.db` (databases are never opened). `--device` asks the other computer through the link's fixed `sessions` probe.
- `send --prompt T [--conversation ID|--continue]`: runs `agy --print --output-format json`; stdout comes back inside `<untrusted-data source="antigravity:ID">`, a closing tag inside is defused.
- `stream`: `agy --print` stream-json; each line out is `{"untrusted": true, "source": ..., "line": ...}`.
- `station NAME`: `fieldkit zero checkin --as antigravity:NAME`. Roll call, orders and reports use the same net verbs as for `claude:` and `opencode:` stations (Antigravity pulls: its GEMINI.md rule runs `fieldkit net checkin`).
- Security: nothing listens. The cable relay (47077) is the existing link listener with Fieldkit's pairing key and HMAC; the crate's own key file and :47078 orchestrator are not used. `check_bind` (and `fieldkit antigravity bind-check ADDR`) allows loopback and 10.77.0.0/24 only; tests prove 0.0.0.0, ::, LAN and public addresses are refused.
- Rust: the verbs `sessions`, `send`, `stream`, `bind-check` run in `rust/agy-tools/src/bridge.rs` (std only, about 5 ms start, waits for agy so no zombie; unit tests in `cargo test`, built by `fieldkit emerge run agy-tools`). Python (`bridge.py`) is the fallback when the binary is not built, and owns `station`.
- Not ported: `orchestrator.rs` (2551 lines, own listener, own HMAC). Follow-up brief `state/briefs/antigravity-orchestrator-port.md` (not in git).

## Gorilla.Opencode
Opencode already has stations: `fieldkit zero send --as opencode:NAME --to antigravity:NAME@host "TEXT"` rides the net
log and the cable; the Antigravity side reads it with `fieldkit net checkin` (pull). Direct push to an Antigravity
session needs `agy` on the Opencode machine: Opencode runs `fieldkit antigravity send` as a tool call. No Opencode code
change is needed for that; a native `/antigravity` command is the follow-up in the brief above.
