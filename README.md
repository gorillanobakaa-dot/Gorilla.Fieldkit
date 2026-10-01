# Fieldkit

<!-- WHO-THIS-IS-FOR: managed block, do not edit by hand -->

**Tested tools that let a small AI model on your own laptop do real work: find code, check and clean documents, run builds, and refuse to publish anything it cannot prove.**

Built for the people every other tool prices out: kids with no credit
card, 15-year-old laptops, data sold by the megabyte. Free forever, by
design, not as a trial.
Why, with the numbers: [PHILOSOPHY.md](https://github.com/gorillanobakaa-dot/Gorilla.Opencode/blob/main/PHILOSOPHY.md)

<!-- /WHO-THIS-IS-FOR -->

[![tests](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/actions/workflows/tests.yml/badge.svg)](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/actions/workflows/tests.yml)

**The model is a small part of the job. The tools around it decide whether the job gets done.**
Fieldkit is those tools: deterministic Python that does the same thing every time, checks
its own result, and answers in plain JSON. The model only has to choose the right tool.

> Measured on one ordinary laptop (Intel i7-1255U, LM Studio): the same small model, Gemma,
> got **one of five** tasks right with basic tools and **five of five** with Fieldkit, in
> about half the time and with fewer tokens. Five tasks is a small test; run it on your own
> model with `fieldkit exam`.

---

## What it does (Layman Track)

You run an AI helper on your own computer, and it is usually a small model. Left alone, a
small model guesses: it opens file after file, loses track, and sometimes makes things up.

Fieldkit gives it tools that do the hard part and hand back a plain answer:

- **"Where is this defined?"** It answers `DEFINED at file:line` or `NOT DEFINED`, so there
  is nothing to guess.
- **"Is this Word file safe to send?"** It checks the file is not broken, removes people's
  names from its hidden properties, checks again and scans it for private data. It answers
  `SAFE TO SEND` or `NOT SAFE`, with the reason.
- **"Why did the build fail?"** It names the known cause from the log and says the fix.
- **"Is this release true?"** It refuses to publish until every claim in the release notes
  has a passing proof.

When the AI wants to change your files, Fieldkit shows you a preview first, keeps a backup,
checks the result and can undo it. Anything that changes your system, or cannot be undone,
waits for **your** approval. The AI cannot approve on your behalf.

It runs on **Windows and Linux** from the same code. It needs no account, sends nothing
anywhere and costs nothing.

## Technical Definition (Developer Track)

Python 3.11+, one codebase for Windows 11 and Debian, AGPL-3.0-or-later. Every command
takes `--json`; exit codes are fixed: `0` fine, `1` error, `2` bad usage, `3` findings.

- **Agent interface.** `discover`, `describe`, `run`, `undo`, served on the command line
  and over MCP (`fieldkit mcp`, JSON-RPC 2.0 on stdio). `run` enforces one sequence: refuse
  draft cards, validate inputs, preview, require approval where the card says so, back up
  the scope, apply, verify, restore on failure. The MCP surface has no approval argument.
- **Tool cards.** Each tool declares inputs (read from argparse by AST, never by running
  it), effects, safety (`read-only`, `reversible`, `irreversible`), modes (preview, apply,
  undo, verify) and tests. Trust ladder: gathered, carded, tested (a pass recorded on the
  file's current sha256), verified.
- **Pipelines.** YAML stages with fingerprinted, resumable state and verify checks
  (`files_exist`, `file_contains`, `output_contains`, `output_lacks`, `python`). A stage
  is done only when its checks pass, never on exit code alone.
- **Release gate.** `release check` exports the tag with `git archive`, runs its tests
  there, compares published files by sha256, privacy-scans, and demands a passing proof
  for every claim in the notes. `release prove` records evidence from another machine
  (platform, git tree id, result). No `--force`.
- **Tests.** `python -m pytest`. A clean copy on the author's Windows 11 laptop: 187
  passed, 41 skipped, 1 known fault kept as a strict xfail. GitHub Actions on every push:
  Windows 183 passed, 46 skipped; Ubuntu 181 passed, 48 skipped. A skipped test names
  the tool it needs, such as a repository `fieldkit gather` has not copied in yet.

Release notes: [GitHub releases](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/releases).

---

## Table of contents

1. [Install](#1-install)
2. [Connect it to your AI helper](#2-connect-it-to-your-ai-helper)
3. [What is in the box](#3-what-is-in-the-box)
4. [How it keeps changes safe](#4-how-it-keeps-changes-safe)
5. [Your own tools stay private](#5-your-own-tools-stay-private)
6. [Measure it on your own model](#6-measure-it-on-your-own-model)
7. [The release gate](#7-the-release-gate)
8. [Honest limits](#8-honest-limits)
9. [Layout](#9-layout)

---

## 1. Install

**Step-by-step guide, for people and for developers: [INSTALL.md](INSTALL.md).** It shows how to
ask your AI to install Fieldkit or do it yourself, where to put it, how to connect it to
Gorilla OpenCode and LM Studio, and how to make a small model aware of it.

The short version (Python 3.11 or newer and Git):

```
git clone https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit
cd Gorilla.Fieldkit
python -m pip install -e ".[test]"
python -m pytest -q
fieldkit host
```

- **Pass:** the tests end with zero failed. Some say `skipped` and name a tool: those need
  `fieldkit gather` first (section 3).
- **Pass:** `fieldkit host` prints your system and Python version.
- **If `fieldkit` is not recognised:** use `python -m fieldkit` instead. It does the same.

To remove it: `python -m pip uninstall fieldkit`, then delete the folder. Fieldkit changes
nothing else on your computer.

## 2. Connect it to your AI helper

Full steps, backups and checks: [INSTALL.md](INSTALL.md), steps 4 to 6.

Fieldkit speaks MCP, the standard way AI helpers use outside tools. Your helper then sees
nine tools: `discover`, `describe`, `run`, `undo`, `next` and `readiness`, plus the three the build
harness gives a model (`build_harness_status`, `build_harness_next`, `build_harness_submit`: one
small job at a time, and the harness checks the result; approving and skipping are not among them).

**Gorilla OpenCode** - add to your `config.json` (on Windows,
`%USERPROFILE%\.config\gorilla-opencode\config.json`), then restart it:

```json
"mcpServers": {
  "fieldkit": { "type": "stdio", "command": "fieldkit", "args": ["mcp"] }
}
```

**Claude Code:**

```
claude mcp add fieldkit -- fieldkit mcp
```

**LM Studio (versions with MCP support)** - add the same `fieldkit` entry to its
`mcp.json`.

If your helper cannot find the `fieldkit` command, give the full path to it instead, for
example the `fieldkit.exe` in your Python `Scripts` folder.

- **Pass:** the first time the AI uses Fieldkit, your helper asks you to allow it.

## 3. What is in the box

| Job | Command |
|-----|---------|
| Read a Word, Excel, PowerPoint or PDF file as text | `fieldkit office read FILE` |
| Create one from a JSON or YAML description | `fieldkit office create SPEC OUT` |
| Check it is not broken | `fieldkit office check FILE` |
| Remove people's names from its hidden properties | `fieldkit office scrub FILE` |
| All of the above, safe to send? | `fieldkit office deliver FILE` |
| Find secrets, home paths, emails, your private words | `fieldkit privacy scan PATH [--git]` |
| Name the cause of a failed build | `fieldkit triage LOG` |
| Run a staged, resumable build | `fieldkit pipeline run NAME` |
| The one next thing to do in a pipeline | `fieldkit next NAME` |
| Every named file, link or module must exist | `fieldkit refcheck ...` |
| Record state, then prove what changed | `fieldkit snapshot take / diff` |
| Install, check, uninstall; list leftovers | `fieldkit lifecycle SPEC --approve` |
| Prove a release before publishing it | `fieldkit release check / prove` |
| Find a tool by describing the job | `fieldkit agent discover WORDS` |
| Index what every script in a folder does | `fieldkit harvest --find WORDS` |
| Bring in tools from GitHub, with their tests | `fieldkit gather [--test]` |
| Measure a model with and without the kit | `fieldkit exam run --model ID` |

Pipelines that ship: `office-deliver`, `firefox-windows` (drives the Gorilla Firefox build
harness) and `debian-kernel`. Triage knows Firefox on Windows, the Debian kernel and Debian
packaging failures. New failures are added as data, in `fieldkit/build/signatures/`.

`fieldkit gather` copies these repositories into `toolbox/` and records the commit and
sha256 of every file: pfind, searchfox-tools, model-eval, code-review-toolkit,
dual-track-doc-generator, debian-kernel, sensors-gorilla, speaker-loudness-fix,
black-gorilla-theme, apple-superdrive-enabler, respect-your-llms and the HDMI harness.
`fieldkit gather --test` runs each one's own tests.

## 4. How it keeps changes safe

Every tool has a card that says what it changes. When the AI asks to run one:

1. **Read-only tools** run straight away.
2. **Tools that change files** show a preview first. When applied, Fieldkit backs up the
   files, runs the tool, checks the result, and puts the backup back if the check fails.
   `undo` restores them later.
3. **Tools that change the system** (registry, services, power, hardware) **or cannot be
   undone** wait for your approval. Over MCP there is no way for the AI to give it.

The process runner records the programs it starts and stops only those, never a program
of the same name that you are running yourself.

## 5. Your own tools stay private

Put your own tools in a `local/` folder next to the code. Git ignores it, and Fieldkit
merges it in when it loads:

| File | What goes in it |
|------|-----------------|
| `local/tools.yaml` | Cards for your own scripts (same format as `fieldkit/desk/tools.yaml`) |
| `local/imports.yaml` | Your own folders for `fieldkit gather` |
| `local/tests/` | Tests for your own scripts; `pytest` runs them with the rest |
| `fieldkit.local.json` | Machine paths and your private words for the privacy scan |

Copy `fieldkit.local.example.json` to `fieldkit.local.json` to start. Your name and email go
in its `privacy.terms` list, so the scan finds them without them ever being written in code.

## 6. Measure it on your own model

`fieldkit exam` builds the same small test project every time and gives your model five
tasks, once with basic tools and once with Fieldkit. It talks to an OpenAI-compatible
server on your own computer (LM Studio's address by default).

```
fieldkit exam run --model <model id as your server lists it>
fieldkit exam report
```

The result on the author's laptop:

| Model | Basic tools | With Fieldkit |
|-------|-------------|---------------|
| Gemma | 1 of 5, 762 s, 8,349 tokens | 5 of 5, 410 s, 6,630 tokens |
| Qwen3 30B-A3B | 3 of 5, 1,415 s, 60,379 tokens | 5 of 5, 1,030 s, 34,455 tokens |

Five tasks on one test project, written by the same author as the tools. It shows the
effect; it does not prove it for every job.

## 7. The release gate

A release spec in `releases/` lists the tag, its tests, the files that must match what was
tested, and the claims the release notes may make, each with a proof.

```
fieldkit release check releases/<name>.yaml    CLEAR, or DO NOT PUBLISH with every reason
fieldkit release prove releases/<name>.yaml    run the tag's tests on THIS machine; record evidence
```

A claim such as "runs on Windows and Linux" passes only with a passing `prove` from each
platform for the same code. There is no `--force`: a failing gate means you fix the
release or fix the check.

## 8. Honest limits

- The Debian kernel pipeline has run as a dry run only; its full build has not run yet.
- The `firefox-windows` pipeline needs the Gorilla Firefox build harness.
- The privacy scan finds patterns and the words you list. It cannot know a secret it has
  no pattern for.
- Fieldkit reads commands and files. It is **not a sandbox**.
- Office text extraction handles ordinary documents; for complex layouts, install the
  optional `[readers]` extra (markitdown, docling).
- Known faults are kept as tests marked "expected to fail", so a fix is noticed.

## 9. Layout

```
fieldkit/core     host, settings, process runner, privacy, pipeline engine, next, snapshot
fieldkit/office   read, create, check, scrub, deliver
fieldkit/build    triage + signatures, kernel, refcheck, lifecycle, pipelines
fieldkit/desk     registry (tools.yaml), cards, discover, readiness
fieldkit/exam     fixture, tasks and graders, toolsets, runner
fieldkit/agent.py, mcp.py, release.py, gather.py, harvest.py
skills/           short SKILL.md pointers; install_skills.py puts them where agents look
tests/            python -m pytest
AGENTS.md         instructions for AI agents working on this code
```

Licence: [AGPL-3.0-or-later](LICENSE). If you run a changed version as a service, its
source must stay open too.
