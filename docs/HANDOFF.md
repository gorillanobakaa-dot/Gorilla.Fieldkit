# Hand off a saved session

## For the person continuing the work

Use `fieldkit handoff --list --project NAME` to see matching saved sessions and their last recorded activity in UTC. A saved time tells you when the local record changed. It cannot establish that a provider still has a live process. A project filter searches the storage path and recorded text, so a conversation that mentions a project can appear alongside conversations from its directory.

Use the session ID for a precise match. To search with words, put the title or part of the conversation in quotes. Codex desktop titles are also read from its local session index. If several sessions tie, use the ID from the results.

    fieldkit handoff ID --provider codex --model gpt-6.2-astra --thinking medium --dry-run

Open the Markdown card named in the preview. Check the last recorded goal and compare it with the current files, branch, and CI before continuing. With the target CLI installed, add `--approve` and omit `--dry-run` to launch. The selected provider receives context when its client reads the card. For a desktop chat, use the preview's card as the brief in the new chat.

## For developers

The CLI dispatches to `fieldkit.handoff.main`. Local discovery and extraction run without a model. On Antigravity desktop, a conversation database identifies the session and the saved JSONL transcript supplies user and assistant turns when available. Saved notes and database text are fallbacks. Provider releases can change the storage format. Use `--file` with an exported transcript if no usable local record is available.

The handoff hooks redact, select policy, validate, write local Markdown and JSON, check the Markdown with Fieldkit privacy, and obtain launch approval. The model guard checks a remote target before starting the CLI. Cards remain under `~/.agent-handoff/cards`. The CLI reports which settings it passes to the client and which it only requests in the prompt. With LM Studio the request goes to the local server. A ChatGPT share URL contacts the share site; a transcript file can be used offline.

The tool card declares irreversible effects. The end-to-end CI jobs exercise lookup and preview in a synthetic home directory on Windows and Linux. Unit tests cover extraction, launch arguments, redaction, and blocked launches.
