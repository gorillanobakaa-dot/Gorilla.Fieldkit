// Help > About shows when this build was made (owner 2026-10-08, D-157-38: "the part where it shows the version number
// also carries a built YY:MM:DD:HH:SS timestamp so we the users can keep track of what build is and when it was
// actually done"). Opens the About window the way the menu does and reads its version line, next to the build's
// own BuildID (application.ini), so the harness can hold both against the build it recorded.
// Output lines:
//   STAMP|buildid|<Services.appinfo.appBuildID>
//   STAMP|expected|built <YY:MM:DD:HH:MM:SS from the BuildID>
//   STAMP|shown|<the About window's version line, as drawn>
//   STAMP|verdict|ok or FAIL|<why>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("STAMP|verdict|FAIL|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const id = Services.appinfo.appBuildID;
const want = /^\d{14}$/.test(id) ? "built " + [id.slice(2, 4), id.slice(4, 6), id.slice(6, 8), id.slice(8, 10), id.slice(10, 12), id.slice(12, 14)].join(":") : "";
say(`STAMP|buildid|${id}`);
say(`STAMP|expected|${want || "(BuildID is not 14 digits)"}`);
// the menu's own command (Help > About Gorilla Unleashed)
if (typeof win.openAboutDialog == "function") { win.openAboutDialog(); }
else { Services.ww.openWindow(win, "chrome://browser/content/aboutDialog.xhtml", "", "chrome,centerscreen,dependent", null); }
let about = null, text = "";
for (let i = 0; i < 40; i++) {
  await sleep(250);
  about = Services.wm.getMostRecentWindow("Browser:About");
  const v = about && about.document.getElementById("version");
  text = v ? (v.textContent || "").trim() : "";
  if (text) { break; }
}
say(`STAMP|shown|${text || "(nothing)"}`);
const ok = !!want && text.endsWith(want);
say(`STAMP|verdict|${ok ? "ok" : "FAIL"}|${ok ? "the version line carries this build's stamp" : !text ? "the About window showed no version line" : "expected the line to end with '" + want + "'"}`);
if (about) { about.close(); }
