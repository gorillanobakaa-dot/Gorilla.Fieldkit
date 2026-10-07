// What the browser frame's look costs in CPU, measured (2026-10-07). The Gorilla theme was designed on Linux to be
// cheap to draw: flat black, solid borders, no transitions, no shadows (master-redirect.css, "QUECTOSECOND ANIMATION
// ANNIHILATION"), with toolkit.cosmeticAnimations.enabled false and ui.prefersReducedMotion 1. This probe measures
// that claim instead of assuming it.
// Workload, the same for every variant: 8 tabs; each tab hovered and un-hovered (hover colours and their
// transitions), then a tab switch, 40 rounds, waiting 120 ms after every step so any transition plays out.
// Variants, interleaved and repeated (REPS), in one browser so start-up and warm-up do not bias any of them:
//   gorilla   as shipped
//   no-theme  master-redirect.css switched off in the window (Mozilla's own colours), motion prefs as shipped
//   stock     master-redirect.css off and Mozilla's motion back on (cosmeticAnimations true, prefersReducedMotion 0)
//   idle      as shipped, same wall time, no hover and no switch: the floor every variant includes
// no-theme and stock switch off master-redirect.css only: Gorilla edits inside Mozilla's own CSS files stay.
// CPU is the sum over every process of ChromeUtils.requestProcInfo() cpuTime, so painting work in any process
// counts. A headless browser paints in software, which is what an old laptop without a usable GPU does too.
// Output lines:
//   THEME|<variant>|<rep>|<cpu ms>|<wall ms>|<parent cpu ms>
//   THEME-MEDIAN|<variant>|<median cpu ms>|<median parent cpu ms>
//   THEME-ERROR|<what>|<message>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("THEME-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const frame = () => new Promise(r => win.requestAnimationFrame(() => win.requestAnimationFrame(r)));
const ROUNDS = 40, STEP_MS = 120, REPS = 5;

const sheets = [...win.document.styleSheets].filter(s => (s.href || "").includes("master-redirect.css"));
function nested(list, out = []) {          // master-redirect.css may arrive through an @import
  for (const s of list) {
    try {
      if ((s.href || "").includes("master-redirect.css")) { out.push(s); }
      for (const r of s.cssRules || []) { if (r.styleSheet) { nested([r.styleSheet], out); } }
    } catch (e) { /* a sheet we may not read */ }
  }
  return out;
}
const theme = [...new Set(sheets.concat(nested([...win.document.styleSheets])))];
say(`THEME-SHEETS|${theme.length}|${theme.map(s => s.href).join(" ")}`);
if (!theme.length) { say("THEME-ERROR|sheet|master-redirect.css is not loaded in the browser window"); }

const shipped = {
  anim: Services.prefs.getBoolPref("toolkit.cosmeticAnimations.enabled", true),
  motion: Services.prefs.getIntPref("ui.prefersReducedMotion", 0),
};
function setVariant(v) {
  for (const s of theme) { s.disabled = v != "gorilla"; }
  const stock = v == "stock";
  Services.prefs.setBoolPref("toolkit.cosmeticAnimations.enabled", stock ? true : shipped.anim);
  Services.prefs.setIntPref("ui.prefersReducedMotion", stock ? 0 : shipped.motion);
}

async function cpu() {
  const info = await ChromeUtils.requestProcInfo();
  let total = info.cpuTime;
  for (const c of info.children) { total += c.cpuTime; }
  return { total: total / 1e6, parent: info.cpuTime / 1e6 };
}

const gb = win.gBrowser;
while (gb.tabs.length < 8) { gb.addTab("about:blank", { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() }); }
await sleep(1500);
// hover through InspectorUtils pseudo-class locks: the same :hover style change (and transition) a mouse causes,
// deterministic, and available where synthesised mouse events are not (windowUtils.sendMouseEvent is gone in 157)
const IU = win.InspectorUtils;
let hovered = null;
function move(el) {
  if (hovered) { IU.removePseudoClassLock(hovered, ":hover"); }
  hovered = el;
  if (el) { IU.addPseudoClassLock(el, ":hover"); }
}
const away = null;

async function workload() {
  for (let i = 0; i < ROUNDS; i++) {
    const tab = gb.tabs[i % gb.tabs.length];
    move(tab); await frame(); await sleep(STEP_MS);
    move(away); await frame(); await sleep(STEP_MS);
    gb.selectedTab = gb.tabs[(i + 3) % gb.tabs.length]; await frame(); await sleep(STEP_MS);
  }
}

// idle: the same wall time with no hover and no switch, so the browser's own background work is visible and can
// be subtracted; one warm-up round first (not reported: the first round of any variant ran up to 2x slower);
// the order rotates every round so no variant always runs first
async function idle() { for (let i = 0; i < ROUNDS * 3; i++) { await frame(); await sleep(STEP_MS); } }
const results = { gorilla: [], "no-theme": [], stock: [], idle: [] };
const order = Object.keys(results);
try {
  setVariant("gorilla"); await workload();
  for (let rep = 1; rep <= REPS; rep++) {
    for (const v of order.slice(rep % order.length).concat(order.slice(0, rep % order.length))) {
      setVariant(v == "idle" ? "gorilla" : v);
      await sleep(800); await frame();
      const a = await cpu(), t0 = Date.now();
      if (v == "idle") { await idle(); } else { await workload(); }
      const b = await cpu();
      const row = { cpu: b.total - a.total, parent: b.parent - a.parent, wall: Date.now() - t0 };
      results[v].push(row);
      say(`THEME|${v}|${rep}|${row.cpu.toFixed(0)}|${row.wall}|${row.parent.toFixed(0)}`);
    }
  }
} catch (e) { say(`THEME-ERROR|workload|${e}`); }
finally { move(null); setVariant("gorilla"); }
const median = xs => { const s = [...xs].sort((p, q) => p - q); return s[Math.floor(s.length / 2)]; };
for (const [v, rows] of Object.entries(results)) {
  if (rows.length) { say(`THEME-MEDIAN|${v}|${median(rows.map(r => r.cpu)).toFixed(0)}|${median(rows.map(r => r.parent)).toFixed(0)}`); }
}
