#!/bin/sh
# SessionStart (P41a): the project brief by cwd, from the registry - no file search. Reads the hook JSON on stdin.
# In a Firefox project folder it also appends the migration brief (what the old hook gave). Prints hook JSON, or nothing.
FK=${FIELDKIT_BIN:-$HOME/.local/bin/fieldkit}
cwd=$(jq -r '.cwd // empty' 2>/dev/null)
[ -n "$cwd" ] || exit 0
out=$("$FK" cookbook load --cwd "$cwd" 2>&1)
case "$out" in ""|"no project") exit 0;; esac
case "$out" in PROJECT\ firefox:*) mb=$("$FK" build-harness migrate brief 2>&1); [ -n "$mb" ] && out="$out
Firefox migration state:
$mb";; esac
jq -Rn --arg o "$out" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:("Project brief (fieldkit cookbook load --cwd; the registry, so no file search is needed):\n" + $o)}}'
