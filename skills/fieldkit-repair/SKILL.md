---
name: fieldkit-repair
description: Small repair and check tools for failures agents hit again and again, through `fieldkit repair` - Windows cp1252 errors in Python scripts, a missing stdlib import, git conflict markers in append-only logs, C/C++ brace balance before a long compile, bash written into a .ps1 file, who opened dozens of PowerShell consoles, stopping a process only when PID and start time still match, which worktrees are merged and clean, a Board row insert, a Mach-O binary's minimum macOS, and the TPFanControl.ini mode. Use on UnicodeEncodeError charmap, NameError for shutil/re/os, git conflict markers, before a Firefox build, a PowerShell parse error, or before recycling worktrees.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline on Windows, Linux and macOS."
---

# Repair tools (fieldkit repair)

Ported from Kiro's extraction (idea I-19), each made safe: a fix changes a file only with `--apply`, keeps its line
endings, and writes nothing if the result would not parse. Exit 0 fine, 1 error, 3 findings; each ends with `NEXT:`.

| The failure | Run |
|---|---|
| `UnicodeEncodeError: 'charmap'` in a script | `fieldkit repair cp1252-stdout-fix FILE [--apply]` |
| `NameError: name 'shutil' is not defined` | `fieldkit repair missing-import-fix FILE [--apply]` |
| `<<<<<<<` in a log or Board where both sides added lines | `fieldkit repair keep-both FILE... [--apply]` (not for tools.yaml: `fieldkit merge-driver`) |
| C/C++ delimiters before a long compile | `fieldkit repair brace-parity-check FILE...` |
| bash heredoc / `export` in a `.ps1`, parse errors | `fieldkit repair pwsh-syntax-check PATH...` |
| dozens of console windows, who owns them | `fieldkit repair pwsh-parent-chains` |
| stop a process, PID may have been reused | `fieldkit repair pid-guarded-stop --pid N --name NAME [--started T --stop]` |
| which worktrees can be recycled | `fieldkit repair worktree-classify-recycle` then `fieldkit size recycle PATH` |
| a Board row into PLAN.md | `fieldkit repair plan-row-insert FILE --anchor A --row R [--apply]` |
| a Mach-O's minimum macOS (Mac 10.13 release) | `fieldkit repair evidence-macho-extract BINARY [--max-minos 10.13.0]` |
| TPFanControl.ini `Active=` mode, exe pin | `fieldkit repair tpfan-config-verify INI [--set-active N] [--exe F --sha256 H] [--apply]` |

Related: `fieldkit pyliterals` (stray backslashes), `fieldkit triage LOG --set agent-shell` names these failures,
`fieldkit hog` reports a hoarding parent, `fieldkit board add` writes a session's own Board row.
