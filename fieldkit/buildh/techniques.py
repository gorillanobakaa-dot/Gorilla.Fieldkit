"""Gorilla's techniques: the IDEA behind a fix, so it survives a Firefox whose code no longer matches the patch.

A patch says "these lines, here". When Mozilla rewrites the file, the patch fails and the reason it existed is easy
to lose. A technique says what problem exists, what the fix does in words, where to look in ANY version of the
tree (signals: searches that still find the pattern after renames and moves), how to apply it, and how a build
proves it. The source carries a `GORILLA TECHNIQUE <id>` comment at every place a technique is applied, so the
next reader of the code finds the reason next to the change.

    fieldkit build-harness techniques <task>          scan the tree: which techniques hold, what is new or unguarded
    fieldkit build-harness techniques <task> --json

The build gate runs the same scan (row "every recorded technique holds in the tree"), so a port that loses one of
these, or a Firefox that adds a new waiter or download path, is stopped before anything is compiled.

Signals are searches over the TREE (git grep, tests and the defining modules excluded):
  must_have   text that must be present (the lock in place)               missing  -> FAIL
  must_lack   text that must be absent (the thing we removed)             present  -> FAIL
  watch       a pattern that marks a place the technique may have to be applied. Each hit counts as GUARDED when a
              `GORILLA` comment naming the technique, or a PHYSICAL LOCK early return, is within `guard_lines`
              lines above it; an UNGUARDED hit is a FAIL: a human (or a model) must look at it and either apply the
              technique there or record why it does not apply (`allow`, with the reason).
"""
import re
import subprocess
from pathlib import Path

