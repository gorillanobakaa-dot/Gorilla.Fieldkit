"""Dispositions: for every network-capable source file in the audit inventory, what stops it from reaching the
network, with the evidence the harness can check. Categories:

  cut-in-source       the file carries a GORILLA PHYSICAL LOCK (the code that would send returns first)
  not-shipped         a JS module absent from both packaged omni.ja archives of the build
  not-built           C++/Rust/tooling the build configuration never compiles (evidence names the flag or excision)
  dead-remote-settings  its only network API is a Remote Settings client, and Remote Settings has no server
                      (services/settings/Utils.sys.mjs PHYSICAL LOCK)
  dead-caller-cut     its only caller is cut in source (evidence names the caller)
  local-only          it only reads resource:, chrome:, file:, blob: or moz-extension: URLs
  user-initiated      the network action happens only when the user asks for it (evidence says how)
  OWNER-DECISION      a trade-off only the owner can make; never approved by a blanket approval
  OPEN                nothing stops it yet; never approvable - it gets cut

A model writes these; the owner approves them, at a real terminal only. There is no approval function in this
module: a chat route (`approve_from_chat`) that skipped the terminal check was removed with the unused `build()`.
"""
import io
import zipfile
from pathlib import Path

REASONS = {
    # not built
    "toolkit/crashreporter/": ("not-built", "mozconfig --disable-crashreporter: the crash reporter, Breakpad and crash submission are not compiled"),
    "toolkit/crashreporter/google-breakpad/src/tools/": ("not-built", "Breakpad developer tools, never part of a Firefox build"),
    "toolkit/components/telemetry/pingsender/": ("not-built", "pingsender excised from DIRS and from package-manifest.in (build 11)"),
    "toolkit/mozapps/extensions/internal/AddonTestUtils.sys.mjs": ("not-built", "test helper, --disable-tests"),
    "services/sync/tps/": ("not-built", "TPS test harness, --disable-tests"),
    "toolkit/components/translations/bergamot-translator/upload-bergamot.py": ("not-built", "maintainer upload script, not part of the build"),
    "toolkit/components/ml/vendor/": ("not-shipped", "toolkit/components/ml/jar.mn emptied (build 6): nothing under chrome://global/content/ml/ is packaged"),
    "toolkit/components/ml/content/": ("not-shipped", "toolkit/components/ml/jar.mn emptied (build 6)"),
    "toolkit/components/ml/actors/": ("not-built", "actors excised from toolkit/components/ml/moz.build DIRS (build 6)"),
    "toolkit/mozapps/update/": ("not-built", "mozconfig --disable-updater: no update service is built"),
    # dead: caller cut / no lists / not initialised
    "toolkit/components/captivedetect/CaptiveDetect.sys.mjs": ("dead-caller-cut", "its only user, CaptivePortalService::Start, returns before starting (netwerk/base/CaptivePortalService.cpp PHYSICAL LOCK)"),
    "dom/push/PushServiceWebSocket.sys.mjs": ("dead-caller-cut", "PushService._changeStateConnectionEnabledEvent forces enabled=false (dom/push/PushService.sys.mjs PHYSICAL LOCK)"),
    "toolkit/mozapps/extensions/internal/ProductAddonChecker.sys.mjs": ("dead-caller-cut", "its callers, GMPInstallManager and the system add-on updater, are cut (PHYSICAL LOCK)"),
    "toolkit/components/url-classifier/UrlClassifierHashCompleter.sys.mjs": ("dead-caller-cut", "gethash only runs for a URL matching a downloaded list prefix; list downloads are cut (nsUrlClassifierStreamUpdater.cpp PHYSICAL LOCK)"),
    "toolkit/components/url-classifier/nsUrlClassifierDBService.cpp": ("dead-caller-cut", "network use is gethash/update through the stream updater, cut in source"),
    "toolkit/components/url-classifier/nsUrlClassifierUtils.cpp": ("dead-caller-cut", "channel classification helpers, no request of its own; list downloads cut"),
    "toolkit/components/glean/src/init/viaduct_uploader.rs": ("dead-caller-cut", "Glean's uploader; FOG::InitializeFOG returns before Glean starts (13.TELEMETRY.KILL)"),
    "toolkit/components/glean/bindings/jog/JOG.cpp": ("local-only", "reads the local JOG metric definitions file"),
    # local
    "browser/extensions/newtab/lib/NewTabGleanUtils.sys.mjs": ("local-only", "fetches a resource:// metrics definition"),
    "browser/extensions/newtab/lib/RemoteRenderer.sys.mjs": ("local-only", "fetches BUNDLED_SCRIPT_URI / BUNDLED_STYLE_URI (packaged); its Remote Settings client has no server"),
    "browser/extensions/newtab/lib/Screenshots.sys.mjs": ("local-only", "fetches file:// thumbnails"),
    "browser/extensions/newtab/lib/cache.worker.js": ("local-only", "XHR to the new tab's own cached page"),
    "toolkit/mozapps/extensions/LightweightThemeManager.sys.mjs": ("local-only", "fetches the manifest of a packaged theme"),
    "toolkit/components/backgroundtasks/BackgroundTasksUtils.sys.mjs": ("local-only", "fetches file: URIs of experiment opt-in data; no background task is scheduled in this build"),
    "browser/components/urlbar/UrlbarUtils.sys.mjs": ("local-only", "fetches blob: and moz-extension: engine icons only"),
    "toolkit/components/translations/bergamot-translator/bergamot-translator.js": ("dead-caller-cut", "the engine is never assembled (TranslationsParent.getTranslationsEnginePayload PHYSICAL LOCK)"),
    "toolkit/components/translations/cld2/cld-worker.js": ("local-only", "language detection worker loading its packaged data"),
    "services/settings/": ("dead-remote-settings", "Remote Settings: Utils.SERVER_URL is the dummy URL and shouldSkipRemoteActivity is true (PHYSICAL LOCK)"),
    # user-initiated
    "toolkit/mozapps/extensions/internal/XPIInstall.sys.mjs": ("user-initiated", "an install the user starts; every non-system install is rejected in source (07.TOOLKIT 'API LOBOTOMY')"),
    "toolkit/components/search/OpenSearchLoader.sys.mjs": ("user-initiated", "only when the user adds a search engine from a page"),
    "toolkit/components/search/SearchUtils.sys.mjs": ("user-initiated", "engine icon/description loads for an engine the user adds"),
    "netwerk/dns/": ("user-initiated", "DNS over HTTPS only when the user enables it (network.trr.mode); no region rollout (Remote Settings/Nimbus dead)"),
    "remote/shared/": ("not-a-sender", "WebDriver network observers; Marionette and the Remote Agent are physically locked (09.REMOTE)"),
    "remote/shared/webdriver/Session.sys.mjs": ("not-a-sender", "WebDriver session; Marionette and the Remote Agent are physically locked (09.REMOTE)"),
    "services/sync/modules/": ("dead-caller-cut", "every Sync request goes through resource.sys.mjs _doRequest, which throws (PHYSICAL LOCK)"),
    "toolkit/components/normandy/": ("dead-caller-cut", "Normandy.init returns first (toolkit/components/normandy/Normandy.sys.mjs PHYSICAL LOCK): no recipe runner, no API call"),
    "toolkit/components/nimbus/lib/RemoteSettingsExperimentLoader.sys.mjs": ("local-only", "fetches resource://nimbus/ schemas; its Remote Settings clients have no server"),
    # owner decisions
    "security/manager/ssl/nsNSSCallbacks.cpp": ("OWNER-DECISION", "OCSP: on a TLS visit Firefox may ask the certificate's CA whether it is revoked, telling the CA which site was visited. CRLite cannot replace it here (its data comes from Remote Settings, which is dead). Off = privacy, no revocation checking; on = revocation, CA sees visits. security.OCSP.enabled is a pref decision."),
    "security/manager/ssl/nsNSSIOLayer.cpp": ("not-a-sender", "the TLS socket layer every HTTPS connection uses"),
    "security/manager/ssl/TLSClientAuthCertSelection.cpp": ("not-a-sender", "client-certificate selection during a TLS handshake"),
}


