# v0.10.1, for developers

## Capture retention

`fieldkit capture clean --keep-last 3d --json` previews retention. Add `--apply --retire-sessions` to recycle eligible old transcripts. A clean-slate lesson start can retire eligible transcripts before recording. Session retirement checks the age, closure, and archive integrity of supported standalone Claude Code, Codex, Gemini CLI JSON, and Kiro V3 transcripts before a reversible move. Shared databases and unsupported formats, including Kiro Crew and Antigravity transcript stores, remain in inventory.

Fresh lessons use live hooks or a selected session boundary. Explicit lookback uses the selected session and project workspace. That boundary keeps unrelated history out of the lesson.

## Zero native inbox hooks

`fieldkit zero hooks codex`, `fieldkit zero hooks gemini`, and `fieldkit zero hooks antigravity` report configuration and live check-ins. Add `--apply` to install the named adapter alongside existing hooks. Use Codex's native trust review when requested.

Each adapter translates its hook envelope into an existing Zero station check-in. Messages arrive through the existing local inbox and paired cable. Each bounded hook response leaves the remaining messages queued for a later check.
