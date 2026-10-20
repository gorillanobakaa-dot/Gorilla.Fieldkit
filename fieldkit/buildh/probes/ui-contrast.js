// UI readability and liveness of the installed build (fieldkit buildh/uicheck.py turns these lines into proof rows).
// Born 2026-10-04: the Gorilla.Satellite toolbar menu shipped in build 23 as cyan text on the system's light grey
// (contrast about 1.2:1) and its opening handler threw, so items meant to be hidden showed and its commands failed.
// Neither was caught before the maintainer saw it. This probe opens every menu a toolbar button owns and the
// Gorilla parts of Settings, and reports for every visible item: the contrast of its text against what is really
// painted behind it, and every script error raised while the surface opened.
// Output lines (parsed by uicheck.py):
//   UI|menu|<button id>|<item label>|<contrast>|<text rgb>|<background rgb>
//   UI|settings|<control id>|<label>|<contrast>|<text rgb>|<background rgb>
//   UI|error|<surface>|<message>
//   UI|surface|<surface>|<visible items>|<hidden items>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("UI|error|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const doc = win.document;

// colours: computed "rgb(a)" -> [r, g, b, a]; composite over what is behind; WCAG 2 contrast ratio
function rgba(s) {
  const m = String(s).match(/rgba?\(([^)]+)\)/);
  if (!m) { return null; }
  const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
  return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
}
function over(top, under) {
  const a = top[3];
  return [0, 1, 2].map(i => Math.round(top[i] * a + under[i] * (1 - a))).concat(1);
}
function lum(c) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}
function contrast(a, b) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
const hex = c => "rgb(" + c.slice(0, 3).join(",") + ")";

// What is painted behind an element: its own and its ancestors' backgrounds, composited from the root down. A
// popup drawn natively (appearance other than none) is painted by the system in the system's Menu colour, whatever
// its CSS background says.
function systemColour(name, w) {
  const d = w.document.createElementNS("http://www.w3.org/1999/xhtml", "div");
  d.style.backgroundColor = name;
  (w.document.body || w.document.documentElement).appendChild(d);
  const c = rgba(w.getComputedStyle(d).backgroundColor);
  d.remove();
  return c;
}
function behind(el, w) {
  const chain = [];
  // a shadow root (nodeType 11) is passed through to its host: the walk stopped there before 2026-10-18 and every
  // item inside a shadow tree (panel-item buttons, menu items in the arrowscrollbox) was judged against white
  for (let n = el; n && (n.nodeType == 1 || n.nodeType == 11); n = n.assignedSlot || n.parentNode || n.host) {
    if (n.nodeType == 11) { continue; }
    chain.push(n);
    if (n.localName == "menupopup" || n.localName == "panel") { break; }
  }
  let base = [255, 255, 255, 1];
  for (const n of chain.reverse()) {
    const cs = w.getComputedStyle(n);
    if ((n.localName == "menupopup" || n.localName == "panel") && cs.appearance != "none") {
      base = systemColour("Menu", w);
      continue;
    }
    const c = rgba(cs.backgroundColor);
    if (c && c[3] > 0) { base = over(c, base); }
  }
  return base;
}

const errors = [];
const listener = { observe(m) {
  try {
    const e = m.QueryInterface(Ci.nsIScriptError);
    if (!(e.flags & Ci.nsIScriptError.warningFlag) && /^(chrome|resource|moz-src):/.test(e.sourceName || "")) {
      errors.push(`${e.errorMessage} @${(e.sourceName || "").split("/").pop()}:${e.lineNumber}`);
    }
  } catch (x) {}
} };
Services.console.registerListener(listener);

