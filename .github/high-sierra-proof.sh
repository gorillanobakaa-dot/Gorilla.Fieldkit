#!/usr/bin/env bash
# Run only from the dedicated release bridge. The SSH alias pins the disposable guest's host key.
set -euo pipefail

target=$1
version=$2
assets=$3
proof=$4
producer=$(git -C "$FIELDKIT_CHECKOUT" rev-parse HEAD)
[[ $target == macos-x86_64 && $version =~ ^[0-9][A-Za-z0-9.+-]*$ ]]
[[ $GITHUB_SHA =~ ^[0-9a-f]{40}$ && $producer =~ ^[0-9a-f]{40}$ ]]
[[ $GITHUB_REPOSITORY =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]
[[ $GITHUB_WORKFLOW_REF =~ ^[A-Za-z0-9_./@+-]+$ ]]
[[ $GITHUB_EVENT_NAME == workflow_dispatch ]]

host=fieldkit-high-sierra
stage=$(ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$host" 'mktemp -d /tmp/fieldkit-install.XXXXXXXX')
[[ $stage =~ ^/tmp/fieldkit-install\.[A-Za-z0-9]+$ ]]
shopt -s nullglob
packages=("$assets"/*.pkg)
if ((${#packages[@]} == 0)); then
  echo 'No .pkg in build artifact' >&2
  exit 1
fi
scp -o BatchMode=yes -o StrictHostKeyChecking=yes "${packages[@]}" "$host:$stage/"

ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$host" bash -s -- \
  "$stage" "$producer" "$target" "$version" "$GITHUB_SHA" "$GITHUB_REPOSITORY" "$GITHUB_WORKFLOW_REF" <<'GUEST'
set -euo pipefail
cd "$1"
git init -q fieldkit
git -C fieldkit fetch -q --depth 1 https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit.git "$2"
git -C fieldkit checkout -q --detach FETCH_HEAD
git init -q product
git -C product fetch -q --depth 1 "https://github.com/$6.git" "$5"
git -C product checkout -q --detach FETCH_HEAD
export GITHUB_ACTIONS=true GITHUB_EVENT_NAME=workflow_dispatch GITHUB_SHA="$5" GITHUB_REPOSITORY="$6" GITHUB_WORKFLOW_REF="$7"
export PYTHONPATH="$PWD/fieldkit"
python3.11 -m fieldkit.method.installproof --repo "$PWD/product" --target "$3" --version "$4" \
  --directory "$PWD" --output "$PWD/proof.json" -- bash .github/verify-candidate-install.sh "$3"
GUEST

mkdir -p "$(dirname "$proof")"
scp -o BatchMode=yes -o StrictHostKeyChecking=yes "$host:$stage/proof.json" "$proof"
