// What does the new tab wait for? (2026-10-03: the black new tab of build 19; it waited for Nimbus for ever)
// Reports whether Activity Stream was built and whether each thing it waits on answered.
const { AboutNewTab } = ChromeUtils.importESModule("resource:///modules/AboutNewTab.sys.mjs");
say("activityStream built:", !!AboutNewTab.activityStream, "initialized:", !!(AboutNewTab.activityStream && AboutNewTab.activityStream.initialized));
const red = Cc["@mozilla.org/network/protocol/about;1?what=newtab"].getService(Ci.nsIAboutModule).wrappedJSObject;
say("built-in newtab add-on initialised:", await race(red.promiseBuiltInAddonInitialized, 5000));
const { ProfileAge } = ChromeUtils.importESModule("resource://gre/modules/ProfileAge.sys.mjs");
say("profile age:", await race(ProfileAge().then(a => a.created), 5000));
try {
  const { NimbusFeatures } = ChromeUtils.importESModule("resource://nimbus/ExperimentAPI.sys.mjs");
  say("Nimbus newtabTrainhop ready (Gorilla: must NOT be needed):", await race(NimbusFeatures.newtabTrainhop.ready(), 5000));
} catch (e) { say("Nimbus not importable:", String(e)); }
