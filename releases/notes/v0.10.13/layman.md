# v0.10.13, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **A cheaper way to run AI jobs:** `fieldkit escalate run BRIEF.md` starts a new AI session at the lowest effort and then checks the work with something that cannot be argued with: a test command, a verifier command, or the result of the GitHub checks. If the check passes, you are done and nothing more is spent. If it fails, a brand new session starts at a higher effort. It receives the original job plus a few lines of what went wrong. Only if that also fails, and only if the spending guard allows it, a third session runs on the strongest model.
- **A record of every try:** each attempt is written to a ledger with its effort, model, time, pass or fail, and tokens, so you can see what the savings really were. `fieldkit escalate ledger` shows it.
- **A check that the one-hour cache is on:** the AI keeps what it has already read in a cache. By default that cache expires after five minutes, which makes long jobs cost more. `fieldkit escalate cache` fails when your Claude settings do not ask for one hour. Every session started by this tool also gets the one-hour setting.
- **Everything from v0.10.11:** the Gorilla Trash right-click menu on Windows 11, cheaper and faster automatic tests, no flashing console windows, and a pre-commit check that can no longer fail silently.

### What did not change

No tool was removed. Existing commands keep their behavior.

### What was not checked

The savings figures come from Anthropic's published guide, not from our own jobs yet; the ledger will show ours. No real AI session was started by the automated tests; they use a stand-in.
