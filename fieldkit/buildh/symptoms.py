# Version: 1.0.0 · updated 26-10-20-18-54
"""What you SEE -> the tool that diagnoses it. The first command for any visible problem in a Gorilla build.

Born 2026-10-15. The owner: "a narrative is not exactly a deterministic tool ... none of the models was trained on
Firefox compiling, troubleshooting and hardening the way our projects work, so the next session does blind steps,
trial and error and reinvents the wheel." A model that is handed a screenshot does not read a document first; it
starts guessing. This registry turns the description of the symptom into the command that was already built and
proven for it, and says what it proves, where to look, and what the fix was the last time. The session-start brief
(fieldkit/migrate/cli.py) prints the one line that points here, so the next model meets it before it guesses.

    fieldkit build-harness symptom "<what you see, any words, English or Romanian>"
    fieldkit build-harness symptom --all          every recorded symptom

Every entry is DATA checked by tests: its command must be a real build-harness action, its words must match its own
example, and two entries may not claim the same example. A symptom that is not here: diagnose it, build the tool,
then add the entry here (tests/test_buildh_symptoms.py shows the shape). The registry grows with every fault.
"""
import re
import sys

SYMPTOMS = [
    {
        "id": "dialog-text-cut",
        "seen": "A page's dialog (alert, confirm, prompt, 'Leave page?') shows its message cut, as a thin strip or "
                "half a line; the buttons look fine.",
        "example": "the Leave page dialog on web.whatsapp.com shows the message truncated",
        "words": [r"dialog|alert|confirm|prompt|leave page|stay on page|pop-?up|fereastr", r"cut|truncat|clip|squeez|half|not visible|nu se vede|t[aă]iat|trunchi|nu este prezent|not present|missing"],
        "action": "clip-check",
        "run": "build-harness clip-check <task>",
        "proves": "which dialogs cut text (OURS vs stock of the same version, repeated), and WHY: what moved after "
                  "the height was measured, the transition running then, and the CSS rule (sheet:line) behind it",
        "look": ["toolkit/modules/SubDialog.sys.mjs resizeVertically (measures once)",
                 "browser/themes/shared/master-redirect.css (global transition rule)",
                 "toolkit/components/prompts/content/commonDialog.css (:root[subdialog] dialog padding)"],
        "last_fix": "T-158-10-A (2026-10-15): transition-property: none inside :root[subdialog]",
        "then": "try a fix without a build: build-harness clip-check <task> try=<path in the build>=<edited tree file>",
    },
    {
        "id": "launcher-old-name-or-icon",
        "seen": "The app grid, the dock or the menu shows an old version number in the name, or two different "
                "Gorilla icons (grid vs dock vs taskbar).",
        "example": "the app grid says Gorilla Unleashed 155 and the icon differs from the one on the taskbar",
        "words": [r"icon|iconi|launcher|app grid|\bgrid\b|dock|taskbar|bara de|desktop file", r"\bold\b|155|154|differ|diferit|wrong|blurry|vechi|\balt[aă]\b|not the same|nu e la fel"],
        "action": "install",
        "run": "{python} -c \"from fieldkit.build import deb_install as d; print(d.icon_shadows('gorilla-unleashed'));"
               " print(d.stale_shell_note([d.DESKTOP, d.PERSONAL], d.shell_started()))\"",
        "proves": "personal icon files that shadow the package's (~/.local/share/icons, ~/.icons, wrong-size ones "
                  "marked), personal launchers that shadow the system one, and a GNOME Shell older than the menu "
                  "entry (it keeps the old name and picture until log out and in)",
        "look": ["~/.local/share/applications then /usr/share/applications (the menu entry wins in that order)",
                 "~/.local/share/icons, ~/.icons, then /usr/share/icons/hicolor/<size>/apps",
                 "gsettings get org.gnome.shell app-picker-layout (the grid's app id)"],
        "last_fix": "2026-10-15: nine July icon files quarantined to Agents.Work.Trash, personal icon cache rebuilt; "
                    "the grid's '155' is the running shell's memory: log out and in",
        "then": "the install stage checks this by itself after every install (deb_install.verify_install)",
    },
    {
        "id": "video-frames-dropped",
        "seen": "Video stutters or drops frames (often at 60 fps) more than stock Firefox on the same machine.",
        "example": "youtube 1080p60 drops frames and stutters",
        "words": [r"frame|video|fps|youtube|cadre", r"drop|stutter|sacad|\blag|jank|pierd|not smooth"],
        "action": "pref-bisect",
        "run": "build-harness pref-bisect <task>",
        "proves": "which of our graphics prefs costs frames, by halving with an isolated, repeated, confirmed "
                  "measurement against the pristine defaults",
        "look": ["modules/libpref/init/StaticPrefList.yaml", "browser/app/profile/firefox.js",
                 "build-harness decode-test <task> (is decoding on the GPU at all)"],
        "last_fix": "2026-10-15: inside the wobble until confirmed (handover item 6)",
        "then": "measure isolated (hands off, browsers closed); noise = INCONCLUSIVE",
    },
    {
        "id": "video-not-hardware-decoded",
        "seen": "Video uses a lot of CPU or the laptop heats up while playing; hardware decoding may be off.",
        "example": "video playback uses high cpu, is hardware decoding working",
        "words": [r"video|play|youtube|decod", r"cpu|heat|\bhot\b|fan\b|ventilat|[iî]nc[aă]lz|procesor|hardware|gpu|va-?api"],
        "action": "decode-test",
        "run": "build-harness decode-test <task>",
        "proves": "whether the installed browser decodes on the GPU's video engine (local clip, frame counters, "
                  "MOZ_LOG), D-158-06",
        "look": ["widget/gtk/GfxInfo.cpp (blocklist short-circuit)", "gfx/thebes/gfxPlatformGtk.cpp"],
        "last_fix": "D-158-06 / D-158-07 (2026-10-15)",
        "then": "",
    },
    {
        "id": "labels-blank-after-removal",
        "seen": "Menu items, buttons or settings show an empty label after texts (.ftl) were removed or moved.",
        "example": "a context menu item shows a blank line with no text",
        "words": [r"menu|label|button|item|setting|etichet|meniu", r"blank|empty|\bgol|f[aă]r[aă] text|no text|missing text|missing label"],
        "action": "fluent-check",
        "run": "build-harness fluent-check <task>",
        "proves": "every .ftl change parses and every removed message id is no longer used by visible UI",
        "look": ["git grep the message id outside *.ftl", "the <link rel=\"localization\"> lines of the window"],
        "last_fix": "D-158-08 (2026-10-15): remove the UI that uses a text BEFORE the text",
        "then": "",
    },
    {
        "id": "build-error",
        "seen": "The build or packaging stage failed.",
        "example": "the build failed with a compiler error",
        "words": [r"build|compil|mach|\blink|package|pachet", r"fail|error|eroare|e[sș]u|broken|stops"],
        "action": "companions",
        "run": "build-harness companions <task>  (then: build-harness api-drift <task>)",
        "proves": "a ported change missing its other half (companions), or an upstream API that moved (api-drift); "
                  "the pipeline log's failure summary already names the first compiler error and any known fix",
        "look": ["the pipeline log's failure summary", "build/api-drift/firefox.yaml"],
        "last_fix": "",
        "then": "every new build error becomes a tool + signature + test + lesson (owner priority 2026-10-15)",
    },
    {
        "id": "pipeline-wrong-source-tree",
        "seen": "The firefox-linux pipeline's preflight fails on things the work tree has (must-keep files missing, "
                "fonts not listed, 'no objdir yet (clean build)') right after a build that worked.",
        "example": "preflight fails must-keep-tree and package-fonts and says no objdir yet",
        "words": [r"preflight|pipeline|must-keep|package-fonts", r"fail|no objdir|missing|not carry|e[sș]u"],
        "action": "preflight",
        "run": "fieldkit pipeline run firefox-linux --from preflight --var src=<the task's work tree>",
        "proves": "the pipeline's default src is {work}/firefox-{version} under Build.Work: the PRISTINE Mozilla "
                  "tree. The Gorilla work tree (task firefox-158-linux) is ~/Documents/FIREFOX.WORK/work/firefox-158.0; "
                  "with --var src= pointing there, preflight passes and the build uses the existing objdir",
        "look": ["fieldkit/build/pipelines/firefox-linux.yaml (vars: work, src)",
                 "the task's workdir: build-harness migrate sitrep <task>, or intents/port-*/STATE.md",
                 "git -C <src> log --oneline -1: a pristine tree has only Mozilla's commit"],
        "last_fix": "2026-10-15: preflight refused the pristine tree (correctly); re-run with --var src=<work tree>",
        "then": "",
    },
    {
        "id": "files-damaged-need-the-original",
        "seen": "Files in the Firefox work tree are damaged (by a model, a bad patch, a script) and must go back to "
                "Mozilla's original, without git (or with GitHub unreachable).",
        "example": "a model broke browser.xhtml and I need the original file back",
        "words": [r"broke|broken|damag|corrupt|stric|distrus|screw|messed|wrong", r"original|pristine|vault|restore|back|"
                  r"[iî]napoi|restaur|mozilla"],
        "action": "vault",
        "run": "build-harness vault restore-files firefox <work tree> <path> [<path> ...]",
        "proves": "the vault (Vault.Do.Not.Delete, read-only, checked against its recorded commit first) gives back each "
                  "file or folder byte for byte; the current versions go to Agents.Work.Trash; a Gorilla-only file "
                  "(not in the release) is listed and left alone",
        "look": ["build-harness vault verify firefox (is the vault itself intact)",
                 "build-harness vault list (which releases the vault holds)",
                 "git -C <work tree> diff <root commit> -- <path> (what the port changed, when git works)"],
        "last_fix": "2026-10-15: restore-files added; whole trees: build-harness vault restore firefox <new folder>",
        "then": "re-apply only the Gorilla change that file needs (its patch or technique), then the register",
    },
    {
        "id": "decision-not-held",
        "seen": "Something the owner decided against is back (AI feature, new-tab tiles, translations, a theme), or "
                "a decision may not hold in this build.",
        "example": "the AI sidebar or smart window is still in the browser",
        "words": [r"\bai\b|smart ?window|smartbar|sidebar|top ?sites|sponsor|translat|decision|decizie|register|registru", r"still|back|again|[iî]nc[aă]|present|apare|violat|hold"],
        "action": "decisions",
        "run": "build-harness decisions <task>",
        "proves": "every recorded product decision checked on the tree and the install: ENFORCED, VIOLATED, UNCHECKABLE",
        "look": ["gorilla-firefox/decisions/PRODUCT-DECISIONS.yaml"],
        "last_fix": "",
        "then": "quote the decision id; never re-argue a settled decision",
    },
    {
        "id": "addon-or-theme-refused",
        "seen": "Installing an extension, a theme, a language pack or a dictionary fails ('could not be installed', "
                "'not compatible with Gorilla Unleashed', 'Blocked on purpose').",
        "example": "the theme could not be installed because it is not compatible with Gorilla Unleashed 158.0",
        "words": [r"extension|add-?on|theme|tem[aă]|extensi|language pack|dictionar|xpi|could not be installed", r"not compatible|incompatib|blocked on purpose|nu se instaleaz|refuz|nu merge"],
        "action": "decisions",
        "run": "build-harness decisions <task>",
        "proves": "NOT A BUG: decision D-157-05 (uBlock Origin only), D-157-04 (one theme), D-157-03 (English only). The "
                  "refusal IS the hardening (owner 2026-10-15: 'We are the cause ... do NOT mess around with them'). "
                  "The command shows every lock ENFORCED or which one a change removed",
        "look": ["decisions/PRODUCT-DECISIONS.yaml D-157-05 not_a_bug and verify",
                 "toolkit/mozapps/extensions (XPIInstall, XPIDatabase, XPIProvider, AddonManager PHYSICAL LOCKs)",
                 "browser/base/content/browser-addons.js (the refusal message, gorilla-addon-install-refused)"],
        "last_fix": "2026-10-15: only the WORDING may change (it says why); never loosen a lock",
        "then": "if a lock shows VIOLATED, put it back from the patch set (22.EGRESS.LOCKDOWN.*), never the other way",
    },
    {
        "id": "patches-do-not-apply-to-new-release",
        "seen": "The patch set does not apply to a new Firefox release, or replay says the patches do not give the "
                "built tree (FAILED patches, differing paths), or another machine must redo a port by hand.",
        "example": "replay failed: 77 patches do not apply to pristine 158 and 40 paths differ",
        "words": [r"patch|replay|rebase", r"not apply|do not apply|nu se aplic|fail|differ|drift|cascade|superseded|nu merg"],
        "action": "rebase-set",
        "run": "build-harness rebase-set <task> write=1",
        "proves": "supersedes the patches that no longer apply (recorded per release in the policy), writes the rest of "
                  "the way as 23.REBASE.<major> (+ .<platform>), proves pristine + set = built tree with replay, and "
                  "lists per old patch whether its logic is in the tree (SUPERSEDED.md: CARRIED / PARTLY / MISSING)",
        "look": ["patches/23.REBASE.<major>/SUPERSEDED.md (read every MISSING and PARTLY with missing ADDED lines)",
                 "config/patch_policy.json superseded_<major>, platforms, exclude_on",
                 "build-harness export-hand <task> (direct commits are exported too since 2026-10-15)"],
        "last_fix": "2026-10-15, 158: 107 superseded, 2 functional losses found and ported (DNS negative cache 3 s, "
                    "SessionStore Smart Window calls); replay OK with 675 patches",
        "then": "port each LOST item with build-harness record, then rebase-set again; check-change does it after every edit",
    },
    {
        "id": "private-data-before-publishing",
        "seen": "Before a push or a release: could personal data reach GitHub (bookmarks, history, cookies, passwords, "
                "a home path, a name, an email, a key)?",
        "example": "make sure my bookmarks and history are not uploaded to github",
        "words": [r"bookmark|history|istoric|cookie|password|parol|personal|private|privat|secret|email|home path", r"upload|github|push|publish|urc|public"],
        "action": "privacy",
        "run": "fieldkit privacy push-check <repo> --range <remote>..HEAD",
        "proves": "scans every file the push sends, as it is in the pushed commit: secrets, personal paths and names, "
                  "emails, and browser profile data by name, by content (SQLite, mozLz4, bookmark export) and inside "
                  "patches; profile data can never be allowed",
        "look": ["fieldkit privacy install-hook <repo> (pre-push hook: every push is checked)",
                 "fieldkit privacy scan <folder> --git (everything git would publish)",
                 "fieldkit.local.json privacy.terms (the private word list, never committed)"],
        "last_fix": "2026-10-16: detector for browser profile data added; the 158 push was clean (306 files)",
        "then": "remove the file from the commits and add it to .gitignore; never publish, then delete",
    },
    {
        "id": "menu-colors-wrong",
        "seen": "A menu, list or popup is white or grey, its text hard to read, the row under the mouse is not the "
                "purple highlight, or a label is cut short, although the theme CSS says otherwise.",
        "example": "the search list popup is white and the hover color is wrong",
        "words": [r"menu|popup|pop-?up|list|panel|hover|highlight|meniu|lista",
                  r"white|grey|gray|alb|gri|wrong|color|colour|culoare|contrast|read|cut|t[aă]iat|purple|mov"],
        "action": "probe",
        "run": "build-harness probe <task> js=theme-diag --install-dir C:\\gfobj\\dist\\bin [file=<path in dist/bin>=<edited tree file> ...]",
        "proves": "what REALLY paints each surface (appearance, background, variables, the item's colors, the hover "
                  "after the transition, the label's needed vs given width), on a copy of the UNPACKED build: a CSS "
                  "fix is tried in about 90 s, never by a rebuild; the gate runs the same loop on every chrome change",
        "look": ["browser/themes/shared/master-redirect.css (GORILLA THEME, ONE CONSTANT; menupopup block)",
                 "browser/themes/shared/toolbarbuttons.css (#gorilla-satellite-button > menupopup)",
                 "browser/themes/shared/customizableui/panelUI-shared.css (#appMenu-popup panelview width)",
                 "gorilla-patchset/decisions/THEME.yaml (the spec the rows judge against)"],
        "last_fix": "D-158-19 (2026-10-18): menus were the system's white (menupopup appearance:auto, Menu), the "
                    "Satellite hover cyan 25%, the app menu sized by panelview 25em not --menu-panel-width",
        "then": "edit the tree file, prove it with file=..., record it (build-harness record), export-hand; the build "
                "gate's fast-loop rows must pass before compiling",
    },
    {
        "id": "window-changes-when-unfocused",
        "seen": "A desktop app (Files, Settings, System Monitor) looks right while in use, but when another window "
                "is on top or after Alt+Tab its sidebar turns light grey or white, its text pale, its header faded.",
        "example": "when I alt tab the file manager sidebar turns into a white bar and the text looks faded",
        "words": [r"alt\W?tab|unfocus|not focus|another (app|window|program)|on top|backdrop|in spate|f[aă]r[aă] focus",
                  r"grey|gray|white|pale|fad|dim|contrast|weird|gri|alb|[sș]ters|decolorat"],
        "action": "gtk-theme",
        "run": "fieldkit gtk-theme check",
        "proves": "every unfocused-window (:backdrop) colour name, opacity fade and fixed grey that the toolkits on "
                  "THIS machine apply (read from libadwaita, GTK4 and GTK3 themselves) and the user stylesheet does "
                  "not override; then which apps still draw an old stylesheet",
        "look": ["~/.config/gtk-4.0/gtk.css and ~/.config/gtk-3.0/gtk.css (the only sheets libadwaita reads)",
                 "Documents/gtk-high-contrast-theme/install.sh (the Black Gorilla Theme source of both blocks)",
                 "Documents/gtk-high-contrast-theme/DOCUMENTATION.dual-track.md section D"],
        "last_fix": "Black Gorilla Theme 26.10.20 (2026-10-20): sidebar/headerbar/secondary_sidebar _backdrop_color "
                    "named, libadwaita filter: opacity(0.5) cancelled by selector, GTK3 .background:backdrop #919190 "
                    "overridden, five GTK3 unfocused_* names set",
        "then": "edit install.sh, run it, run the check again, then restart the app (closing its window is not enough)",
    },
    {
        "id": "theme-fix-not-showing",
        "seen": "A GTK theme or gtk.css change was made and installed, but the app on screen still looks the old "
                "way, even after closing and opening its window.",
        "example": "I closed and restarted the file manager and it still does not look fixed",
        "words": [r"restart|reopen|closed|close|reinstall|upgrad|after|repornit|[iî]nchis|redeschis",
                  r"(not|n't|still) (look |seem |appear )?(fixed|changed|different|work)|still (the )?(same|old)|"
                  r"nu (s-a )?(schimbat|merge)|tot la fel|neschimbat"],
        "action": "gtk-theme",
        "run": "fieldkit gtk-theme check",
        "proves": "which GTK programs started BEFORE the stylesheet was last written (one line each, with how to "
                  "really restart it), and whether the installed theme package writes something different from the "
                  "live stylesheet",
        "look": ["ps -o lstart -C nautilus: Files runs as nautilus --gapplication-service and survives its window",
                 "/usr/bin/black-gorilla-theme --apply-once (refreshes users who kept the theme since 26.10.20)"],
        "last_fix": "2026-10-20: Files had run since 15:23 (fix written 18:19); /usr/bin/nautilus -q then "
                    "gapplication launch org.gnome.Nautilus showed the fix at once",
        "then": "restart what it lists; a service app needs its quit command, not its window closed",
    },
    {
        "id": "text-looks-different-on-two-screens",
        "seen": "The same page or app looks different on two machines: text thin and crisp on one, thick, bold, blurry or "
                "over-smoothed on the other; or a font looks wrong and nobody can say which font or why.",
        "example": "here the fonts are thin crisp, on the mac they look oversmoothed thick blurry, is that antialias or serif",
        "words": [r"font|text|letters|typeface|segoe|serif|antialias|anti-alias|smooth|hinting",
                  r"thick|thin|bold|blurr?y|crisp|sharp|fuzzy|soft|oversmooth|readab|compare|difference|different"],
        "action": "shot",
        "run": "fieldkit shot text-compare A X,Y,W,H B X,Y,W,H --out both.png",
        "proves": "stroke width in pixels, ink share, blur (half-tone pixels), coloured (LCD) smoothing and extent of the "
                  "SAME text on two screenshots, with the two boxes stacked at 3x; a different extent means a different font",
        "look": ["fieldkit shot grid FILE to find the box; fieldkit shot crop FILE BOX --zoom 3 to see pixels",
                 "fieldkit mac shot NAME to fetch an old Mac's screen", "which font is drawn: browser UI and about: pages "
                 "use the OS interface font, web pages the browser's default (D-158-12/21)"],
        "last_fix": "2026-10-22: about:about on the MacBook Air: strokes 1.40x the Linux ones (2.97 vs 2.12 px) and taller "
                    "text: macOS's interface font with its stroke-widening smoothing, not Segoe UI; not serif",
        "then": "change ONE thing (the font, or -moz-osx-font-smoothing), take the same screenshot, text-compare again",
    },
    {
        "id": "old-mac-unreachable",
        "seen": "The old Mac (MacBook Air or another out-of-support Mac) cannot be reached over SSH, its address changed, "
                "the ssh alias stopped working, or a session does not know how to connect to it.",
        "example": "can you still connect to the macbook air via ssh",
        "words": [r"mac\b|macbook|mac ?air|imac|mac ?mini|sierra|high sierra|el capitan|mojave|osx|os x",
                  r"ssh|connect|reach|address|ip\b|alias|remote|log ?in|unreachable|offline|can'?t get"],
        "action": "mac",
        "run": "fieldkit mac doctor",
        "proves": "the key is there with safe permissions, the Mac's address today (mDNS name, else hardware address, "
                  "else after a ping sweep), that login works and which macOS answers, and whether the ssh alias works",
        "look": ["local/macs.json (private: the Macs this machine knows)", "~/.ssh/config Host entries",
                 "avahi-resolve -4 -n NAME.local; ip -4 neigh"],
        "last_fix": "2026-10-22: a handover said 'ssh macair' was stale after the Mac's name changed; tested, it worked "
                    "(mDNS answers some minutes, not others). The Air is now in local/macs.json with its hardware "
                    "address, so a silent mDNS or a new DHCP address no longer loses it",
        "then": "fieldkit mac fix-alias NAME --apply if the alias fails; fieldkit mac run NAME -- COMMAND for work on it",
    },
    {
        "id": "device-firmware-missing",
        "seen": "A USB Wi-Fi stick, antenna, network card or other device is plugged in and detected, but never works: "
                "its interface stays down, it disconnects and reconnects in a loop, or it is missing from the network list.",
        "example": "the alfa antenna is connected but the wifi interface stays down",
        "words": [r"antenna|antena|alfa|dongle|usb wi-?fi|stick|adapter|network card|bluetooth|webcam|device|plugged",
                  r"not work|doesn'?t work|down|no connect|reconnect|loop|missing|dead|firmware|not detected|nu merge"],
        "action": "firmware",
        "run": "fieldkit firmware check",
        "proves": "which plugged-in device's driver failed to load its firmware (kernel log), even when the device has "
                  "reset off the bus since; whether that driver is built into the kernel (fix: CONFIG_EXTRA_FIRMWARE) or "
                  "a module (fix: the file in /lib/firmware)",
        "look": ["dmesg | grep 'Direct firmware load'", "lsusb (a stick that resets may be missing between tries)",
                 "/lib/modules/$(uname -r)/modules.builtin.modinfo (what built-in drivers need)"],
        "last_fix": "2026-10-22: ALFA AWUS036NH (148f:3070), rt2800usb built in, rt2870.bin never built in nor on disk; "
                    "not rebuilt (next kernel build carries it: Debian.Kernel.Work/CLAUDE.md 'NEXT KERNEL BUILD')",
        "then": "built-in driver: add the file to the next kernel's CONFIG_EXTRA_FIRMWARE; module: put it in /lib/firmware; "
                "plug the device in again and run the check",
    },
    {
        "id": "desktop-leftovers",
        "seen": "A Linux desktop that has had other desktops, virtual machines or many programs installed and removed: "
                "things start at login that should not, menu entries open nothing, two copies of something run, or the "
                "person wants a clean install back without reinstalling.",
        "example": "I tried KDE and XFCE and some VMs, now there is junk starting at login and dead icons in the menu",
        "words": [r"leftover|remnant|orphan|residu|junk|crap|clutter|dead (icon|entr|launcher)|duplicate|conflict|"
                  r"r[aă]m[aă]?[sș]i[tț]|resturi|gunoi",
                  r"launcher|autostart|login|start ?up|menu|desktop|package|uninstall|removed|vm|virtual|de\b|kde|xfce"],
        "action": "leftovers",
        "run": "fieldkit leftovers scan",
        "proves": "every removed-not-purged package (and which of its files still start at login), login launchers that "
                  "start one program twice or a program that is gone, VM guest helpers on real hardware, dead menu "
                  "entries and user services, dead login-screen sessions; masks, TryExec self-hides, OnlyShowIn and "
                  "DesktopNames honoured so the running desktop's own parts are never listed",
        "look": ["dpkg -l | grep '^rc'", "ls /etc/xdg/autostart ~/.config/autostart ~/.local/share/applications",
                 "systemd-detect-virt (none = a VM helper has nothing to do here)"],
        "last_fix": "2026-10-22 on the VAIO: 10 rc packages, spice-vdagent on real hardware, desktop-base's KDE copy of "
                    "xdg-user-dirs, 2 dead menu entries (an old nvm path, Telegram from Downloads)",
        "then": "fieldkit leftovers clean shows the plan; --apply copies everything to ~/Agents.Work.Trash first, "
                "changes only what starts at the next login, and checks login safety; restore undoes a run",
    },
    {
        "id": "theme-scrambled-after-login",
        "seen": "After a restart or login, a dark GTK theme that worked yesterday is gone: Files and Settings come up "
                "light grey or white, some icons missing, while the terminal still looks right.",
        "example": "after I logged in the theme is light again, files and settings are white, it got scrambled",
        "words": [r"login|log in|logged|restart|reboot|boot|start ?up|this morning|today|repornit|pornit",
                  r"theme|light|white|grey|gray|scrambl|regress|broken|went back|reverted|tem[aă]|alb|deschis"],
        "action": "gtk-theme",
        "run": "fieldkit gtk-theme check",
        "proves": "whether ~/.config/gtk-*/gtk.css holds the theme block more than once or with BEGIN/END markers that "
                  "do not pair up (two writers at once), and which login launchers start the same program at the same "
                  "instant, with the package that owns each and its dpkg state (rc = removed, leftover kept)",
        "look": ["journalctl --user -b | grep -i theme  (cp: ... gtk.css.new: File exists = two runs collided)",
                 "ls /etc/xdg/autostart ~/.config/autostart; dpkg -l | grep '^rc'",
                 "wc -l ~/.config/gtk-4.0/gtk.css: exactly double the clean block = two copies interleaved"],
        "last_fix": "2026-10-22: black-gorilla-theme.desktop and a leftover gtk-high-contrast-theme.desktop (old "
                    "package, state rc, Exec through a symlink to the same tool) both ran --apply-once; both files "
                    "came out doubled (318/158 lines). Files quarantined, tool run once, old package purged, tool "
                    "26.10.22-14-03 writes under flock with a mktemp name (old 40 of 40 races broke, new 0 of 40)",
        "then": "quarantine the scrambled file, run the theme tool once, purge the leftover package, check again, "
                "restart the apps it lists",
    },
    {
        "id": "only-on-my-profile",
        "seen": "Something is wrong on the browser as the person uses it but right on a fresh or test profile: an "
                "engine, a setting, a page or a color the build does not have, Linux or Windows.",
        "example": "my browser still offers Perplexity but a fresh profile shows only Google",
        "words": [r"my (browser|profile)|real profile|old profile|profilul meu|browserul meu|on my|la mine|still offers|still shows",
                  r"fresh|new profile|test profile|clean|profil nou|only google|works on|merge pe"],
        "action": "profile-diff",
        "run": "build-harness profile-diff <task> probe=<the probe that measures it> profile=<the real profile dir>",
        "proves": "the same probe on a fresh profile and on a COPY of the real one (never the real one): the lines "
                  "only the real profile shows are what the profile carries, not the build",
        "look": ["build-harness profile-check <task> (which profile the launcher opens, which version last ran it)",
                 "fieldkit/buildh/probes/ (search-engines, ui-contrast, settings-groups ...: pick the one that measures it)",
                 "post-install runs the search rows on a fresh profile AND a copy of the kept profile"],
        "last_fix": "D-158-13 (2026-10-18): SearchService stores only AppProvidedConfigEngine ids ^google(-|$); a 157 "
                    "profile had brought Perplexity, Bing, DuckDuckGo, Wikipedia and eBay back",
        "then": "fix it in the build (lock or refuse what the profile brings) or move the person with profile-fresh; "
                "never edit the profile by hand",
    },
]


