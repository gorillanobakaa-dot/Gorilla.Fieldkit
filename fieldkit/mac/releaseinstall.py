# Version: 1.0.0 · updated 26-10-23-13-40
"""releaseinstall.py - install a program on an old Mac the way a new user would: from the GitHub release page.

    fieldkit mac release-install NAME [--tag vX.Y.Z|latest] [--launch] [--plan]

What it does on the Mac, in order, and it stops at the first step that fails:
  1. quarantine  every older copy of the program (the program, the app in ~/Applications AND /Applications, the
                 Desktop icon, old installer folders and archives in Downloads, Documents and the home folder) is
                 MOVED to ~/Agents.Work.Trash-linux/<product>-old-versions-<time>/ with a MANIFEST.txt saying where
                 each came from. Nothing is deleted. Settings, sign-in and conversations are data, not versions: kept.
  2. download    the release asset and its SHA256SUMS from github.com/<repo>/releases/download/<tag>/, nothing else:
                 no copy from this machine, no build folder, the same bytes a person anywhere gets.
  3. verify      the asset against SHA256SUMS (shasum -a 256 -c), then unpack with ditto, as Archive Utility does.
  4. install     the release's own installer, as a double-click runs it.
  5. check       the installed program reports <tag>, the app's Info.plist says the same version, and no second
                 copy of the program or the app is left anywhere it looked in step 1.
  6. --launch    double-click the Desktop icon (open) and fetch a picture of the screen (fieldkit mac shot).

Born 2026-10-23. The owner, after a night of sessions tested builds copied by hand onto the MacBook Air while the
release page had no Mac file at all: "learn the harness to behave like a new user: go to GitHub, take the new Mac
program from the release page, download it, install it and test it. Old versions must not stay installed on this
computer." Every earlier test on the Air ran a binary that no user could have got.

Not covered here: Gatekeeper's "unidentified developer" prompt on first open. A file fetched by curl over SSH has
no quarantine attribute, so the prompt a Safari download raises is not exercised; say so in any report.
"""
import datetime
import json
import shlex
import urllib.request

PRODUCTS = {
    "opencode": {
        "repo": "gorillanobakaa-dot/Gorilla.Opencode",
        "asset": "gorilla-opencode-{v}-mac-10.13-x86_64.zip",
        "sums": "SHA256SUMS-v{v}.txt",
        "folder": "gorilla-opencode-{v}-mac",
        "installer": "Install Gorilla OpenCode.command",
        "binary": "$HOME/.local/bin/gorilla-opencode",
        "app": "Gorilla OpenCode.app",
        "desktop": "Gorilla OpenCode",
        # old installs: exact paths, then name patterns searched two levels deep in these folders
        "old_paths": ["$HOME/.local/bin/gorilla-opencode", "$HOME/Applications/Gorilla OpenCode.app",
                      "/Applications/Gorilla OpenCode.app", "$HOME/Desktop/Gorilla OpenCode",
                      "$HOME/Desktop/Gorilla OpenCode.command"],
        "old_globs": ["gorilla-opencode-*-mac*", "gorilla-opencode-mac*", "gorilla-0.1.*", "mac gorila.opencode*",
                      "mac gorilla.opencode*", "Gorilla-release-test"],
        "old_dirs": ["$HOME/Downloads", "$HOME/Documents", "$HOME"],
    },
}


def latest_tag(repo, opener=urllib.request.urlopen):
    with opener(f"https://api.github.com/repos/{repo}/releases/latest", timeout=30) as r:
        return json.load(r)["tag_name"]


