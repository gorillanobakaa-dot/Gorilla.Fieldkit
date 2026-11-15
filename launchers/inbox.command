#!/bin/sh
# Opens the inbox page (state/INBOX.md). Double-click or run it.
cd "$(dirname "$0")/.." || exit 1
python3 -m fieldkit review --inbox