TECHNIQUES = [
    {
        "id": "T-157-31-A",
        "title": "No page or feature waits on the experiment system",
        "decision": "D-157-31",
        "found": "2026-10-03, build 19: black new tab and start page",
        "problem": (
            "Gorilla never starts Normandy, and in Firefox 157 Normandy's init is the only caller of "
            "ExperimentAPI.init(), so Nimbus (the experiment system) never starts. Firefox code that AWAITS Nimbus "
            "readiness before doing its job therefore waits for ever. The new tab and start page awaited "
            "NimbusFeatures.newtabTrainhop.ready() before building the page: it stayed black (34 elements, no picture)."),
        "concept": (
            "Do not start Nimbus to answer the question; remove the question. Every await on Nimbus readiness on a "
            "path that draws a page or starts a feature is deleted (the feature keeps working on its built-in "
            "defaults). Starting Nimbus 'just to say nothing new' was tested and works, but keeps the experiment "
            "system alive, which D-157-31 forbids."),
        "apply": [
            "List the waiters: `fieldkit build-harness techniques <task>` (signal 'nimbus-waiters').",
            "For each one on a page or feature start path: remove the awaited promise (from Promise.all, or the await "
            "line), keep the rest; add a `GORILLA TECHNIQUE T-157-31-A` comment with the reason.",
            "If the waiter only exists to receive remote configuration, cut that receiver too (T-157-31-B).",
            "Never re-enable Normandy or call ExperimentAPI.init() to make a waiter return.",
            "List the starters (signal 'nimbus-starters'): every ExperimentAPI.init() outside Nimbus itself gets a "
            "PHYSICAL LOCK early return at the top of its function (157: AboutWelcomeParent.waitForNimbusForAboutWelcome, "
            "DefaultLaunchOnLogin.waitForNimbusReady, BackgroundTasksUtils.enableNimbus, Normandy.init).",
        ],
        "verify": [
            "visual RT-CONTENT: about:newtab and about:home show the Gorilla logo and the search box",
            "decision D-157-31 checks (tree_lacks nimbusFeature.ready() in AboutNewTab.sys.mjs)",
            "leak gate: no new connection",
        ],
        "signals": [
            {"name": "newtab-does-not-wait", "kind": "must_lack", "path": "browser/modules/AboutNewTab.sys.mjs",
             "text": "nimbusFeature.ready()"},
            {"name": "normandy-lock-explains-nimbus", "kind": "must_have", "path": "toolkit/components/normandy/Normandy.sys.mjs",
             "text": "GORILLA TECHNIQUE T-157-31-A"},
            {"name": "nimbus-waiters", "kind": "watch",
             "pathspec": ["browser", "toolkit", "services", ":!toolkit/components/nimbus", ":!**/test/**", ":!**/tests/**"],
             # 2026-10-08: two more forms that hung pages - the store's own ready() (about:support's Normandy
             # section, written over two lines) and Normandy's RecipeRunner.initializedPromise (about:studies)
             "regex": r"NimbusFeatures(\.[A-Za-z_]+|\[[^]]+\])\.ready\(\)|ExperimentAPI\.ready\(\)"
                      r"|ExperimentAPI\.manager\.store(\s*$|\.ready\(\))|RecipeRunner\.initializedPromise",
             "guard_lines": 40,
             # known waiters that do not gate a page in Gorilla, each with the reason
             "allow": {}},
            {"name": "nimbus-starters", "kind": "watch",
             "pathspec": ["browser", "toolkit", "services", ":!toolkit/components/nimbus", ":!**/test/**", ":!**/tests/**"],
             "regex": r"ExperimentAPI\.init\(", "guard_lines": 20,
             # Normandy's own call is dead code below its PHYSICAL LOCK early return (found by the function rule)
             "allow": {}},
        ],
    },
    {
        "id": "T-157-31-B",
        "title": "No remotely delivered code or configuration (train-hop and its successors)",
        "decision": "D-157-31",
        "found": "2026-10-03, reading what the new tab waited for",
        "problem": (
            "Firefox 157's new tab can be replaced after shipping: a Nimbus enrollment names an XPI that is "
            "downloaded from browser.newtabpage.trainhopAddon.xpiBaseURL (https://archive.mozilla.org/pub/"
            "system-addons/newtab/) and installed over the built-in page; the same enrollments (trainhopConfig) "
            "change section order, ad placement, layout and widgets, and write DEFAULT-branch prefs."),
        "concept": (
            "The browser you install is the browser you run: every path that downloads or installs replacement code, "
            "or applies remote configuration, returns at once with a PHYSICAL LOCK, and its URL pref is \"\" and "
            "locked. Whatever Mozilla renames it to next, the shape is the same: a remote payload names code or "
            "settings, and the client applies it."),
        "apply": [
            "Cut the download/install entry points (today: updateTrainhopAddonState, _installTrainhopAddon, "
            "firstStartupNewProfile in AboutNewTabResourceMapping.sys.mjs).",
            "Make the remote configuration empty at the point it is computed (today: PrefsFeed._getTrainhopConfig).",
            "Blank and lock the download base URL pref in firefox.js (edit the line in place).",
            "Search the new tree for the watch signal and for new names of the same idea.",
        ],
        "verify": ["decision D-157-31 checks", "leak gate: no request to archive.mozilla.org"],
        "signals": [
            {"name": "trainhop-update-locked", "kind": "must_have", "path": "browser/components/newtab/AboutNewTabResourceMapping.sys.mjs",
             "text": "PHYSICAL LOCK (D-157-31): no train-hop"},
            {"name": "trainhop-install-locked", "kind": "must_have", "path": "browser/components/newtab/AboutNewTabResourceMapping.sys.mjs",
             "text": "PHYSICAL LOCK (D-157-31): never download or install a newtab XPI"},
            {"name": "trainhop-config-empty", "kind": "must_have", "path": "browser/extensions/newtab/lib/PrefsFeed.sys.mjs",
             "text": "PHYSICAL LOCK (D-157-31): nothing sent by Mozilla reconfigures the new tab"},
            {"name": "trainhop-url-blank", "kind": "must_have", "path": "browser/app/profile/firefox.js",
             "text": 'pref("browser.newtabpage.trainhopAddon.xpiBaseURL", "", locked);'},
            {"name": "remote-addon-installs", "kind": "watch",
             "pathspec": ["browser/components/newtab", "browser/extensions/newtab/lib", ":!**/test/**", ":!**/tests/**"],
             "regex": r"getInstallForURL|installTemporaryAddon|xpiDownloadURL\s*\)|\.install\(\s*\)",
             "guard_lines": 60, "allow": {}},
        ],
    },
    {
        "id": "T-157-31-C",
        "title": "A lock must not switch off the built-in copy of the data",
        "decision": "D-157-31",
        "found": "2026-10-03, a blurry search-engine placeholder on the fixed new tab",
        "problem": (
            "The Remote Settings lock points SERVER_URL at Mozilla's dummy URL. Upstream uses the data packaged in "
            "the binary (dumps) only when SERVER_URL is the production server, so every dump was silently ignored: "
            "no search-engine icons, no URL-decoration list, no password rules. Nothing leaked; features did less."),
        "concept": (
            "After locking any remote source, find what else reads its URL or state and make sure the local, "
            "packaged copy is still used. Built-in data is part of the binary, so using it never conflicts with "
            "D-157-31."),
        "apply": ["Utils.LOAD_DUMPS returns true (services/settings/Utils.sys.mjs).",
                  "Re-check every reader of SERVER_URL / a locked URL pref after a port."],
        "verify": ["decision D-157-31 tree_contains check", "no 'Unable to find the attachment' at start-up",
                   "visual: the search box shows the engine's icon, not search-engine-placeholder.png"],
        "signals": [
            {"name": "dumps-always-loaded", "kind": "must_have", "path": "services/settings/Utils.sys.mjs",
             "text": "the data built into this binary is always used"},
            {"name": "readers-of-the-locked-url", "kind": "watch",
             "pathspec": ["services/settings", ":!**/test/**", ":!**/tests/**"],
             "regex": r"REMOTE_SETTINGS_SERVER_URLS\.includes\(", "guard_lines": 8,
             "allow": {"services/settings/Utils.sys.mjs":
                       "allowServerURL (may a pref override the server?): unused, the locked SERVER_URL getter never reads it"}},
        ],
    },
]