def script(product, tag, stamp, launch=False):
    """The shell script the Mac runs. One script, so a Wi-Fi drop between steps cannot leave half an install."""
    p = PRODUCTS[product]
    v = tag.lstrip("v")
    url = f"https://github.com/{p['repo']}/releases/download/{tag}"
    asset, sums, folder = p["asset"].format(v=v), p["sums"].format(v=v), p["folder"].format(v=v)
    q = f"$HOME/Agents.Work.Trash-linux/{product}-old-versions-{stamp}"
    paths = " ".join(f'"{x}"' for x in p["old_paths"])
    globs = " ".join(shlex.quote(g) for g in p["old_globs"])
    dirs = " ".join(f'"{d}"' for d in p["old_dirs"])
    return f"""set -u
step() {{ printf '\\n== %s\\n' "$1"; }}
fail() {{ printf 'FAILED: %s\\n' "$1"; exit 1; }}
Q="{q}"; DL="$HOME/Downloads"
step "1 quarantine older copies into $Q"
mkdir -p "$Q" || fail "cannot create $Q"
qmove() {{ d="$Q/$(printf '%s' "${{1#/}}" | tr / _)"; mv "$1" "$d" && printf '%s\\t<- %s\\n' "$d" "$1" >> "$Q/MANIFEST.txt"; }}
for p in {paths}; do [ -e "$p" ] || [ -L "$p" ] && qmove "$p"; done
for d in {dirs}; do
  for g in {globs}; do
    find "$d" -maxdepth 2 -iname "$g" -not -path "*/Agents.Work.Trash*" -not -path "*/.Trash/*" -not -path "*/Library/*" \\
      -print 2>/dev/null
  done
done | sort -u | while IFS= read -r p; do [ -e "$p" ] && qmove "$p"; done
if [ -f "$Q/MANIFEST.txt" ]; then sed "s#$HOME#~#g" "$Q/MANIFEST.txt"; else echo "nothing older was installed"; rmdir "$Q"; fi
step "2 download {asset} and {sums} from the release page"
cd "$DL" || fail "no Downloads folder"
[ -e "{sums}" ] && qmove "$DL/{sums}"   # an earlier download of the same files went to quarantine in step 1 or here
curl -fsSL -o "{asset}" "{url}/{asset}" || fail "download of {asset}"
curl -fsSL -o "{sums}" "{url}/{sums}" || fail "download of {sums}"
step "3 verify and unpack"
grep " {asset}$" "{sums}" | shasum -a 256 -c || fail "{asset} does not match {sums}"
ditto -x -k "{asset}" . || fail "unpack"
[ -x "{folder}/{p['installer']}" ] || fail "the installer is missing or not executable in the zip"
step "4 install with the release's own installer"
( cd "{folder}" && echo | bash "./{p['installer']}" ) || fail "the installer stopped"
step "5 check"
GOT="$("{p['binary']}" --version 2>/dev/null | tail -1)"
[ "$GOT" = "{tag}" ] && echo "[ok] program reports $GOT" || fail "program reports ${{GOT:-nothing}}, wanted {tag}"
PV="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$HOME/Applications/{p['app']}/Contents/Info.plist" 2>/dev/null)"
[ "$PV" = "{v}" ] && echo "[ok] app version $PV" || fail "app says ${{PV:-nothing}}, wanted {v}"
[ -e "/Applications/{p['app']}" ] && fail "an old app is still in /Applications"
N="$(command -v -a "$(basename "{p['binary']}")" 2>/dev/null | sort -u | wc -l | tr -d ' ')"
[ "${{N:-0}}" -le 1 ] && echo "[ok] one copy of the program on PATH" || fail "$N copies of the program on PATH"
echo "[ok] {tag} installed from the release page; older copies in $Q"
""" + (f"""step "6 launch: the Desktop icon, as a double-click"
open "$HOME/Desktop/{p['desktop']}" || fail "open the Desktop icon"
""" if launch else "")


def main(o, mac, address, ssh_argv, take_shot, now=None):
    """o: parsed args (product, tag, launch, plan). Returns the exit code."""
    p = PRODUCTS[o.product]
    tag = o.tag
    if tag == "latest":
        tag = latest_tag(p["repo"])
    stamp = (now or datetime.datetime.now()).strftime("%y%m%d%H%M")
    text = script(o.product, tag, stamp, launch=o.launch)
    if o.plan:
        print(text)
        return 0
    import subprocess
    rc = subprocess.run(ssh_argv(mac, address, "bash -s"), input=text, text=True).returncode
    if rc == 0 and o.launch:
        import time
        time.sleep(15)   # the launcher sizes the window and asks for the work folder
        out = f"{o.name}-{o.product}-{tag}-{(now or datetime.datetime.now()):%y-%m-%d-%H-%M}.png"
        take_shot(mac, address, f"/tmp/fieldkit-release-install-{stamp}.png", out)
    return rc