def shipped_names(install_or_zip):
    """Basenames of every file in both omni.ja archives of a build (dir or zip)."""
    names = set()
    p = Path(install_or_zip)
    if p.is_dir():
        read = lambda rel: (p / rel).read_bytes()
    else:
        z = zipfile.ZipFile(p)
        top = z.namelist()[0].split("/")[0]
        read = lambda rel: z.read(f"{top}/{rel}")
    for ja in ("omni.ja", "browser/omni.ja"):
        with zipfile.ZipFile(io.BytesIO(read(ja))) as o:
            names.update(Path(n).name for n in o.namelist())
    return names


def classify(rel, info, shipped):
    if info.get("physical_lock"):
        return "cut-in-source", "GORILLA PHYSICAL LOCK in the file: the sending code returns first"
    best = None
    for prefix, (cat, why) in REASONS.items():
        if rel == prefix or rel.startswith(prefix):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, cat, why)
    if best:
        return best[1], best[2]
    if set(info.get("apis", [])) <= {"RemoteSettings("}:
        return "dead-remote-settings", "its only network API is a Remote Settings client, and Remote Settings has no server (PHYSICAL LOCK)"
    if rel.endswith((".mjs", ".js")) and Path(rel).name not in shipped:
        return "not-shipped", "absent from both packaged omni.ja archives of the build"
    return "OPEN", "nothing in the tree stops this file from sending yet"

