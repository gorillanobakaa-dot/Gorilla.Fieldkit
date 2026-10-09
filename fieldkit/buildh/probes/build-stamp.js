// Help > About shows when this build was made (owner 2026-10-08, D-157-38: "the part where it shows the version number
// also carries a built YY:MM:DD:HH:SS timestamp so we the users can keep track of what build is and when it was
// actually done"). Opens the About window the way the menu does and reads its version line, next to the build's
// own BuildID (application.ini), so the harness can hold both against the build it recorded.
// Output lines:
//   STAMP|buildid|<Services.appinfo.appBuildID>
//   STAMP|expected|built <YY:MM:DD:HH:MM:SS from the BuildID>
//   STAMP|shown|<the About window's version line, as drawn>
//   STAMP|number|<the build number the browser carries: pref gorilla.build.number, 0 when absent>
//   STAMP|idline|<the line under it: "Build ID <the 14 digits>" (owner 2026-10-09: "i do not have a way to check
//                 whether ... is actually the new build (20261009103433) and it was / is installed when you say it was")>
//   STAMP|verdict|ok or FAIL|<why>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("STAMP|verdict|FAIL|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const id = Services.appinfo.appBuildID;
const want = /^\d{14}$/.test(id) ? "built " + [id.slice(2, 4), id.slice(4, 6), id.slice(6, 8), id.slice(8, 10), id.slice(10, 12), id.slice(12, 14)].join(":") : "";
const number = Services.prefs.getIntPref("gorilla.build.number", 0);
say(`STAMP|buildid|${id}`);
say(`STAMP|number|${number}`);
say(`STAMP|expected|${want || "(BuildID is not 14 digits)"}`);
// the menu's own command (Help > About Gorilla Unleashed)
if (typeof win.openAboutDialog == "function") { win.openAboutDialog(); }
else { Services.ww.openWindow(win, "chrome://browser/content/aboutDialog.xhtml", "", "chrome,centerscreen,dependent", null); }
let about = null, text = "", idline = "";
for (let i = 0; i < 40; i++) {
  await sleep(250);
  about = Services.wm.getMostRecentWindow("Browser:About");
  const v = about && about.document.getElementById("version");
  text = v ? (v.textContent || "").trim() : "";
  const b = about && about.document.getElementById("gorilla-buildid");
  idline = b && !b.hidden ? (b.textContent || "").trim() : "";
  if (text && idline) { break; }
}
say(`STAMP|shown|${text || "(nothing)"}`);
say(`STAMP|idline|${idline || "(nothing)"}`);
const wantNumber = number > 0 ? `build ${number}, ${want}` : "";
const wantId = `Build ID ${id}`;
const ok = !!want && text.endsWith(want) && (!wantNumber || text.endsWith(wantNumber)) && idline == wantId;
say(`STAMP|verdict|${ok ? "ok" : "FAIL"}|${ok ? "the version line carries this build's number and stamp, and the Build ID line its BuildID" :
  !text ? "the About window showed no version line" :
  !text.endsWith(want) ? "expected the line to end with '" + want + "'" :
  wantNumber && !text.endsWith(wantNumber) ? "expected the line to end with '" + wantNumber + "'" :
  "expected the line under it to read '" + wantId + "', it read '" + (idline || "nothing") + "'"}`);
if (about) { about.close(); }
