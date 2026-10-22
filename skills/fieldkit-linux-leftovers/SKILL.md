---
name: fieldkit-linux-leftovers
description: Find and clean what removed packages, other desktops (KDE, XFCE, Cinnamon...), virtual machines and old installs left behind on a Linux desktop - packages removed but not purged, two login launchers starting one program, launchers and menu entries whose program is gone, VM guest helpers on real hardware, dead user services and login-screen sessions - show the person first, then clean without touching the running session, and undo a clean. Use when something starts at login that should not, a theme or setting changes after a login, menu icons open nothing, after trying several desktops or VMs, or when asked for a "clean install" without reinstalling.
---

# Desktop leftovers on Linux

Born 2026-10-22. After a login the owner's Files and Settings came up light: two login launchers started the same
theme tool at once and scrambled its stylesheet. One was the leftover of a package removed but never purged
(dpkg state `rc`). Removing a package keeps its config files, and an autostart `.desktop` is a config file: it keeps
starting at every login.

## The order

1. `fieldkit leftovers doctor` (dpkg-query, systemd-detect-virt, systemctl, sudo).
2. `fieldkit leftovers scan` - read-only. Show the person the findings in plain words, grouped by kind. Exit 3 means
   findings, 0 clean.
3. `fieldkit leftovers clean` - prints the plan only. Ask the person which to clean; ids are taken from the scan.
4. `fieldkit leftovers clean [ID ...] --apply` - only after the person said yes. Without ids it takes every finding
   not marked "only when named" (login-screen sessions and unowned system menu entries need naming).
5. Read the last line. `done: ... safe to log out and back in` is the only success. `LOGIN-UNSAFE`, `FAILED` or
   `STILL-THERE` means stop and report; the run folder holds everything.
6. Undo: `fieldkit leftovers restore ~/Agents.Work.Trash/leftovers-<time>` (plan), then `--apply`.

## What each action does, and why nothing crashes

| Kind | Action |
|---|---|
| removed-package (rc) | config files copied to the run folder, then `dpkg --purge` (password) |
| autostart-duplicate | the copy NOT owned by the program's own package is hidden |
| autostart-dead | user file moved out; system file hidden |
| vm-guest-agent | hidden (only when `systemd-detect-virt` says `none`) |
| menu-dead | moved out (a user file; a system one only when named, with the password) |
| unit-dead | disabled if enabled, moved out |
| session-dead | reported; moved only when named (password) |

- "Hidden" means a user-level `~/.config/autostart/<same name>.desktop` with `Hidden=true`, the autostart spec's own
  switch-off. The system file stays, so dpkg never sees a changed or missing file it owns.
- Nothing is stopped. Every action changes what starts at the NEXT login or what the menu lists.
- An installed package is never removed. Another desktop is reported with the apt command; removing a desktop is the
  person's decision.
- Every file is copied into the run folder before it changes, with `manifest.json`. A half-finished run is
  recoverable.

## Never leftovers (each was a false alarm on 2026-10-22)

- A unit linked to `/dev/null` is a MASK, a deliberate switch-off (filter-chain.service).
- A package's menu entry hidden by its own `TryExec` (vim.desktop with only vim-tiny installed).
- A launcher with `OnlyShowIn`/`NotShowIn` excluding this desktop: it never starts here (reported as info only).
- GNOME Classic is GNOME: login sessions are matched by their `DesktopNames=` line, never by file name.
- Debian's `desktop-base` ships `xdg-user-dirs-kde.desktop` with no OnlyShowIn, so on GNOME both it and
  `xdg-user-dirs.desktop` start. That IS a duplicate; the program's own package (xdg-user-dirs) keeps its copy.

## Related

- `fieldkit gtk-theme check` finds a stylesheet scrambled by two writers and the duplicate launchers behind it.
- Symptoms: `fieldkit build-harness symptom "<what you see>"` (desktop-leftovers, theme-scrambled-after-login).