def match(text):
    """-> the entries whose word groups ALL occur in `text`, in any order (best first: most distinct words hit).
    A group is one regex of alternatives: 'what' (dialog, icon, video) and 'how it is wrong' (cut, old, drops)."""
    t = " ".join(str(text).lower().split())
    scored = []
    for e in SYMPTOMS:
        found = [{m.group(0) for m in re.finditer(g, t)} for g in e["words"]]
        if all(found):
            scored.append((sum(len(f) for f in found), e))
    return [e for _, e in sorted(scored, key=lambda x: -x[0])]


def command(run, self_cmd="fieldkit", python=None):
    """The entry's command with the full path of its program (directives 12b: it runs from any folder)."""
    if run.startswith("build-harness"):
        return f"{self_cmd} {run}"
    if run.startswith("fieldkit "):
        return self_cmd + run[len("fieldkit"):]
    return run.replace("{python}", python or sys.executable)


def lines(entries, self_cmd="fieldkit", python=None):
    out = []
    for e in entries:
        out += [f"SYMPTOM {e['id']}: {e['seen']}",
                f"  RUN FIRST: {command(e['run'], self_cmd, python)}",
                f"  it proves: {e['proves']}"]
        out += [f"  look at: {x}" for x in e["look"]]
        if e["last_fix"]:
            out.append(f"  last time: {e['last_fix']}")
        if e["then"]:
            out.append(f"  then: {e['then']}")
    return out
