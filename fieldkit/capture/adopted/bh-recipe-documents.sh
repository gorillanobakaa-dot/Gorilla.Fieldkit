#!/usr/bin/env bash
# Recipe: documents
# candidate tool distilled from 9 captured episodes (3 replayed); review before use: it has never been run.
set -euo pipefail
# check branches, where the debian build script lives, ignore rules and embedded files  [check: The command ends with exit code 0.]
git fetch -q origin
# give the test a household; rerun  [check: The command ends with exit code 0.]
git checkout -q tests/test_zero_command.py
# commit under guard (pre-commit tests)  [check: The command ends with exit code 0.]
fieldkit guard -- python
# group privacy findings by kind for my files  [check: The command ends with exit code 0.]
fieldkit privacy scan .
# retry the commit with names removed  [check: The command ends with exit code 0.]
git add -a
