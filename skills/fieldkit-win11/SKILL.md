---
name: fieldkit-win11
description: Run the Windows 11 tweaks framework (gorillanobakaa-dot/windows.11.tweaks.optimisation) safely on any Windows 11 PC - check a module, preview it, apply it with the owner's consent, undo it; check first which service-profile entries (measured on one ThinkPad) would be wrong on this PC, and whether the maker's hardware-button services are present. Use when asked to make Windows lighter, disable services, Copilot, OneDrive, recommendations, Xbox, visual effects, apps, or hold feature updates.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Windows 11 tweaks (fieldkit win11)

Always in this order. Every step is one command; every answer ends with NEXT:.

1. `fieldkit win11 modules` - the ten modules, each with its check, its change (with a preview) and its undo.
2. `fieldkit win11 oem` - who made the PC. On a Lenovo, the hardware buttons (mic mute, airplane mode,
   brightness) need Lenovo's services: no profile disables them, and nothing here will.
   Another maker: no verified list - read every "OEM" entry of a services profile before applying it.
3. Before a services profile: `fieldkit win11 fit --profile moderate`. It checks, on THIS PC, the facts the
   profile was built on (no SIM, no WSL, no shared folders, only virtual printers, Remote Desktop off, not in a
   domain). WRONG HERE = do not apply that profile here, or use a lower one. "check it" = could not tell: ask.
4. `fieldkit win11 run MODULE check` (read-only), then `run MODULE apply --preview` (-WhatIf, changes nothing).
5. Apply only with the owner's approval. Through the agent door, the win11-* cards already require it.
   A module that needs admin shows Windows' own consent prompt; the run waits for it and returns its output.
6. Exit 3 = the framework changed nothing (refused without a backup, nothing to do, or a safety stop): read why.
7. Undo: `fieldkit win11 run MODULE undo` - back to the state before the last apply.
   app-debloat cannot always put an app back: it is marked irreversible.

The framework is never edited from here; the ThinkPad it was measured on keeps its profiles.
