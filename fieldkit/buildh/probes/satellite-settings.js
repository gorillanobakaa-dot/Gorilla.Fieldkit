// Gorilla.Satellite mode in Settings: is the switch there, with its words, and does each level do what it says?
// (2026-10-04, maintainer: a switch a normal user understands, a longer explanation on hover, working at 5 KB/s)
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const tab = win.gBrowser.addTab("about:preferences#general", { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
win.gBrowser.selectedTab = tab;
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
await new Promise(r => wait(r, 6000));
const doc = tab.linkedBrowser.contentDocument;
function deep(root, sel) {                       // querySelector through every shadow root
  const hit = root.querySelector(sel);
  if (hit) return hit;
  for (const el of root.querySelectorAll("*")) {
    if (el.shadowRoot) { const h = deep(el.shadowRoot, sel); if (h) return h; }
  }
  return null;
}
const group = deep(doc, "#gorillaLinkMode");
say("switch found:", !!group, group ? group.localName : "");
if (!group) { return; }
say("label:", group.label || group.getAttribute("label"));
say("description:", group.description || group.getAttribute("description"));
const radios = [...group.querySelectorAll("moz-radio")];
for (const r of radios) {
  // title is a "mapped" property: Firefox removes it from the host and puts it inside the shadow root,
  // so the row (label-wrapper) is what the mouse hovers; the host attribute is always empty.
  const row = r.shadowRoot && r.shadowRoot.querySelector(".label-wrapper");
  const hover = (row && row.getAttribute("title")) || "";
  say("option", r.value, "|", r.label || r.getAttribute("label"), "| hover over row:", (hover || "(no hover text)").slice(0, 90));
}
const d = Services.prefs.getDefaultBranch("");
for (const level of [2, 1, 0]) {
  Services.prefs.setIntPref("gorilla.linkmode", level);
  await new Promise(r => wait(r, 500));
  say(`level ${level}: images ${d.getIntPref("permissions.default.image")}, fonts ${d.getBoolPref("gfx.downloadable_fonts.enabled")}, ` +
      `autoplay ${d.getIntPref("media.autoplay.default")}, disk cache ${d.getBoolPref("browser.cache.disk.enable")}, ` +
      `save-data ${d.getBoolPref("gorilla.network.save_data", false)}, per-server ${d.getIntPref("network.http.max-persistent-connections-per-server")}, ` +
      `radio shows ${group.value}`);
}
