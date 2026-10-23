#!/bin/sh
# Version: 1.1.0 · updated 26-10-23-16-30
# Fieldkit on a computer WITHOUT git (macOS or Linux): no Homebrew, no Xcode, no admin password needed.
#
#   sh install.sh [--yes] [--full] [--branch NAME] [FOLDER]
#
# Downloaded on its own:   curl -fLO https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/main/install.sh
#                          sh install.sh                (it downloads Fieldkit's zip into ./Fieldkit)
# Inside an unzipped Fieldkit folder (downloaded in a browser):  sh install.sh     (no download)
#
# Born 2026-10-23 on a 2011 MacBook Air (macOS 10.13, 4 GB), cleaned of Homebrew, Xcode and git to save memory:
# the first Mac the harness met, where "git clone" could not work. What it does, asking before each step that
# downloads or installs (--yes answers yes):
#   1 finds a Python 3.11 or newer, also where python.org hides it (/usr/local/bin is missing from ssh's PATH)
#   2 downloads GitHub's zip with curl (3.7 MB on 2026-10-23) and unzips it into FOLDER (default ./Fieldkit);
#     never into a folder that already has files in it
#   3 runs the doctor's probe: the machine, and the way in (zip; lean when memory is under 8 GB or no compiler)
#   4 makes a private Python environment in FOLDER/.venv and installs the lean set (PyYAML, psutil), or with
#     --full every dependency; nothing is installed outside FOLDER
#   5 runs the doctor and writes FOLDER/INSTALLED.txt: everything downloaded and installed, and how to remove it
# Exit 0 ready, 3 something to do (the last lines say what), 2 bad usage.
set -u
REPO_ZIP="https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/archive/refs/heads"
YES=0; FULL=0; BRANCH=main; DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --full) FULL=1 ;;
    --branch) shift; BRANCH="${1:-main}" ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    -*) echo "unknown option $1 (sh install.sh --help)"; exit 2 ;;
    *) DEST="$1" ;;
  esac
  shift
done

ask() {   # ask "question": 0 = yes
  [ "$YES" = 1 ] && { echo "$1 yes (--yes)"; return 0; }
  printf '%s [y/N] ' "$1"
  a=""; { read -r a </dev/tty; } 2>/dev/null || read -r a || true   # no keyboard (a script, a test): the answer is no
  case "$a" in y|Y|yes|YES|da|DA) return 0 ;; *) return 1 ;; esac
}
say() { printf '%s\n' "$*"; }
LOG=""
note() { LOG="$LOG$*
"; }

# 1 Python 3.11+
PY=""
for c in python3 /usr/local/bin/python3 /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
         /usr/local/bin/python3.11 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         python3.14 python3.13 python3.12 python3.11 "$HOME/.local/bin/python3"; do
  p=$(command -v "$c" 2>/dev/null) || continue
  "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null && { PY="$p"; break; }
done
if [ -z "$PY" ]; then
  say "Fieldkit needs Python 3.11 or newer and none was found."
  case "$(uname -s)" in
    Darwin) say "Download it from https://www.python.org/downloads/macos/ (the 3.13 installer runs on macOS 10.13 and newer), install it, then run this again." ;;
    *) say "Install it with your system's package manager (Debian/Ubuntu: sudo apt-get install python3), then run this again." ;;
  esac
  exit 3
fi
say "Python: $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# 2 the code: here already, or GitHub's zip
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -f "$HERE/fieldkit/core/doctor.py" ] && [ -z "$DEST" ]; then
  SRC="$HERE"
  say "Fieldkit is already here: $SRC (no download)"
