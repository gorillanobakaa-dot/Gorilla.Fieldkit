#!/bin/sh
# Version: 1.0.0 · updated 26-10-14-21-10
# Wait until no Fieldkit pre-commit hook is running, then exit 0. Usage: hooks/wait-for-hook.sh [max-seconds]
# The hook writes <git-common-dir>/pre-commit.running.<pid> while it runs. A marker whose process is gone (a hook
# killed with SIGKILL never runs its trap) is removed, so a dead hook never blocks a waiter for ever.
# Why a marker and not `pgrep -f hooks/pre-commit`: a pgrep loop matches its own command line (2026-10-14).
dir="$(git -C "$(dirname "$0")/.." rev-parse --git-common-dir 2>/dev/null)" || { echo "wait-for-hook: not in a git repo"; exit 2; }
case "$dir" in /*) ;; *) dir="$(cd "$(dirname "$0")/.." && cd "$dir" && pwd)" ;; esac
max="${1:-1800}"; waited=0
while :; do
  busy=0
  for m in "$dir"/pre-commit.running.*; do
    [ -e "$m" ] || continue
    pid="${m##*.}"
    if kill -0 "$pid" 2>/dev/null; then busy=1; else rm -f "$m"; fi
  done
  [ "$busy" = 0 ] && exit 0
  [ "$waited" -ge "$max" ] && { echo "wait-for-hook: still running after ${max}s"; exit 1; }
  sleep 5; waited=$((waited + 5))
done
