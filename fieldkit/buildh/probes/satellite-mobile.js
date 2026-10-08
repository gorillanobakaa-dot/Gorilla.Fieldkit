// Gorilla.Satellite mode, mobile pages and the cache (D-157-33): which identity does a site receive (header and
// navigator) at each level, does "This site: desktop version" switch one site back, and are the cache settings
// in place? Needs a local page on 127.0.0.1:8765 that puts navigator.userAgent in its title and logs the header;
// `build-harness probe` starts it for the run (fieldkit/buildh/probe_servers.py) and prints every request it got.
// gprobe-server: echo-ua 8765
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const d = Services.prefs.getDefaultBranch("");
const sys = Services.scriptSecurityManager.getSystemPrincipal();
async function visit(tag) {
  const tab = win.gBrowser.addTab(`http://127.0.0.1:8765/${tag}`, { triggeringPrincipal: sys });
  win.gBrowser.selectedTab = tab;
  // the page's script writes navigator.userAgent into the title; title "x" = loaded, but JavaScript did not run
  for (let i = 0; i < 20 && !(tab.linkedBrowser.contentTitle || "").startsWith("NAV:"); i++) { await sleep(250); }
  const title = tab.linkedBrowser.contentTitle || "";
  say(tag, "|", title.startsWith("NAV:") ? "JS ran, navigator: " + title.slice(4) : title == "x" ? "JS OFF (page loaded, script did not run)" : "(page did not load)");
  win.gBrowser.removeTab(tab);
}
function str(n) { return d.getPrefType(n) == d.PREF_STRING ? d.getStringPref(n) : "(none)"; }
await visit("level0");
for (const level of [1, 2]) {
  Services.prefs.setIntPref("gorilla.linkmode", level);
  await sleep(500);
  say(`level ${level}: clear cache at shutdown ${d.getBoolPref("privacy.clearOnShutdown_v2.cache")}/${d.getBoolPref("privacy.clearOnShutdown.cache")}, ` +
      `check_doc_frequency ${d.getIntPref("browser.cache.check_doc_frequency")}, ua override ${str("general.useragent.override")}`);
  await visit(`level${level}`);
}
const site = Services.scriptSecurityManager.createContentPrincipalFromOrigin("http://127.0.0.1:8765");
Services.perms.addFromPrincipal(site, "gorilla-desktop-site", Services.perms.ALLOW_ACTION);
await visit("level2-desktop-site");
Services.perms.removeFromPrincipal(site, "gorilla-desktop-site");
await visit("level2-mobile-again");
Services.prefs.setBoolPref("gorilla.linkmode.mobile_pages", false);
await sleep(500);
await visit("level2-mobile-unticked");
Services.perms.addFromPrincipal(site, "gorilla-javascript-site", Services.perms.ALLOW_ACTION);
await visit("level2-site-allowed-javascript");
Services.perms.removeFromPrincipal(site, "gorilla-javascript-site");
Services.prefs.setBoolPref("gorilla.linkmode.no_javascript", false);
await sleep(500);
await visit("level2-javascript-unticked");
Services.prefs.setBoolPref("gorilla.linkmode.no_javascript", true);
await sleep(500);
// a tab opened in Very slow link must run scripts again once the level is Off (per-tab switch reset)
const kept = win.gBrowser.addTab("http://127.0.0.1:8765/kept", { triggeringPrincipal: sys });
await sleep(3000);
Services.prefs.setIntPref("gorilla.linkmode", 0);
await sleep(500);
kept.linkedBrowser.reload();
await sleep(3000);
say("tab kept open, level Off, reloaded |", (kept.linkedBrowser.contentTitle || "").startsWith("NAV:") ? "JS ran" : "JS STILL OFF");
win.gBrowser.removeTab(kept);
Services.prefs.setIntPref("gorilla.linkmode", 2);
await sleep(500);
Services.prefs.setBoolPref("gorilla.linkmode.mobile_pages", true);
Services.prefs.setIntPref("gorilla.linkmode", 0);
await sleep(500);
say(`level 0: clear cache at shutdown ${d.getBoolPref("privacy.clearOnShutdown_v2.cache")}, check_doc_frequency ${d.getIntPref("browser.cache.check_doc_frequency")}, ua override ${str("general.useragent.override")}, platform override ${str("general.platform.override")}`);
await visit("level0-again");
