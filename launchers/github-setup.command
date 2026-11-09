#!/bin/sh
# Double-click on macOS 10.13+: guided GitHub setup (fieldkit github menu). Nothing changes on GitHub unless you type y.
cd "$(dirname "$0")/.." || exit 1
PY=$(command -v python3 || command -v python) || { echo "Python is not installed. Install python3 and try again."; read -r _; exit 1; }
"$PY" -m fieldkit github menu
echo
printf 'Done. Press Enter to close this window. '
read -r _
