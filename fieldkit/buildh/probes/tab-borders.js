// How the tab strip draws its tabs: active, inactive and inactive under the mouse (owner 2026-10-08: "the inactive
// tab has lost its thin cyan border. ONLY the active border is visible because of the pink"). For each state it
// reports what the tab's .tab-background computes to (background, outline, border, box-shadow) and the theme tokens
// that feed it, so two builds can be compared line by line (build-harness probe-compare A B).
// Output lines:
//   TAB|<state>|<property>|<computed value>
//   TOK|<state>|<token>|<value>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("TAB-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const SYS = { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() };
const second = win.gBrowser.addTab("about:blank", SYS);
await sleep(1500);
const first = win.gBrowser.tabs[0];
win.gBrowser.selectedTab = first;                 // the second tab is the inactive one
await sleep(800);
const PROPS = ["background-color", "outline-style", "outline-width", "outline-color", "outline-offset", "border-top-style",
               "border-top-width", "border-top-color", "box-shadow", "opacity"];
const TOKENS = ["--tab-border", "--tab-border-color", "--tab-border-color-hover", "--tab-border-color-selected",
                "--tab-background-color", "--tab-background-color-selected", "--toolbarbutton-outline-color",
                "--button-border-color", "--border-color", "--border-width", "--tab-outline-offset"];
function dump(state, tab) {
  const bg = tab.querySelector(".tab-background");
  if (!bg) { say(`TAB|${state}|error|no .tab-background`); return; }
  const cs = win.getComputedStyle(bg);
  for (const p of PROPS) { say(`TAB|${state}|${p}|${cs.getPropertyValue(p).trim()}`); }
  for (const t of TOKENS) { say(`TOK|${state}|${t}|${cs.getPropertyValue(t).trim()}`); }
  const r = bg.getBoundingClientRect();
  say(`TAB|${state}|box|${Math.round(r.width)}x${Math.round(r.height)}`);
}
dump("active", first);
dump("inactive", second);
// under the mouse: the :hover rules apply only to a real pointer, so the hover state is read from the rule text.
// The walk follows @import and grouping rules (@media, @layer, @supports): master-redirect.css arrives by @import,
// and a flat walk saw none of its rules (2026-10-09).
const seen = new Set();
function walk(rules, href) {
  for (const rule of rules) {
    if (rule.styleSheet) { visit(rule.styleSheet); continue; }
    if (rule.cssRules && !rule.selectorText) { walk(rule.cssRules, href); continue; }
    const t = rule.cssText || "";
    if (/tabbrowser-tab:not\(\[selected\]\)(:hover)?[^{]*\.tab-background/.test(t) && /outline|border|box-shadow/.test(t)) {
      say(`TAB|rule|${(href || "inline").split("/").pop()}|${t.replace(/\s+/g, " ").slice(0, 300)}`);
    }
  }
}
function visit(sheet) {
  if (!sheet || seen.has(sheet)) { return; }
  seen.add(sheet);
  let rules;
  try { rules = sheet.cssRules; } catch (e) { return; }
  walk(rules, sheet.href);
}
for (const sheet of win.document.styleSheets) { visit(sheet); }
say(`TAB|sheets|walked|${seen.size}`);
win.gBrowser.removeTab(second);
