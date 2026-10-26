---
name: fieldkit-skills
description: >-
  Agent Skills (the SKILL.md folder standard at agentskills.io, now at the Agentic AI Foundation) checked, aligned and
  installed the same way on every machine with fieldkit skills, plus the commands behind the adopted Anthropic skills -
  fieldkit webtest (start local servers, run a Playwright or other test, stop them), fieldkit prefix-diff (what broke
  the prompt-cache prefix between two API requests, offline), fieldkit mcp-eval (evaluate an MCP server, opt-in) and
  fieldkit skills package, benchmark and eval (skill-creator). Use when writing, editing, reviewing or installing a
  skill, when a skill does not show up in Claude Code, Codex, Gemini or another agent, before committing a SKILL.md,
  when adopting a third-party skill, when testing a local web app, or when asked why prompt caching misses.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS. Works offline; no network access unless a step says so."
---

# Agent Skills in Fieldkit (fieldkit skills)

Every Fieldkit skill is an Agent Skills folder: `SKILL.md` with YAML frontmatter (`name` = folder name, lowercase
letters, digits and single hyphens, at most 64; `description` at most 1024; optional `license`, `compatibility` at most
500, `metadata` string to string, `allowed-tools`; nothing else), body under 500 lines, detail in `references/`,
`scripts/`, `assets/`. Over 70 agent platforms read this format, so one folder serves them all.

## Check a skill (do this before every commit that touches one)

```
fieldkit skills validate                      # skills/, local/skills/ (strict), third_party/anthropic-skills/ + doors
fieldkit skills validate path/to/my-skill --strict
fieldkit skills validate --installed          # also: are the installed copies current on this machine?
```

The pre-commit hook runs it by itself, but only when the commit changes a skill, the adopted tree, `install_skills.py`
or `fieldkit/skills/`. Typical errors and their fix:

- `[yaml] ... mapping values are not allowed here`: the description holds `: `. Write it as a folded block:
  `description: >-` and the text indented on the next lines.
- `[description] ... (max 1024)`: cut words; move detail into the body.
- `[name-folder]`: the folder and `name` must be the same string.
- `[description-brackets]`: no `<` or `>` in a description (Anthropic's own validator refuses them).

New Fieldkit skill: write `name` and `description`, then `fieldkit skills align --apply` adds `license` and
`compatibility` (the platforms come from the tool cards whose `skill:` points at it).

## Install on this machine and the others

```
python install_skills.py            # ~/.claude/skills, and ~/.agents/skills, ~/.gemini/skills where they exist
fieldkit skills installed           # missing, outdated, foreign (a name taken by someone else's folder), orphan
fieldkit sync --apply               # on the Linux VAIO / the Air: take verified commits, then install the skills
```

`fieldkit sync` shows an `agent skills` row; `STALE` means run it with `--apply`. Only `fieldkit-*`/`gorilla-*`
folders and third-party names listed in the target's `.fieldkit-skills.json` receipt are ever written or removed.

## Adopted Anthropic skills (third_party/anthropic-skills/)

Pinned to one upstream commit in `ADOPTION.yaml`, with the owner's approval verbatim, the reason for each skill, what
was stripped (Google Fonts, CDN scripts, a script that called api.anthropic.com) and every door left with its reason.
Only skills with real scripts were adopted, each with a command:

| Skill | Command | What it does |
|---|---|---|
| webapp-testing | `fieldkit webtest --server "npm run dev" --port 5173 -- python test.py` | servers up, test, servers' whole process trees down; logs in server-N.log |
| claude-api | `fieldkit prefix-diff reqA.json reqB.json` or `--scan REPO` | offline: what changed in system/tools/messages between two requests |
| mcp-builder | `fieldkit mcp-eval eval.xml --api-base URL -t stdio -c python -a server.py` | a model answers questions through the server; refused without `--api-base` and `ANTHROPIC_API_KEY` |
| skill-creator | `fieldkit skills package DIR [OUT]` | validated, byte-identical `NAME.skill` zip |
| skill-creator | `fieldkit skills benchmark DIR` | benchmark.json + .md from eval runs, offline |
| skill-creator | `fieldkit skills eval --eval-set F --skill-path P --yes` | trigger rate of a description via `claude -p` (costs tokens) |

Not vendored: docx, pdf, pptx, xlsx (all rights reserved; `fieldkit office` does that work offline), doc-coauthoring
(no license). Dropped by the owner: the design, art, brand and communications skills.

## New upstream commit

```
fieldkit skills doors                        # today's tree: 0 unaccepted doors
# extract the new tarball into an empty folder, set `commit:` in ADOPTION.yaml, then:
fieldkit skills vendor <extracted>/anthropics-skills-<sha> --apply
```

A strip that no longer matches stops the rebuild (fail closed); a new door fails `fieldkit skills doors` until it is
stripped or accepted with a reason. Run `fieldkit telemetry scan third_party/anthropic-skills` as well.