// 1. every menu owned by a toolbar button in the navigation bar
win.resizeTo(1400, 800);
await sleep(1500);
const buttons = [...doc.querySelectorAll("#nav-bar toolbarbutton")].filter(b => b.querySelector(":scope > menupopup"));
for (const b of buttons) {
  const popup = b.querySelector(":scope > menupopup");
  const before = errors.length;
  const shown = new Promise(r => popup.addEventListener("popupshown", r, { once: true }));
  b.open = true;
  await Promise.race([shown, sleep(3000)]);
  let visible = 0, hidden = 0;
  for (const it of popup.querySelectorAll("menuitem, menu")) {
    if (it.hidden || win.getComputedStyle(it).display == "none") { hidden++; continue; }
    visible++;
    const text = rgba(win.getComputedStyle(it).color);
    const bg = behind(it, win);
    say(`UI|menu|${b.id}|${(it.getAttribute("label") || "").slice(0, 60)}|${contrast(text, bg).toFixed(2)}|${hex(text)}|${hex(bg)}`);
  }
  say(`UI|surface|menu ${b.id}|${visible}|${hidden}`);
  popup.hidePopup();
  await sleep(300);
  for (const e of errors.slice(before)) { say(`UI|error|menu ${b.id}|${e}`); }
}

// 2. the Gorilla controls in Settings (ids starting with "gorilla")
const before = errors.length;
const tab = win.gBrowser.addTab("about:preferences#general", { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
win.gBrowser.selectedTab = tab;
function deepAll(root, out = []) {
  for (const el of root.querySelectorAll("*")) {
    if (el.id && el.id.startsWith("gorilla")) { out.push(el); }
    if (el.shadowRoot) { deepAll(el.shadowRoot, out); }
  }
  return out;
}
let controls = [];
for (let i = 0; i < 80 && !(controls = deepAll(tab.linkedBrowser.contentDocument)).some(c => c.label); i++) { await sleep(250); }
await sleep(1000);
const pw = tab.linkedBrowser.contentWindow;
controls = deepAll(tab.linkedBrowser.contentDocument);
for (const c of controls) {
  const label = c.shadowRoot && (c.shadowRoot.querySelector(".text, label, .label") || c.shadowRoot.querySelector("*"));
  if (!c.label || !label) { continue; }
  const text = rgba(pw.getComputedStyle(label).color);
  const bg = behind(c, pw);
  say(`UI|settings|${c.id}|${c.label.slice(0, 60)}|${contrast(text, bg).toFixed(2)}|${hex(text)}|${hex(bg)}`);
}
say(`UI|surface|settings|${controls.filter(c => c.label).length}|0`);
for (const e of errors.slice(before)) { say(`UI|error|settings|${e}`); }
win.gBrowser.removeTab(tab);

// 3. (2026-10-18) every popup a person opens from the toolbar and the address bar, not only toolbar-button menus:
// the search-mode list next to the address bar shipped white with cyan text (about 1.25:1) and the app menu cut
// "Gorilla Extensions and Gorilla The..." short; section 1 never opened either. For every visible item: text and
// background as painted, the HOVER background (forced: _moz-menuactive on menu items, an InspectorUtils :hover lock
// on panel buttons), and whether its label is clipped (needs more width than it has).
//   UI|popup|<surface>|<label>|<contrast>|<text rgb>|<background rgb>|<hover rgb>|<clipped 0/1>|<disabled 0/1>
// (a disabled row has no hover by design: its hover is not judged)
const IU = win.InspectorUtils;
function painter(it) {
  return (it.localName == "panel-item" && it.shadowRoot && it.shadowRoot.querySelector("button")) || it;
}
// the hover is read after 250 ms: read at once, a transition (the theme's is 0.01 ms) reports its FIRST frame, and
// app-menu hovers read black although #65568C was painted (probe theme-diag, 2026-10-18). A failed lock is an error
// line, never a silent fallback to the plain background.
async function hoverBg(it, lab) {
  let c = null;
  it = painter(it);
  lab = lab || it;
  try {
    if (it.localName == "menuitem" || it.localName == "menu") {
      it.setAttribute("_moz-menuactive", "true");
      await sleep(250);
      c = behind(lab, win);
      it.removeAttribute("_moz-menuactive");
    } else if (IU) {
      IU.addPseudoClassLock(it, ":hover");
      await sleep(250);
      c = behind(lab, win);
      IU.removePseudoClassLock(it, ":hover");
    }
  } catch (x) { say(`UI|error|hover|${x}`); }
  return c;
}
function labelOf(it) {
  return it.querySelector(".menu-text, .menu-iconic-text, .toolbarbutton-text, label") ||
         (it.shadowRoot && it.shadowRoot.querySelector("label, .toolbarbutton-text"));
}
async function measure(surface, items) {
  let visible = 0;
  for (const it of items) {
    if (it.hidden || win.getComputedStyle(it).display == "none" || !it.getBoundingClientRect().width) { continue; }
    visible++;
    const lab = labelOf(it) || it;
    const text = rgba(win.getComputedStyle(lab).color) || rgba(win.getComputedStyle(it).color);
    // what is behind the TEXT (its own box and every ancestor): the zoom button paints its hover on its label
    const bg = behind(lab, win);
    const disabled = it.hasAttribute("disabled") || painter(it).disabled ? 1 : 0;
    const hv = disabled ? bg : ((await hoverBg(it, lab)) || bg);
    const clipped = lab.scrollWidth > lab.clientWidth + 1 ? 1 : 0;
    const name = (it.getAttribute("label") || lab.textContent || it.id || "").trim().slice(0, 60);
    say(`UI|popup|${surface}|${name}|${contrast(text, bg).toFixed(2)}|${hex(text)}|${hex(bg)}|${hex(hv)}|${clipped}|${disabled}`);
  }
  say(`UI|surface|${surface}|${visible}|0`);
}
async function openPopup(popup, opener) {
  if (!popup) { return false; }
  const shown = new Promise(r => popup.addEventListener("popupshown", r, { once: true }));
  try { opener(); } catch (x) { return false; }
  await Promise.race([shown, sleep(3000)]);
  return popup.state == "open" || popup.state == "showing";
}
const before3 = errors.length;
// 3a. the search-mode list beside the address bar (158: a XUL panel ".searchmode-switcher-panel" wrapping
// panel-item elements, built on the first click of the ".searchmode-switcher" button in the address bar)
try {
  const btn = doc.querySelector("#urlbar .searchmode-switcher") || doc.querySelector(".searchmode-switcher");
  if (btn) {
    btn.click();
    let sm = null;
    for (let i = 0; i < 20 && !(sm = doc.querySelector(".searchmode-switcher-panel")); i++) { await sleep(150); }
    for (let i = 0; i < 20 && sm && sm.state != "open"; i++) { await sleep(150); }
    if (sm && sm.state == "open") {
      await sleep(300);
      await measure("search-mode list", sm.querySelectorAll("panel-item"));
      sm.hidePopup();
      await sleep(300);
    } else {
      say("UI|error|search-mode list|did not open after a click on .searchmode-switcher");
    }
  } else {
    say("UI|error|search-mode list|no .searchmode-switcher button in the address bar");
  }
} catch (x) { say(`UI|error|search-mode list|${x}`); }
// 3b. the app menu (the hamburger) and its main view
try {
  const panel = doc.getElementById("appMenu-popup");
  if (await openPopup(panel, () => win.PanelUI.show())) {
    await sleep(500);
    await measure("app menu", doc.querySelectorAll("#appMenu-mainView toolbarbutton.subviewbutton"));
    panel.hidePopup();
    await sleep(300);
  } else {
    say("UI|error|app menu|could not be opened");
  }
} catch (x) { say(`UI|error|app menu|${x}`); }
// 3c. right-click menus a person meets everywhere: on a tab, on the toolbar
for (const [id, label] of [["tabContextMenu", "tab right-click menu"], ["toolbar-context-menu", "toolbar right-click menu"]]) {
  const cm = doc.getElementById(id);
  const target = id == "tabContextMenu" ? win.gBrowser.selectedTab : doc.getElementById("nav-bar");
  if (await openPopup(cm, () => cm.openPopup(target, "after_start", 0, 0, true, false))) {
    await measure(label, cm.querySelectorAll("menuitem, menu"));
    cm.hidePopup();
    await sleep(300);
  } else {
    say(`UI|error|${label}|could not be opened (${id})`);
  }
}
for (const e of errors.slice(before3)) { say(`UI|error|popups|${e}`); }
Services.console.unregisterListener(listener);
