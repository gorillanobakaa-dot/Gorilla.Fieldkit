// Gorilla.Satellite mode and call sites (D-157-39, build 28 item 2): a site the user allowed the camera or the
// microphone (remembered, or for this tab only), or a host on gorilla.linkmode.call_sites, gets its desktop identity
// and its JavaScript at every level; any other site keeps the phone identity and, in Very slow link, no scripts.
// Needs the local page on 127.0.0.1:8765 that puts navigator.userAgent in its title and logs the User-Agent header
// it receives (`build-harness probe` starts it; every request it got is printed in time order).
// Output lines:
//   CALLS|<case>|<what the page saw: "JS ran, navigator: <ua>" or "JS OFF ...">
//   CALLS-MENU|<case>|desktop-site checked=<bool> disabled=<bool>|javascript-site checked=<bool> disabled=<bool>|<tooltip>
// gprobe-server: echo-ua 8765
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("CALLS|error|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const { SitePermissions: SP } = ChromeUtils.importESModule("resource:///modules/SitePermissions.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const sys = Services.scriptSecurityManager.getSystemPrincipal();
const site = Services.scriptSecurityManager.createContentPrincipalFromOrigin("http://127.0.0.1:8765");
const d = Services.prefs.getDefaultBranch("");

async function visit(tag, { temporary = null, menu = false } = {}) {
  const tab = win.gBrowser.addTab("about:blank", { triggeringPrincipal: sys });
  win.gBrowser.selectedTab = tab;
  await sleep(300);
  if (temporary) {   // allowed for this tab only, the way the permission prompt does it without "Remember"
    SP.setForPrincipal(site, temporary, SP.ALLOW, SP.SCOPE_TEMPORARY, tab.linkedBrowser);
  }
  tab.linkedBrowser.fixupAndLoadURIString(`http://127.0.0.1:8765/${tag}`, { triggeringPrincipal: sys });
  for (let i = 0; i < 20 && !(tab.linkedBrowser.contentTitle || "").startsWith("NAV:"); i++) { await sleep(250); }
  const title = tab.linkedBrowser.contentTitle || "";
  say(`CALLS|${tag}|${title.startsWith("NAV:") ? "JS ran, navigator: " + title.slice(4) : title == "x" ? "JS OFF (page loaded, script did not run)" : "(page did not load)"}`);
  if (menu) {        // what the toolbar menu tells the user about this site
    const button = win.document.getElementById("gorilla-satellite-button");
    const popup = button && button.querySelector("menupopup");
    if (!popup) {
      say(`CALLS-MENU|${tag}|no Satellite button in this window`);
    } else {
      popup.dispatchEvent(new win.Event("popupshowing"));
      const it = n => popup.querySelector(`menuitem[gorilla-item='${n}']`);
      const st = n => `${n} checked=${it(n).getAttribute("checked") == "true"} disabled=${!!it(n).disabled}`;
      say(`CALLS-MENU|${tag}|${st("desktop-site")}|${st("javascript-site")}|${it("desktop-site").getAttribute("tooltiptext")}`);
    }
  }
  win.gBrowser.removeTab(tab);
  await sleep(200);
}

say(`CALLS|list|${d.getPrefType("gorilla.linkmode.call_sites") == d.PREF_STRING ? d.getStringPref("gorilla.linkmode.call_sites") : "(no call_sites pref in this build)"}`);
Services.prefs.setIntPref("gorilla.linkmode", 2);       // Very slow link: phone identity and no scripts by default
await sleep(500);
await visit("very-slow-plain-site", { menu: true });
Services.perms.addFromPrincipal(site, "microphone", Services.perms.ALLOW_ACTION);
await visit("very-slow-microphone-remembered", { menu: true });
Services.perms.removeFromPrincipal(site, "microphone");
await visit("very-slow-camera-this-tab-only", { temporary: "camera" });
await visit("very-slow-after-permissions-gone");
Services.prefs.setStringPref("gorilla.linkmode.call_sites", "example.org, 127.0.0.1");
await visit("very-slow-on-call-list", { menu: true });
Services.prefs.setIntPref("gorilla.linkmode", 1);       // Satellite: phone identity, scripts on
await sleep(500);
await visit("satellite-on-call-list");
Services.prefs.clearUserPref("gorilla.linkmode.call_sites");
await visit("satellite-plain-site");
Services.prefs.setIntPref("gorilla.linkmode", 0);
await sleep(500);
await visit("off-plain-site");
