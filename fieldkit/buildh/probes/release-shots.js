// Version: 1.0.0 · updated 26-10-23-19-50
// The release page's pictures, from the installed build in a VISIBLE window (fresh profile, nothing personal on
// screen): each surface a release changes is opened the way a person opens it, then held still while the harness
// takes a picture of the real screen (releaseshots.py, build-harness release-shots). Born 2026-10-23, Linux 158.0-2.
// Output lines:
//   SHOT|<name>|<what it proves>   the surface is open and drawn; the harness captures the screen now
//   SHOT|skip|<name>|<why>         the surface could not be opened
//   SHOT|end
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("SHOT|skip|all|no browser window"); say("SHOT|end"); return; }
const doc = win.document;
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const HOLD = 5000;      // the harness captures within this window; the next surface opens after it
async function openPopup(popup, opener) {
  if (!popup) { return false; }
  const shown = new Promise(r => popup.addEventListener("popupshown", r, { once: true }));
  try { opener(); } catch (x) { return false; }
  await Promise.race([shown, sleep(3000)]);
  return popup.state == "open" || popup.state == "showing";
}
// the window fills the screen, so the picture is the browser and the desktop's top bar only: never the other
// windows a person has open (2026-10-23: the first pictures showed the Claude app's sidebar beside the browser)
win.maximize();
win.focus();
await sleep(2500);
// 1. Help > About: the build number, the build stamp and the Build ID (D-157-38)
win.openAboutDialog();
let about = null;
for (let i = 0; i < 40 && !(about = Services.wm.getMostRecentWindow("Browser:About")); i++) { await sleep(250); }
if (about) {
  await sleep(1500);
  const v = (about.document.getElementById("version") || {}).textContent || "";
  say(`SHOT|about-dialog-build-2|Help > About reads: ${v.trim()}`);
  await sleep(HOLD);
  about.close();
  await sleep(800);
} else { say("SHOT|skip|about-dialog-build-2|the About window did not open"); }
// 2. the app menu: black with cyan text, wide enough for every label (D-158-19, 27em)
const panel = doc.getElementById("appMenu-popup");
if (await openPopup(panel, () => win.PanelUI.show())) {
  await sleep(800);
  say("SHOT|app-menu-black-cyan|the app menu, black with cyan text, every label whole");
  await sleep(HOLD);
  panel.hidePopup();
  await sleep(600);
} else { say("SHOT|skip|app-menu-black-cyan|the app menu did not open"); }
// 3. the search-mode list beside the address bar: Google only, black and cyan (D-158-13, D-158-19)
const btn = doc.querySelector("#urlbar .searchmode-switcher") || doc.querySelector(".searchmode-switcher");
let sm = null;
if (btn) {
  btn.click();
  for (let i = 0; i < 20 && !(sm = doc.querySelector(".searchmode-switcher-panel")); i++) { await sleep(150); }
  for (let i = 0; i < 20 && sm && sm.state != "open"; i++) { await sleep(150); }
}
if (sm && sm.state == "open") {
  await sleep(800);
  say("SHOT|search-mode-list-google-only|the search-mode list: Google is the only engine, black with cyan text");
  await sleep(HOLD);
  sm.hidePopup();
  await sleep(600);
} else { say("SHOT|skip|search-mode-list-google-only|the search-mode list did not open"); }
// 4. a right-click menu on a tab: black with cyan text (D-158-19)
const cm = doc.getElementById("tabContextMenu");
// a real right-click on a tab loads the menu's strings first (Tabbrowser translateTabContextMenu, lazy since 158);
// opened directly, 12 of its 14 rows were blank in the first picture (2026-10-23)
try { win.gBrowser.translateTabContextMenu(); } catch (x) { say(`SHOT|skip|tab-right-click-menu|strings: ${x}`); }
if (await openPopup(cm, () => cm.openPopup(win.gBrowser.selectedTab, "after_start", 0, 0, true, false))) {
  await sleep(800);
  say("SHOT|tab-right-click-menu|a right-click menu on a tab, black with cyan text");
  await sleep(HOLD);
  cm.hidePopup();
} else { say("SHOT|skip|tab-right-click-menu|the tab menu did not open"); }
say("SHOT|end");
