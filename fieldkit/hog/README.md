# fieldkit hog

The resource watchdog. Owner-facing guide: `skills/fieldkit-hog/SKILL.md`. Thresholds: `thresholds.json`.

## hog churn (churn.py)

Counts the processes the room hook starts and catches whoever starts new console programs.

- `fieldkit hog churn hooks [--event E] [--json]` runs `python -m fieldkit.autopilot.room --hook` once per event
  (SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop) with a probe payload and a temporary
  `FIELDKIT_ROOM_DIR`, and polls the hook's descendants every 10 ms with psutil. Budget: `churn_budget` in
  `thresholds.json` (0 per event, SessionStart 8). Exit 1 when an event is over its budget.
- `fieldkit hog churn trace [--secs N] [--json]` watches the whole process table for new conhost, OpenConsole,
  powershell, pwsh, git, gh, netsh, typeperf, wmic and cmd processes, walks each one up past those names to the
  program that started it, groups by that command line, names the Fieldkit module and projects an hourly rate.
- `fieldkit hog churn scan [--json]` builds a function-level call graph of the `fieldkit` package with `ast`, starting
  at `fieldkit.autopilot.room:hook` and `:main` (an import runs the imported module's top level; Thread, map, submit
  and key= targets are followed; `if __name__ == "__main__"` blocks are not), and lists every reachable
  `subprocess`, `os.system`/`exec*`/`spawn*`/`startfile` and `asyncio` subprocess call, plus a program name
  (powershell, netsh, typeperf, wmic) handed to a call. `churn_allow.yaml` lists the accepted ones, each with a reason;
  an entry with no reason does not count, and an entry no longer reachable is reported as stale.

Read-only: nothing is stopped. Tests: `tests/test_hog_churn.py` (the scan gate runs on every CI; the zero-children
UserPromptSubmit probe runs on Windows).

## Kernel limits (probe.limits, rules.limits)

`probe.limits()` measures visible windows (EnumWindows; the desktop-heap proxy), per-process USER and GDI objects
(GetGuiResources), per-process handles (psutil), the process count and copies per name, the pagefile use and the
paging rate (PDH `\\Memory\\Pages Input/sec` over 1 s on Windows, pswpin+pswpout from /proc/vmstat on Linux). It never
raises; a part it cannot read is absent. `rules.limits()` turns them into desktop_heap, gui_objects, handles,
processes, process_storm and thrash findings (thresholds in thresholds.json). Tests: `tests/test_hog.py` (kernel
limits section), mutations in `tests/hog_mutations.yaml`.

## hog consoles (consoles.py) and fieldkit/proc.py

`fieldkit hog consoles [--reap] [--json]` groups every conhost/OpenConsole by its parent process and exits 1 when a
Python process running `fieldkit` owns one. `--reap` stops the empty ones (`empty_hosts`) by PID. It also counts each terminal's visible windows: a terminal (WindowsTerminal, OpenConsole, conhost) holding 20+ windows for 10 minutes is a flood, fails the check, and the watch stops it by PID (`reap_floods`). The prevention is
`fieldkit/proc.py` (DETACHED_PROCESS plus redirected streams) and the CI gate `tests/test_no_console_spawn.py`
(allowlist `fieldkit/proc_allow.yaml`, one reason per entry).
