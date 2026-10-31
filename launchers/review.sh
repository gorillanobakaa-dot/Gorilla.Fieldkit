#!/bin/sh
# Runs the one review (everything waiting on the owner, answered once). Double-click and "Run in terminal", or run it.
cd "$(dirname "$0")/.." || exit 1
python3 -m fieldkit review
printf '\nPress Enter to close. '
read -r _
