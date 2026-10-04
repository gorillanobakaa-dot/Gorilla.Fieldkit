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
  for (let n = el; n && n.nodeType == 1; n = n.parentNode || n.host) {
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
Services.console.unregisterListener(listener);