TECHNIQUES.append({
    "id": "T-157-16-A",
    "title": "Decide local-network access before the packet leaves",
    "decision": "D-157-16",
    "found": "2026-10-04, release leak gate on build 20: a public page knocked on 192.168.0.1 and 10.0.0.1",
    "problem": (
        "Firefox 157 checks local network access (AllowedToConnectToIpAddressSpace) only after TCP has connected. "
        "For a LAN address where nothing answers, the SYN has already left: no data is read, but a site can time "
        "the answers and map a home network."),
    "concept": (
        "Where the resolved addresses are known and no socket exists yet, apply Firefox's own permission decision; "
        "refuse with the same error the late check uses (NS_ERROR_LOCAL_NETWORK_ACCESS_DENIED) so the prompt-and-replay "
        "path still works. Every code path that creates an HTTP socket needs it."),
    "apply": [
        "Find every socket creation for HTTP (signal 'socket-creators'): today DnsAndConnectSocket::TransportSetup::"
        "SetupStreams (applied) and ConnectionEstablisher (Happy Eyeballs, off in release builds).",
        "Before the transport is created: if every address is local or private and the transaction may not reach that "
        "address space, return NS_ERROR_LOCAL_NETWORK_ACCESS_DENIED.",
        "If Mozilla turns Happy Eyeballs on in a release, apply the same check in ConnectionEstablisher before "
        "CreateTransport, then remove its allow entry here.",
    ],
    "verify": ["leak gate lan-probe: no socket to 192.168.0.1 or 10.0.0.1 (LAN_POLICY PASS)",
               "a router page opened by the user still loads (top-level navigation is not public-to-private)"],
    "signals": [
        {"name": "pre-connect-check", "kind": "must_have", "path": "netwerk/protocol/http/DnsAndConnectSocket.cpp",
         "text": "PHYSICAL LOCK (D-157-16): the local-network check runs BEFORE the connection"},
        {"name": "proxy-path-check", "kind": "must_have", "path": "netwerk/protocol/http/nsHttpChannel.cpp",
         "text": "PHYSICAL LOCK (D-157-16): local network access through a proxy"},
        {"name": "socket-creators", "kind": "watch",
         "pathspec": ["netwerk/protocol/http", ":!**/test/**", ":!**/tests/**"],
         "regex": r"(sts|STS)->Create(Routed)?Transport\(", "guard_lines": 70,
         "allow": {"netwerk/protocol/http/ConnectionEstablisher.cpp":
                   "Happy Eyeballs path: network.http.happy_eyeballs_enabled is @IS_NIGHTLY_BUILD@, off in release builds; "
                   "apply T-157-16-A here when Mozilla enables it"}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-01-A",
    "title": "Nothing about updates is written outside the profile",
    "decision": "D-157-01",
    "found": "2026-10-04, release leak gate on build 21: Settings wrote ProgramData/Mozilla-<id>/updates/<hash>/update-config.json",
    "problem": ("Per-installation update settings live in a machine-wide file under ProgramData, written when Settings "
                "opens, although Gorilla has no updater (--disable-updater)."),
    "concept": ("With no updater, the update setting is an ordinary profile pref: UpdateUtils.PER_INSTALLATION_PREFS_SUPPORTED "
                "is false, so nothing is read from or written to the machine-wide update folder."),
    "apply": ["Keep PER_INSTALLATION_PREFS_SUPPORTED false in UpdateUtils (or its successor).",
              "Re-check every writer of the update root (signal 'update-root-writers') in a new Firefox."],
    "verify": ["leak gate FILESYSTEM_POLICY: no file under ProgramData/Mozilla-*", "decision D-157-01 checks"],
    "signals": [
        {"name": "per-install-off", "kind": "must_have", "path": "toolkit/modules/UpdateUtils.sys.mjs",
         "text": "PHYSICAL LOCK (D-157-01): nothing about updates is written outside the profile"},
        {"name": "update-root-writers", "kind": "watch",
         "pathspec": ["toolkit/modules", "browser/components/preferences", ":!**/test/**", ":!**/tests/**"],
         "regex": r"writeUpdateConfig\(", "guard_lines": 60,
         "allow": {"toolkit/modules/UpdateUtils.sys.mjs":
                   "the writer itself; unreachable while PER_INSTALLATION_PREFS_SUPPORTED is false (checked by per-install-off)"}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-32-A",
    "title": "A menu Gorilla adds is drawn in the Gorilla colours by a rule scoped to it",
    "decision": "D-157-32",
    "found": "2026-10-04, build 23: the Gorilla.Satellite toolbar menu showed cyan text on system grey (contrast 1.25:1)",
    "problem": ("The Gorilla theme forces EVERY menupopup and menuitem to the system colours with !important "
                "(master-redirect.css 'PANEL STYLING RESTORED': menupopup {appearance:auto; background-color:Menu; "
                "color:MenuText}). A menu owned by a toolbar button inherits the toolbar's cyan text, so it ends up "
                "cyan on the system's light grey."),
    "concept": ("Never theme menus globally. Give each menu Gorilla adds its own rule, scoped by the owner's id "
                "('#owner > menupopup'), with !important on appearance:none, the background, the text colour "
                "(var(--toolbar-text-color), the theme's cyan), the hover and the separators, so it beats the theme's "
                "global rule."),
    "apply": ["For a new Gorilla menu, copy the '#gorilla-satellite-button > menupopup' block in toolbarbuttons.css "
              "under the new owner's id.",
              "Prove it with fieldkit build-harness ui-check (contrast of every item against what is painted)."],
    "verify": ["ui-check: UI rule UI-MENU, and every menu item at contrast 4.5:1 or more"],
    "signals": [
        {"name": "scoped-menu-rule", "kind": "must_have", "path": "browser/themes/shared/toolbarbuttons.css",
         "text": "background-color: #000000 !important;"},
        {"name": "theme-forces-system-menus", "kind": "must_have", "path": "browser/themes/shared/master-redirect.css",
         "text": "background-color: Menu !important;"},
        {"name": "gorilla-menus", "kind": "watch", "pathspec": ["browser/modules", ":!**/test/**", ":!**/tests/**"],
         "regex": r"createXULElement\([[:space:]]*.menupopup", "guard_lines": 3,
         "allow": {"browser/modules/webrtcUI.sys.mjs": "upstream camera/microphone sharing menu: system text on the "
                   "system menu colour, readable as the theme intends (it inherits no toolbar cyan)"}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-32-B",
    "title": "Hover text covers the whole Settings row, not the 16-pixel radio circle",
    "decision": "D-157-32",
    "found": "2026-10-04: Gorilla.Satellite mode's long explanations never appeared on hover in Settings",
    "problem": ("The moz-* input widgets (moz-radio, moz-checkbox) treat `title` as a 'mapped' property: it is "
                "removed from the host and put on the inner <input> only, which nobody hovers."),
    "concept": "MozBaseInputElement.render() also puts the title on the label wrapper, so the label and description show it.",
    "apply": ["Keep title=${ifDefined(this.title)} on the .label-wrapper span in lit-utils.mjs (or its successor).",
              "Settings items that want hover text pass controlAttrs {'data-l10n-attrs': 'title'} and a .title in the FTL."],
    "verify": ["probe satellite-settings: 'hover over row' text present for every option"],
    "signals": [
        {"name": "row-title", "kind": "must_have", "path": "toolkit/content/widgets/lit-utils.mjs",
         "text": '<span class="label-wrapper" title=${ifDefined(this.title)}>'},
        {"name": "mapped-title", "kind": "watch", "pathspec": ["toolkit/content/widgets/lit-utils.mjs"],
         "regex": r"title: \{ type: String, mapped: true \}", "guard_lines": 200, "allow": {}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-32-C",
    "title": "Gorilla code reaches a window through ownerDocument.defaultView, never ownerGlobal",
    "decision": "D-157-32",
    "found": ("2026-10-04, build 23: the satellite menu's handler threw and showed every item; the same mistake in "
              "the FF155 theme fix THEME_FIX_LOG 39 (autocomplete popup height) had been throwing unnoticed"),
    "problem": ("Node.ownerGlobal is a chrome-only shortcut that this Firefox 157 tree's WebIDL does not define: it is "
                "undefined, so `x.ownerGlobal.anything` throws at the moment it runs, not at load."),
    "concept": "Use the standard ownerDocument.defaultView. uicheck rule UI-API checks every line Gorilla added.",
    "apply": ["Replace ownerGlobal with ownerDocument.defaultView in Gorilla code.",
              "If a future tree defines ownerGlobal again, UI-API stops flagging it by itself (it reads the WebIDL)."],
    "verify": ["ui-check: UI rule UI-API; the probe's 'no script error while menus and Settings open'"],
    "signals": [
        {"name": "satellite-menu-window", "kind": "must_have", "path": "browser/modules/GorillaLinkMode.sys.mjs",
         "text": "const win = () => button.ownerDocument.defaultView;"},
        {"name": "autocomplete-window", "kind": "must_have", "path": "toolkit/content/widgets/autocomplete-popup.js",
         "text": "new this.ownerDocument.defaultView.ResizeObserver("},
        {"name": "owner-global", "kind": "watch", "pathspec": ["browser/modules/Gorilla*", ":!**/test/**"],
         "regex": r"\.ownerGlobal", "guard_lines": 3, "allow": {}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-33-A",
    "title": "Per-site choices are made before the page is requested, per tab",
    "decision": "D-157-33",
    "found": "2026-10-04: satellite mode's mobile identity, per-site desktop version and no-JavaScript",
    "problem": ("A per-site choice applied after the page arrived (reload on location change) downloads the page twice, "
                "which is what a 5 KB/s link cannot afford."),
    "concept": ("An http-on-modify-request observer, registered only while a level needs it, looks at every top-level "
                "document request and sets the tab's BrowsingContext.customUserAgent and allowJavascript from the "
                "site's permission (gorilla-desktop-site, gorilla-javascript-site) before the request leaves. Leaving "
                "the level resets every open tab. javascript.enabled is never touched, so browser pages keep working."),
    "apply": ["Keep the observer, the two permission types and _resetTabs in GorillaLinkMode.sys.mjs.",
              "Prove with probe satellite-mobile against a local echo page."],
    "verify": ["probe satellite-mobile: identity and JS per level, per-site switch, reset after Off"],
    "signals": [
        {"name": "per-site-js", "kind": "must_have", "path": "browser/modules/GorillaLinkMode.sys.mjs",
         "text": 'const JS_SITE_PERM = "gorilla-javascript-site";'},
        {"name": "per-site-desktop", "kind": "must_have", "path": "browser/modules/GorillaLinkMode.sys.mjs",
         "text": 'const DESKTOP_SITE_PERM = "gorilla-desktop-site";'},
        {"name": "tabs-reset", "kind": "must_have", "path": "browser/modules/GorillaLinkMode.sys.mjs",
         "text": "_resetTabs(mobile, noJs) {"},
        {"name": "per-site-observer", "kind": "watch", "pathspec": ["browser/modules", ":!**/test/**", ":!**/tests/**"],
         "regex": r"http-on-modify-request", "guard_lines": 400,
         "allow": {"browser/modules/ASWebAuthSessionService.sys.mjs": "upstream macOS web-authentication session: it "
                   "watches its own login requests, no per-site page choice",
                   "browser/modules/GorillaLinkMode.sys.mjs": "the per-site observer itself (T-157-33-A); a new "
                   "observer anywhere else in browser/modules must be looked at"}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-34-A",
    "title": "A generated upstream file is changed only by a GORILLA block appended at its end",
    "decision": "D-157-04",
    "found": ("2026-10-07, build 28: the three design-token files (DO NOT EDIT, generated by mach buildtokens) were "
              "155-era copies forced onto 157 by the 16.SNAPSHOT ports"),
    "problem": ("Editing a generated file in place, or replacing it with an older version's copy, survives every check "
                "that looks for text: the file still parses enough to load. In 157 the token copies had a renamed "
                "layer, two dropped @media/@layer openers (Nova applied unconditionally, above the contrast layers), "
                "--color-gray-0 renamed (16 files use it) and about 40 tokens deleted; Settings and Add-ons lost "
                "button padding and page spacing, and nothing noticed for five builds."),
    "concept": ("Upstream's generated file stays byte-identical to pristine. Gorilla's values go in ONE block at the "
                "end, starting with `/* GORILLA`, layered so the cascade does the overriding. A new Firefox then "
                "needs no merge: its new file plus the same block. Probe design-tokens shows what the browser really "
                "computes, before and after."),
    "apply": ["Restore the file from pristine (git show <root>:<path>), append the GORILLA block, record both steps.",
              "Prove with probe design-tokens (omni= the new files) against the installed build: every changed or "
              "lost token must be intended."],
    "verify": ["this signal in the build gate and check-change", "probe design-tokens before/after"],
    "signals": [
        {"name": "generated-files-append-only", "kind": "append_only", "header": "DO NOT EDIT"},
    ],
})

TECHNIQUES.append({
    "id": "T-157-35-A",
    "title": "One master Gorilla logo, linked everywhere; never drawn tiny",
    "decision": "D-157-35",
    "found": ("2026-10-07, build 28: the logo shipped as 11 files; an SVG wrapping a 1400 px PNG cost 12-21 MB per "
              "process, and the artwork was drawn at 24 px beside page titles"),
    "problem": ("Every different file that holds the Gorilla is decoded and kept on its own, and an SVG that wraps a "
                "raster is decoded at full size in every process that shows it, whatever size it is drawn at. Models "
                "kept adding copies (1x/2x PNGs, private copies, wrappers) because that is what Firefox's own branding "
                "does. Drawn below 64 px the artwork's detail is lost and it only costs decoding."),
    "concept": ("One plain raster master, chrome://branding/content/about-logo.png (1400 px, Lanczos-made from the "
                "canonical 2598 px master): one cache entry per process, decoded at the size each page draws it. "
                "--gorilla-master-icon names it. Nowhere is it drawn smaller than 64 CSS px; the 16-128 px icon "
                "ladder (iconNN.png) is the only small Gorilla, where an application icon is required."),
    "apply": ["Point every reference at about-logo.png (an image-set 1x/2x becomes the one url).",
              "Remove the artwork where it is drawn tiny (visual rule RT-TINYLOGO lists every place, with its size).",
              "Never add another file holding the artwork; never wrap a raster in an SVG.",
              "Measure with probe image-memory before and after; compare the probe's pictures."],
    "verify": ["this technique in the build gate and check-change", "visual RT-TINYLOGO", "probe image-memory",
               "owner gate check_logo_provenance.py (ICON-002, ASSET-002) on about-logo.png"],
    "signals": [
        {"name": "master-is-the-png", "kind": "must_have", "path": "browser/themes/shared/master-redirect.css",
         "text": '--gorilla-master-icon: url("chrome://branding/content/about-logo.png");'},
        {"name": "no-page-title-logo", "kind": "must_have", "path": "toolkit/content/widgets/moz-page-nav/moz-page-nav.css",
         "text": "GORILLA D-157-35: no 24 px Gorilla beside the page title"},
        {"name": "retired-logo-files", "kind": "watch", "pathspec": ["browser", "toolkit", "docshell", ":!**/test/**",
                                                                     ":!**/tests/**", ":!*.md"],
         "regex": r"chrome://branding/content/(about-logo\.svg|about-logo@2x|about-logo-private|about\.svg|about\.png)",
         "guard_lines": 0, "allow": {}},
    ],
})

TECHNIQUES.append({
    "id": "T-157-35-B",
    "title": "No small Gorilla icon drawn by the browser (tab icons, address bar, rows, dialogs)",
    "decision": "D-157-35",
    "found": "2026-10-08: the 16-128 px icon ladder still drew a tiny Gorilla in tabs, the address bar and lists",
    "problem": ("The small icon files (iconNN.png, document.ico, document_pdf.svg) are the Gorilla shrunk to 16-48 px: "
                "the artwork is lost at that size and each one is another picture to decode. Firefox names them as tab "
                "icons, the address-bar chip of browser pages, process rows, dialogs and handler rows."),
    "concept": ("Inside the browser the Gorilla is drawn large or not at all. Tab icons and page favicons name no "
                "Gorilla; the address-bar chip shows the label only; rows and dialogs that need a symbol use Firefox's "
                "neutral ones (info, reload, pdf, page, folder). The ladder stays for Windows (the program and its file "
                "types use the .ico files) and in the places listed in `allow`, each with its reason."),
    "apply": ["Remove the name (a <link rel=icon>, a map entry) or swap in a neutral chrome://global/skin/icons/ symbol.",
              "A new use found by this watch: remove it, or add it to allow with the reason it is never drawn small.",
              "Prove with probe small-gorilla (tab icons and the chip) and visual RT-TINYLOGO."],
    "verify": ["this technique in the build gate and check-change", "probe small-gorilla", "visual RT-TINYLOGO"],
    "signals": [
        {"name": "chip-hidden", "kind": "must_have", "path": "browser/themes/shared/identity-block/identity-block.css",
         "text": "GORILLA D-157-35: no 16 px Gorilla in the address bar on browser pages"},
        {"name": "small-gorilla-icons", "kind": "watch",
         "pathspec": ["browser", "toolkit", ":!**/test/**", ":!**/tests/**", ":!*.md", ":!**/*.stories.mjs", ":!**/storybook/**"],
         "regex": r"chrome://branding/content/(icon(16|32|48|64|128)\.png|document\.ico|document_pdf\.svg)",
         "guard_lines": 0,
         "allow": {
             "browser/components/asrouter/modules/InfoBar.sys.mjs": "messaging-system infobars: Gorilla never shows them",
             "browser/components/asrouter/modules/PanelTestProvider.sys.mjs": "test messages for the messaging system",
             "browser/components/backup/BackupService.sys.mjs": "written into a backup archive file, not drawn by the browser",
             "browser/components/shell/CustomIconManager.sys.mjs": "the custom application icon chooser's 64 px preview",
             "browser/components/urlbar/UrlbarProviderCalculator.sys.mjs": "Mozilla's calculator easter egg (left for the Mozilla-artwork sweep)",
             "browser/components/urlbar/content/UrlbarView.mjs": "Mozilla's calculator easter egg sprite (left for the Mozilla-artwork sweep)",
             "browser/components/urlbar/UrlbarProviderQuickSuggestContextualOptIn.sys.mjs": "Firefox Suggest opt-in: Suggest is off",
             "toolkit/mozapps/extensions/content/components/addon-mlmodel-details.mjs": "ML model cards: ML is off (D-157-21)",
             "toolkit/mozapps/extensions/content/components/mlmodel-card-list-additions.mjs": "ML model cards: ML is off (D-157-21)",
             "toolkit/themes/osx/global/wizard.css": "128 px wizard header, macOS",
             "toolkit/themes/windows/global/wizard.css": "128 px wizard header",
         }},
    ],
})

GUARD = re.compile(r"GORILLA (TECHNIQUE|UNLEASHED)")


def _append_only(workdir, header):
    """Files upstream marks `header` that the tree changed against pristine: each must be pristine, byte for byte,
    followed only by a block that starts with `/* GORILLA`. -> (bad, checked)"""
    git = lambda *a: subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(workdir), *a], capture_output=True)
    root = git("rev-list", "--max-parents=0", "HEAD").stdout.decode().split()
    if not root:
        return [("(tree)", "no pristine root commit")], 0
    changed = [p for p in git("diff", "--name-only", "--diff-filter=M", root[0], "HEAD").stdout.decode("utf-8", "replace").splitlines() if p]
    bad, checked = [], 0
    for path in changed:
        old = git("show", f"{root[0]}:{path}").stdout
        if header.encode() not in old[:2000]:
            continue
        checked += 1
        new = (Path(workdir) / path).read_bytes()
        if not new.startswith(old):
            bad.append((path, "upstream's own text was changed (restore it from pristine; add values in a GORILLA block at the end)"))
        elif new[len(old):].strip() and not new[len(old):].lstrip().startswith(b"/* GORILLA"):
            bad.append((path, "text after upstream's end does not start with /* GORILLA"))
    return bad, checked


def _grep(workdir, regex, pathspec):
    r = subprocess.run(["git", "-C", str(workdir), "grep", "-n", "-E", regex, "--", *pathspec],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in r.stdout.splitlines():
        try:
            path, n, text = line.split(":", 2)
            out.append((path, int(n), text.strip()))
        except ValueError:
            continue
    return out


COMMENT = re.compile(r"^\s*(//|/?\*)")
# "#" starts a comment only where it is one: in CSS it is an id selector, so `#identity-icon { ... }` on one line was
# skipped as a comment and a forbidden name in it went unseen (found 2026-10-08 by test_buildh_techniques_one_logo)
HASH_COMMENT = re.compile(r"^\s*#")
HASH_FILES = (".py", ".sh", ".mn", ".build", ".yaml", ".yml", ".toml", ".properties", ".ftl", ".ini", ".cfg", ".txt",
              ".configure", ".mozbuild")


def _is_comment(path, text):
    return bool(COMMENT.match(text)) or (path.lower().endswith(HASH_FILES) and bool(HASH_COMMENT.match(text)))
KEYWORDS = {"if", "for", "while", "switch", "catch", "with", "return", "function"}
HEADER = re.compile(r"^(\s*)(?:export\s+)?(?:async\s+|static\s+|get\s+|set\s+)*(?:function\s*\*?\s*)?([#$\w]+)\s*\((?:[^;]*\)\s*\{|\{?)\s*$")


def _guarded(workdir, path, n, tid, span):
    """A hit is guarded when the technique (or a Gorilla PHYSICAL LOCK) is named within `span` lines above it, or
    when it sits inside a function whose first lines are an early return (`if (true)`) named PHYSICAL LOCK or by the
    technique (the body below is dead)."""
    try:
        lines = (Path(workdir) / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    above = lines[max(0, n - 1 - span):n - 1]
    if any(tid in l or ("PHYSICAL LOCK" in l and GUARD.search(l)) for l in above):
        return True
    ind = len(lines[n - 1]) - len(lines[n - 1].lstrip())
    for i in range(n - 2, max(-1, n - 2 - 1500), -1):
        m = HEADER.match(lines[i])
        if m and m.group(2) not in KEYWORDS and len(lines[i]) - len(lines[i].lstrip()) < ind:
            head = lines[i + 1:i + 20]                    # room for a multi-line signature
            # the early return is named either as a PHYSICAL LOCK or as this technique (2026-10-08: about:support's
            # Normandy section returns at once "GORILLA TECHNIQUE T-157-31-A", its old waiters dead below it)
            if any("PHYSICAL LOCK" in l or tid in l for l in head) and any(l.strip().startswith("if (true)") for l in head):
                return True
            # not locked here: an enclosing function may be (a nested function, or a call such as `done({` that only
            # looks like a header, inside a locked function is dead too)
            ind = len(lines[i]) - len(lines[i].lstrip())
    return False


def scan(workdir):
    """-> [{id, title, ok, signals: [{name, kind, ok, detail, hits}]}]"""
    w = Path(workdir)
    out = []
    for t in TECHNIQUES:
        sigs = []
        for s in t["signals"]:
            if s["kind"] in ("must_have", "must_lack"):
                p = w / s["path"]
                body = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
                if body is None:
                    sigs.append({**_pub(s), "ok": False, "detail": f"{s['path']} does not exist: the code moved; find its new home"})
                    continue
                has = s["text"] in body
                ok = has if s["kind"] == "must_have" else not has
                sigs.append({**_pub(s), "ok": ok, "detail": f"{s['path']}: " + ("in place" if ok else
                             ("the lock is missing" if s["kind"] == "must_have" else "the removed code is back"))})
            elif s["kind"] == "append_only":
                bad, checked = _append_only(w, s["header"])
                sigs.append({**_pub(s), "ok": not bad, "detail": f"{checked} generated file(s) changed, {len(bad)} edited in place"
                             + (f": {bad[0][0]}: {bad[0][1]}" if bad else "")})
            else:
                hits = []
                for path, n, text in _grep(w, s["regex"], s["pathspec"]):
                    if _is_comment(path, text):
                        continue                          # our own rationale text names the pattern
                    why = s.get("allow", {}).get(path)
                    g = _guarded(w, path, n, t["id"], s.get("guard_lines", 20))
                    hits.append({"where": f"{path}:{n}", "text": text[:160],
                                 "state": "guarded" if g else ("allowed: " + why if why else "UNGUARDED")})
                bad = [h for h in hits if h["state"] == "UNGUARDED"]
                sigs.append({**_pub(s), "ok": not bad, "hits": hits,
                             "detail": f"{len(hits)} place(s), {len(bad)} unguarded" + (f": {bad[0]['where']}" if bad else "")})
        out.append({"id": t["id"], "title": t["title"], "decision": t["decision"], "ok": all(x["ok"] for x in sigs),
                    "signals": sigs})
    return out


def _pub(s):
    return {"name": s["name"], "kind": s["kind"]}


def gate_rows(workdir):
    """Rows for compile.gate: one per technique."""
    rows = []
    for r in scan(workdir):
        bad = [s for s in r["signals"] if not s["ok"]]
        rows.append((f"technique {r['id']} holds: {r['title']}", not bad,
                     "all signals hold" if not bad else "; ".join(f"{s['name']}: {s['detail']}" for s in bad)[:300]))
    return rows


def lines(results, verbose=False):
    by = {t["id"]: t for t in TECHNIQUES}
    out = []
    for r in results:
        t = by[r["id"]]
        out.append(f"{'OK  ' if r['ok'] else 'FAIL'} {r['id']} ({r['decision']}): {r['title']}")
        for s in r["signals"]:
            out.append(f"     [{'ok' if s['ok'] else 'FAIL'}] {s['name']} ({s['kind']}): {s['detail']}")
            for h in s.get("hits", []) if (verbose or not s["ok"]) else []:
                out.append(f"          {h['state']:<10} {h['where']}  {h['text'][:100]}")
        if not r["ok"] or verbose:
            out.append(f"     problem: {t['problem']}")
            out.append(f"     concept: {t['concept']}")
            out += [f"     apply {i}. {a}" for i, a in enumerate(t["apply"], 1)]
            out.append(f"     verify: {'; '.join(t['verify'])}")
    return out
