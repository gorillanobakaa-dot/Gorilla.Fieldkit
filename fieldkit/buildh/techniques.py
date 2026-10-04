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
             "regex": r"NimbusFeatures(\.[A-Za-z_]+|\[[^]]+\])\.ready\(\)|ExperimentAPI\.ready\(\)",
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
        {"name": "socket-creators", "kind": "watch",
         "pathspec": ["netwerk/protocol/http", ":!**/test/**", ":!**/tests/**"],
         "regex": r"(sts|STS)->Create(Routed)?Transport\(", "guard_lines": 70,
         "allow": {"netwerk/protocol/http/ConnectionEstablisher.cpp":
                   "Happy Eyeballs path: network.http.happy_eyeballs_enabled is @IS_NIGHTLY_BUILD@, off in release builds; "
                   "apply T-157-16-A here when Mozilla enables it"}},
    ],
})

GUARD = re.compile(r"GORILLA (TECHNIQUE|UNLEASHED)")


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


COMMENT = re.compile(r"^\s*(//|/?\*|#)")
KEYWORDS = {"if", "for", "while", "switch", "catch", "with", "return", "function"}
HEADER = re.compile(r"^(\s*)(?:export\s+)?(?:async\s+|static\s+|get\s+|set\s+)*(?:function\s*\*?\s*)?([#$\w]+)\s*\((?:[^;]*\)\s*\{|\{?)\s*$")


def _guarded(workdir, path, n, tid, span):
    """A hit is guarded when the technique (or a Gorilla PHYSICAL LOCK) is named within `span` lines above it, or
    when it sits inside a function whose first lines are a PHYSICAL LOCK early return (the body below is dead)."""
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
            return any("PHYSICAL LOCK" in l for l in head) and any(l.strip().startswith("if (true)") for l in head)
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
            else:
                hits = []
                for path, n, text in _grep(w, s["regex"], s["pathspec"]):
                    if COMMENT.match(text):
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
