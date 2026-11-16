#!/usr/bin/env bash
# Recipe: test
# candidate tool distilled from 82 captured episodes (5 replayed); review before use: it has never been run.
set -euo pipefail
# run all tests  [check: The command ends with exit code 0.]
cargo test --release <N>>&<N>
# compare opencode remote branches  [check: The command ends with exit code 0.]
git fetch -q origin
# build and run affected package tests  [check: The command ends with exit code 0.]
go build ./... <N>>&<N>
# run all tui tests  [check: The command ends with exit code 0.]
go test ./internal/llm/tools/ -run
# show the failure  [check: The command ends with exit code 0.]
python -m pytest -q
# commit a local checkpoint  [check: The command ends with exit code 0.]
git add -a
