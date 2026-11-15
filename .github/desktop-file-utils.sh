#!/usr/bin/env bash
# desktop-file-validate for the installverify tests on a Linux runner, WITHOUT Ubuntu's archive (plan P9, 2026-11-11:
# the runner's DNS for archive.ubuntu.com / security.ubuntu.com failed from ~13:59 to past 16:43 while pip worked;
# apt-get was the only thing that needed it, and PR 461 sat blocked for hours).
#
# Source: .github/vendor/desktop-file-utils_0.27-2build1_amd64.deb, committed in this repository, Ubuntu 24.04
# (noble) main. Proven on 2026-11-11: the Ubuntu Archive Automatic Signing Key (2018),
# F6ECB3762474EDA9D21B7022871920D1991BC93C, signs dists/noble/InRelease -> main/binary-amd64/Packages.xz
# (sha256 2a6a199e1031a5c2...) -> the package's SHA256 below.
# Who can act through it: only whoever can push to this repository (the same people who can edit the workflow);
# no new host, no new account, nobody learns anything new (the checkout already happens).
#
# A wrong hash FAILS (never falls back: a changed file is not a network problem). Only when dpkg cannot install the
# verified package (its dependencies changed on a newer runner image) is apt tried, with retries and a bound.
set -u
DEB="$(dirname "$0")/vendor/desktop-file-utils_0.27-2build1_amd64.deb"
SHA256="a6b03256d0153a8d1213ee21123d15315945752308f31b63751d7b29543b8bc0"
SUDO="${SUDO-sudo}"

[ -f "$DEB" ] || { echo "missing $DEB"; exit 1; }
echo "$SHA256  $DEB" | sha256sum -c - || { echo "WRONG HASH for $DEB: refusing to install it"; exit 1; }
if command -v desktop-file-validate >/dev/null 2>&1; then
  echo "desktop-file-validate already present: $(command -v desktop-file-validate)"; exit 0
fi
if $SUDO dpkg -i "$DEB" && command -v desktop-file-validate >/dev/null 2>&1; then
  echo "desktop-file-validate installed from the pinned package (no network)"; exit 0
fi
echo "dpkg could not install the pinned package; falling back to apt (bounded)"
$SUDO dpkg -r desktop-file-utils >/dev/null 2>&1 || true
for wait in 30 60 0; do
  $SUDO apt-get -o Acquire::Retries=3 update &&
    $SUDO apt-get -o Acquire::Retries=3 install -y --no-install-recommends desktop-file-utils && exit 0
  [ "$wait" = 0 ] && break
  echo "apt failed; trying again in ${wait}s"; sleep "$wait"
done
exit 1
