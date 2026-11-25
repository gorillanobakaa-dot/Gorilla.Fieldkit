---
name: fieldkit-profile-repair
description: "Repair a Windows PC after its profile folder was renamed, and purge a program completely (profilefix), the Windows form of apt-get purge. Use for \"Error opening file for writing\" under an old C:\\Users folder, or to uninstall every trace of a program including registry entries."
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Profile repair and program purge (fieldkit profile)

Standalone: `python -I profilefix.py <command>` (one file, standard library). In Fieldkit: `fieldkit profile <command>`.
Every changing command is a dry run until `--apply`, writes a manifest, and has `undo`.

| The person says | Run |
|---|---|
| "The installer tries to write to C:\Users\<someone> who does not exist" | `doctor` (exit 3 = the Shell Folders cache is stale: that is the cause), then `scan` |
| "Fix it" | `fix --apply` (HKCU, no admin). Then the HKLM half, elevated with ONE UAC click: write a .ps1 that runs `fix --old OLD --apply`, launch it with the elevate tool (`elevate.py run X.ps1 --close`), `elevate.py wait LOG --marker ...` |
| "Remove / purge program X completely" | fix the profile FIRST if it is stale (the program's own uninstaller reads the same cache), then `purge-app "^X\b"` (dry run, show the list), then `purge-app "^X\b" --apply` |
| "Put it back" | `undo <manifest>`; purged files are in the Recycle Bin |

Rules:
- Show the dry-run list before `--apply`; it names every key, value and folder.
- Keep a copy of the program's installer before a purge if it will be reinstalled (an updater's pending installer is
  inside the folder being purged). Check its hash against the updater's manifest and its Authenticode signature.
- `purge-app` stops the program by PID: warn the person first, it closes their window.
- After a purge, check the Recycle Bin really holds the folders before saying they are recycled.
- Not covered: .lnk shortcut targets and application config files holding the old path. Say so.
