// Which setting groups did about:preferences render, and what failed? (debugging a Settings change without a build)
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const tab = win.gBrowser.addTab("about:preferences#general", { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
win.gBrowser.selectedTab = tab;
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const errors = [];
const listener = { observe(m) { try { const e = m.QueryInterface(Ci.nsIScriptError); if (e.flags === 0 && /preferences|privacy|Gorilla/i.test(e.sourceName + e.errorMessage)) errors.push(e.errorMessage + " @ " + e.sourceName + ":" + e.lineNumber); } catch {} } };
Services.console.registerListener(listener);
await new Promise(r => wait(r, 8000));
Services.console.unregisterListener(listener);
const doc = tab.linkedBrowser.contentDocument;
say("document:", doc ? doc.documentURI : "none", "readyState", doc && doc.readyState);
const groups = [...doc.querySelectorAll("setting-group")];
say("setting-groups:", groups.length, groups.map(g => g.getAttribute("groupid") + (g.hidden ? "(hidden)" : "")).join(", ").slice(0, 600));
const np = doc.querySelector('setting-group[groupid="networkProxy"]');
if (np) {
  const ids = [];
  const walk = root => { for (const el of root.querySelectorAll("*")) { if (el.id) ids.push(el.localName + "#" + el.id); if (el.shadowRoot) walk(el.shadowRoot); } };
  walk(np); if (np.shadowRoot) walk(np.shadowRoot);
  say("networkProxy contents:", ids.join(", ").slice(0, 600));
}
say("errors:", errors.length ? errors.slice(0, 6).join(" || ") : "none");
