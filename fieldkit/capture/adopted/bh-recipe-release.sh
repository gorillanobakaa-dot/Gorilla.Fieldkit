#!/usr/bin/env bash
# Recipe: release
# candidate tool distilled from 25 captured episodes (5 replayed); review before use: it has never been run.
set -euo pipefail
# skim the v0.<N> opening note  [check: The command ends with exit code 0.]
gh pr view <N>
# resolve with p5c cards, run touched go tests  [check: The command ends with exit code 0.]
git checkout -q -b
# commit the dual-track documentation  [check: The command ends with exit code 0.]
git commit -q -f
# commit merge and push pr <N>  [check: The command ends with exit code 0.]
fieldkit guard -- python
# commit the register entry locally  [check: The command ends with exit code 0.]
git add -a
# wait for pr <N> ci and merge on green  [check: The command ends with exit code 0.]
fieldkit ship ci-wait <N>
# merge main into pr <N> branch and push  [check: The command ends with exit code 0.]
git fetch -q origin
# close inbox <N>, park parity idea, read board format  [check: The command ends with exit code 0.]
fieldkit inbox add <S>
# start the vaio install and push job  [check: The command ends with exit code 0.]
fieldkit task status --json
# privacy scan and push emerge fix branch  [check: The command ends with exit code 0.]
fieldkit privacy scan .
# list fan control releases, installs and firefox build dates  [check: The command ends with exit code 0.]
gh release view v0.<N>
# recycle the fetch test folder  [check: The command ends with exit code 0.]
fieldkit size recycle <P>
