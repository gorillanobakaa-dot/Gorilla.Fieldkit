---
name: fieldkit-repair
description: "Small fixes for recurring failures via fieldkit repair: UnicodeEncodeError charmap, missing import, conflict markers in logs, brace balance, bash in .ps1, PowerShell parse error, console hoards, safe process stop, merged worktrees, Board rows. Use before a Firefox build or recycling worktrees."
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
| `Expand-Archive` / `Get-FileHash` break on windows-latest (pwsh 7) | `fieldkit repair ps-compat-check FILE... [--apply]` |

Related: `fieldkit pyliterals` (stray backslashes), `fieldkit triage LOG --set agent-shell` names these failures,
`fieldkit hog` reports a hoarding parent, `fieldkit board add` writes a session's own Board row.