else
  DEST="${DEST:-$PWD/Fieldkit}"
  case "$DEST" in /*) ;; *) DEST="$PWD/$DEST" ;; esac
  if [ -d "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
    say "STOP: $DEST already has files in it. Choose an empty or new folder: sh install.sh /path/to/new-folder"
    exit 3
  fi
  URL="${FIELDKIT_ZIP_URL:-$REPO_ZIP/$BRANCH.zip}"
  if ! command -v curl >/dev/null 2>&1; then
    say "curl is missing, so download Fieldkit in a browser instead:"
    say "  1. open $URL"
    say "  2. unzip it (double-click the zip)"
    say "  3. in the unzipped folder run: sh install.sh"
    exit 3
  fi
  ask "Download Fieldkit (about 4 MB, branch $BRANCH) into $DEST?" || { say "Nothing downloaded."; exit 3; }
  TMP=$(mktemp -d 2>/dev/null || mktemp -d -t fieldkit)
  # A dropped connection resumes from where it stopped, not from zero (the people this is for are on single-digit
  # KB/s). Stops when a try makes no progress, after 6 tries, or when the server cannot resume twice in a row.
  fetch() {
    PART="$TMP/fieldkit.zip.part"; TRY=0; LAST=-1; NORESUME=0
    while [ "$TRY" -lt 6 ]; do
      TRY=$((TRY + 1))
      curl -fL --retry 3 --retry-delay 5 -C - -o "$PART" "$URL"; RC=$?
      [ "$RC" -eq 0 ] && { mv "$PART" "$TMP/fieldkit.zip"; return 0; }
      if [ "$RC" -eq 33 ]; then                 # the server does not do ranges: start again from zero, once
        NORESUME=$((NORESUME + 1)); rm -f "$PART"
        [ "$NORESUME" -ge 2 ] && { say "The server cannot resume a download and it broke twice."; return 1; }
        say "The server cannot resume; downloading again from the start."; continue
      fi
      NOW=$(wc -c < "$PART" 2>/dev/null | tr -d ' '); NOW=${NOW:-0}
      if [ "$NOW" -le "$LAST" ]; then say "No progress since the last try ($NOW bytes): stopping."; return 1; fi
      LAST=$NOW
      say "The download stopped at $NOW bytes (curl $RC); resuming, try $TRY of 6."
      sleep "${FIELDKIT_RETRY_WAIT:-10}"
    done
    return 1
  }
  zip_ok() { "$PY" -c 'import sys, zipfile; z = sys.argv[1]; sys.exit(0 if zipfile.is_zipfile(z) and zipfile.ZipFile(z).testzip() is None else 1)' "$1"; }
  if ! fetch; then
    say "The download failed ($URL). On a weak connection try again later; or download it in a browser and run install.sh inside the unzipped folder."
    exit 3
  fi
  if ! zip_ok "$TMP/fieldkit.zip"; then          # a resumed file can be damaged: one clean download, then stop
    say "The downloaded zip is damaged; downloading it once more from the start."
    rm -f "$TMP/fieldkit.zip"
    fetch && zip_ok "$TMP/fieldkit.zip" || { say "The zip is still damaged ($URL). Download it in a browser instead."; exit 3; }
  fi
  SIZE=$(wc -c < "$TMP/fieldkit.zip" | tr -d ' ')
  if command -v unzip >/dev/null 2>&1; then unzip -q "$TMP/fieldkit.zip" -d "$TMP/x"
  else "$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$TMP/fieldkit.zip" "$TMP/x"; fi
  INNER=$(ls -d "$TMP/x"/*/ 2>/dev/null | head -1)
  [ -f "$INNER/fieldkit/core/doctor.py" ] || { say "The zip does not hold Fieldkit ($URL)."; exit 3; }
  mkdir -p "$DEST" && cp -R "$INNER". "$DEST"/ && rm -rf "$TMP"
  SRC="$DEST"
  say "Unzipped into $SRC"
  note "downloaded  $URL  ($SIZE bytes)  ->  $SRC        remove: delete the folder $SRC"
fi
cd "$SRC" || exit 3

# 3 the machine
"$PY" -m fieldkit.core.doctor --probe || true

# 4 a private environment in the folder
if [ "$FULL" = 1 ]; then WHAT="every dependency (about 150 MB; needs a fast connection)"; else WHAT="the lean set: PyYAML and psutil (about 2 MB)"; fi
if ask "Make a private Python environment in $SRC/.venv and install $WHAT?"; then
  "$PY" -m venv "$SRC/.venv" || { say "Could not make the environment ($PY -m venv)."; exit 3; }
  VPY="$SRC/.venv/bin/python"
  note "created     $SRC/.venv  (a private Python environment; nothing outside the folder)        remove: delete $SRC/.venv"
  if [ "${FIELDKIT_SKIP_PIP:-0}" = 1 ]; then
    say "(FIELDKIT_SKIP_PIP=1: no packages installed; tests only)"
  elif [ "$FULL" = 1 ]; then
    "$VPY" -m pip install -e "$SRC[test]" || { say "Installing failed: see the lines above."; exit 3; }
    note "installed   every dependency of pyproject.toml into $SRC/.venv"
  else
    "$VPY" -m pip install "pyyaml>=6" "psutil>=5.9" && "$VPY" -m pip install --no-deps -e "$SRC" \
      || { say "Installing failed: see the lines above."; exit 3; }
    note "installed   pyyaml, psutil, fieldkit (editable) into $SRC/.venv"
  fi
else
  say "No environment made. To use Fieldkit without it: cd $SRC && $PY -m fieldkit doctor --lean"
  VPY="$PY"
fi

# 5 the doctor, and the list
if [ "$FULL" = 1 ]; then "$VPY" -m fieldkit.core.doctor; RC=$?; else "$VPY" -m fieldkit.core.doctor --lean; RC=$?; fi
{
  echo "What install.sh downloaded and installed ($(date '+%Y-%m-%d %H:%M'))"
  printf '%s' "$LOG"
  echo "Nothing was installed outside $SRC. To remove everything, delete that folder."
} > "$SRC/INSTALLED.txt"
say "The list of everything downloaded and installed: $SRC/INSTALLED.txt"
if [ "$RC" = 0 ]; then
  say "READY. Start with: $VPY -m fieldkit next"
else
  say "NOT READY yet: the doctor's lines above say what is missing and the line that installs it."
fi
exit "$RC"
