# profilefix

Repair a renamed Windows user profile, and purge a program completely (the Windows `apt-get purge`).
One file, Python 3.8+ standard library only: copy `profilefix.py` to any Windows PC and run it, or use
`fieldkit profile ...` inside Fieldkit. Card `profilefix` in `fieldkit/desk/tools.yaml`; test `tests/test_profilefix.py`;
skill `fieldkit-profile-repair`.

| Command | What it does |
|---|---|
| `doctor` | profiles folder, current user, every profile in ProfileList, and whether the Shell Folders cache is stale (exit 3) |
| `scan [--old NAME] [--key K...] [--verbose]` | read-only: profile folder names the registry mentions that do not exist, and the plan (exit 3 = stale found) |
| `fix [--old NAME] [--apply]` | rewrite `\Users\<OLD>` to `\Users\<NEW>` in value data and value names, delete keys whose name is the old path. Dry run without `--apply`. HKLM and MSI keys need an elevated shell |
| `undo MANIFEST.json` | put back exactly what a run changed (values rewritten back, keys re-imported from their .reg export) |
| `purge-app REGEX [--apply]` | stop the program (by PID), run its own silent uninstaller, then Recycle Bin its install folder, earlier copies, AppData folders, updater cache and shortcuts, and delete its registry keys and caches |

Backups and manifests: `%LOCALAPPDATA%\profilefix\<time>\` (or `--backup-dir`).

## Why it exists (measured 2026-10-24)

An account was renamed by changing only `ProfileImagePath` and the folder name. 5,287 registry values still named
`C:\Users\<OLD>` (266 in HKCU, 5,021 in HKLM, mostly MSI component paths). Among them the legacy
`HKCU\...\Explorer\Shell Folders` cache: `Local AppData = C:\Users\<OLD>\AppData\Local`. Antigravity's 32-bit NSIS
updater (electron-builder) wrote `Uninstall Antigravity.exe` there and failed with "Error opening file for writing",
on every update, though its own registry keys were correct. Reinstalling cannot fix that; `fix` does.

## Rules it keeps

- Only paths with `<profiles folder>\OLD` followed by a separator or the end are touched: `C:\Users\<OLDX>`,
  `C:\Data\OLD` and `OLD@example.com` are not.
- Old name: `--old`, else the stale name in the Shell Folders cache, else the only stale name; with several it refuses.
- Sorted walk: the same registry gives the same plan. `HKLM\SYSTEM\CurrentControlSet` (a link) is not walked twice.
- `purge-app` never removes `~\.<name>` dot folders (user data, projects); it prints them for a human to decide.
- Not covered: shortcuts (.lnk) and application config files that hold the old path; Linux (prints not-this-platform).
