// Every about: page the build registers, opened and read (owner 2026-10-08: "I tried to go through each and every
// page in about:about but i might have missed something"). For each page: where it ended up (a page blocked by policy
// lands on the error page), the text it shows, every picture it draws with its size, its controls, the script errors
// it raised and every network request opened while it was showing. A DEAD proxy (127.0.0.1:9) is set first, so a
// request is recorded but nothing leaves the machine. about:glean's menu, while the page exists, is clicked through
// entry by entry and its "submit ping" button pressed, to show whether any of it does anything.
// Pages that crash the browser on purpose (about:crash*) are never opened.
// Two ways (the command rewrites the constants below):
//   default: every registered page in a new tab of its own (hidden ones too), headless;
//   WALK:    the way a person does it, in a visible window: about:about, click a link, read the page, back, next
//            link, DWELL ms per page (owner 2026-10-08: "click on each of those links and analyze ... in real time").
// Errors, requests and missing strings are printed the moment they happen, so a visible run can be followed live.
// Output lines:
//   ABOUT-NOW|<name>                                      (the page about to be opened)
//   ABOUT|<name>|<process: parent or content>|<landed on>|<title>|<text length>|<pictures>|<controls>|<hidden in about:about>
//   ABOUT-TEXT|<name>|<first 400 characters of the text>
//   ABOUT-PIC|<name>|<picture url>|<w>x<h>
//   ABOUT-ERR|<name>|<script error>|<source>
//   ABOUT-REJ|<name>|<reason>|<where the promise was rejected, or made>   (a rejection nobody handled; the console
//                                                                          only says "uncaught exception: undefined")
//   ABOUT-LIT|<name>|<element>|<why its update failed>   (web components whose update never completes)
//   ABOUT-L10N|<name>|<id>|<element>   (an element names a Fluent message no loaded file defines: it shows nothing;
//                                         Fluent rejects the page's translate promise with no reason)
//   ABOUT-NET|<name>|<request url>|<who opened it: a page, an extension (extension <id>) or the browser>
//   GLEAN-MENU|<entry>|<section shown>|<text length>|<first 160 characters>
//   GLEAN-SUBMIT|<what the page said afterwards>|<requests opened>
//   ABOUT-SUMMARY|<pages>|<blocked>|<blank>|<with errors>|<requests>
// A name ending " (closing)" is a page whose tab (or, walking, whose visit) was being left.
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("ABOUT-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const clip = (s, n) => String(s || "").replace(/[\r\n|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, n);
// only=<a,b>: the command rewrites this line to read just those pages
const ONLY = [];
// walk=1: the command rewrites these two lines
const WALK = false;
const DWELL = 3500;

// nothing may leave the machine: a dead proxy for every protocol, set before the first page opens
for (const [k, v] of [["network.proxy.type", 1], ["network.proxy.http_port", 9], ["network.proxy.ssl_port", 9],
                      ["network.proxy.socks_port", 9]]) { Services.prefs.setIntPref(k, v); }
for (const k of ["network.proxy.http", "network.proxy.ssl", "network.proxy.socks"]) { Services.prefs.setStringPref(k, "127.0.0.1"); }
Services.prefs.setBoolPref("network.proxy.socks_remote_dns", true);
// a rejection's stack is captured only with async stacks on (release builds keep them off): on for this copy
Services.prefs.setBoolPref("javascript.options.asyncstack", true);
Services.prefs.setBoolPref("javascript.options.asyncstack_capture_debuggee_only", false);

let current = "(start-up)";
const net = [], errs = [];
const netObs = { observe(s) {
  try {
    const ch = s.QueryInterface(Ci.nsIChannel), li = ch.loadInfo;
    const p = li && (li.triggeringPrincipal || li.loadingPrincipal);
    const who = !p ? "?" : p.isSystemPrincipal ? "browser" : p.addonId ? "extension " + p.addonId : (p.spec || p.origin || "?");
    net.push(current);
    say(`ABOUT-NET|${current}|${clip(ch.URI.spec, 200)}|${clip(who, 120)}`);
  } catch (e) {}
} };
Services.obs.addObserver(netObs, "http-on-opening-request");
const conObs = { observe(m) {
  try {
    if (m instanceof Ci.nsIScriptError && !(m.flags & Ci.nsIScriptError.warningFlag) && !(m.flags & Ci.nsIScriptError.infoFlag)) {
      // a rejected promise has no sourceName: its first stack frame says where it came from
      const st = m.stack, at = st && st.source ? `${st.source}:${st.line}` : "";
      errs.push(current);
      say(`ABOUT-ERR|${current}|${clip(m.errorMessage, 200)}|${clip(m.sourceName || at, 110)}`);
    }
  } catch (e) {}
} };
Services.console.registerListener(conObs);
// promises rejected and never handled, with the place they were rejected (PromiseDebugging, chrome only); the
// autoconfig sandbox has no PromiseDebugging, the browser window (system principal) has
const PD = win.PromiseDebugging;
const rejObs = {
  onLeftUncaught(pr) {
    try {
      // where it was rejected; failing that, where it was made (a promise rejected from C++ has no rejection stack)
      const r = PD.getRejectionStack(pr), a = r ? null : PD.getAllocationStack(pr);
      const s = r || a;
      const at = s ? `${a ? "made at " : ""}${s.source}:${s.line} ${s.functionDisplayName || ""}` : "(no stack)";
      let reason = "";
      try { reason = String(PD.getState(pr).reason); } catch (e) {}
      say(`ABOUT-REJ|${current}|${clip(reason, 120)}|${clip(at, 160)}`);
    } catch (e) {}
  },
  onConsumed() {},
};
if (PD) { PD.addUncaughtRejectionObserver(rejObs); } else { say("ABOUT-ERROR|rejections|no PromiseDebugging in the window"); }

// read a page: text, pictures (CSS backgrounds, list images, generated content, <img>), controls; walks shadow roots
function gInspect(doc) {
  const w = doc.defaultView, out = { title: doc.title, uri: doc.documentURI, text: "", pics: [], controls: 0 };
  // the text a reader sees, INCLUDING what web components draw in their shadow roots (about:logins, the error
  // card): every text node whose element is rendered
  const parts = [];
  const textOf = r => {
    const tw = doc.createTreeWalker(r, 4 /* SHOW_TEXT */);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      const el = n.parentElement;
      if (el && !/^(script|style|template|noscript)$/i.test(el.localName) && el.getClientRects().length && n.data.trim()) { parts.push(n.data.trim()); }
    }
    for (const el of r.querySelectorAll("*")) { if (el.shadowRoot) { textOf(el.shadowRoot); } }
  };
  const root = doc.body || doc.documentElement;
  if (root) { textOf(root); }
  out.text = parts.join(" ");
  const seen = new Set();
  const walk = r => {
    for (const el of r.querySelectorAll("*")) {
      const cs = w.getComputedStyle(el);
      const urls = [];
      for (const v of [cs.backgroundImage, cs.listStyleImage, cs.content]) {
        if (v && v != "none" && v != "normal") { for (const m of v.matchAll(/url\("?([^")]+)"?\)/g)) { urls.push(m[1]); } }
      }
      if (el.localName == "img" && (el.currentSrc || el.src)) { urls.push(el.currentSrc || el.src); }
      if (urls.length && cs.visibility != "hidden") {
        const b = el.getBoundingClientRect();
        if (b.width > 0 && b.height > 0) {
          for (const u of urls) {
            const k = u.slice(0, 120) + "|" + Math.round(b.width) + "x" + Math.round(b.height);
            if (!seen.has(k)) { seen.add(k); out.pics.push(k); }
          }
        }
      }
      if (el.matches("button, a[href], input, select, textarea, moz-button, moz-toggle, moz-checkbox, [role=button], [role=menuitem]")) { out.controls++; }
      if (el.shadowRoot) { walk(el.shadowRoot); }
    }
  };
  walk(doc);
  return out;
}
const FRAME = "data:application/javascript," + encodeURIComponent(
  gInspect.toString() + "\nsendAsyncMessage('gprobe:about', gInspect(content.document));");

async function read(tab) {
  const browser = tab.linkedBrowser;
  if (!browser.isRemoteBrowser && browser.contentDocument) { return gInspect(browser.contentDocument); }
  return new Promise(resolve => {
    const mm = browser.messageManager;
    const done = m => { mm.removeMessageListener("gprobe:about", done); resolve(m.data); };
    mm.addMessageListener("gprobe:about", done);
    mm.loadFrameScript(FRAME, false);
    wait(() => resolve(null), 5000);
  });
}

const PFX = "@mozilla.org/network/protocol/about;1?what=";
function hiddenOf(name) {
  try {
    const flags = Cc[PFX + name].getService(Ci.nsIAboutModule).getURIFlags(Services.io.newURI("about:" + name));
    return (flags & Ci.nsIAboutModule.HIDE_FROM_ABOUTABOUT) ? "hidden" : "listed";
  } catch (e) { return "flags-error"; }
}

let blocked = 0, blank = 0, withErr = 0;
// everything learnt about the page showing in `tab` (`name` without "about:")
async function report(name, tab, hidden) {
  let r = await read(tab);
  // a slow page is not a blank page: one with no text yet is read again, every 2 s, for up to 10 s more
  // (2026-10-08: about:debugging and about:about in a fresh unpacked build needed ~6 s; at 3.5 s they read as blank)
  for (let i = 0; i < 5 && r && clip(r.text, 100).length < 20; i++) {
    await sleep(2000);
    r = await read(tab);
  }
  const proc = tab.linkedBrowser.isRemoteBrowser ? "content" : "parent";
  if (!r) { say(`ABOUT|${name}|${proc}|(unreadable)|||||${hidden}`); return; }
  const landed = r.uri && r.uri.startsWith("about:neterror") ? "BLOCKED " + clip(r.uri, 60) : clip(r.uri, 60);
  if (landed.startsWith("BLOCKED")) { blocked++; }
  const text = clip(r.text, 100000);
  if (text.length < 20) { blank++; }
  say(`ABOUT|${name}|${proc}|${landed}|${clip(r.title, 60)}|${text.length}|${r.pics.length}|${r.controls}|${hidden}`);
  say(`ABOUT-TEXT|${name}|${text.slice(0, 400)}`);
  for (const p of r.pics) { say(`ABOUT-PIC|${name}|${p}`); }
  const doc = !tab.linkedBrowser.isRemoteBrowser ? tab.linkedBrowser.contentDocument : null;
  if (!doc) { return; }
  // every Fluent message the page names, asked of the page's own localization; a missing one formats to null
  // (release builds print no warning for it) (parent-process pages only)
  if (doc.l10n) {
    const ids = new Map();
    const walk = root => {
      for (const el of root.querySelectorAll("[data-l10n-id]")) { const id = el.getAttribute("data-l10n-id"); if (id && !ids.has(id)) { ids.set(id, el.localName + (el.id ? "#" + el.id : "")); } }
      for (const el of root.querySelectorAll("*")) { if (el.shadowRoot) { walk(el.shadowRoot); } }
    };
    walk(doc);
    for (const [id, where] of ids) {
      const res = await race(doc.l10n.formatMessages([{ id }]), 1500);
      if (!res.ok || !res.value || !res.value[0]) { say(`ABOUT-L10N|${name}|${id}|${where}`); }
    }
  }
  // web components (lit) whose update failed: their updateComplete promise rejects (parent-process pages only)
  const els = [];
  const walk = root => { for (const el of root.querySelectorAll("*")) { if (el.updateComplete) { els.push(el); } if (el.shadowRoot) { walk(el.shadowRoot); } } };
  walk(doc);
  for (const el of els) {
    let res;
    try { res = await race(el.updateComplete, 1500); } catch (e) { res = { ok: false, error: String(e) }; }
    if (!res.ok) {
      const id = el.id ? "#" + el.id : el.getAttribute("data-l10n-id") ? `[${el.getAttribute("data-l10n-id")}]` : "";
      say(`ABOUT-LIT|${name}|${el.localName}${id}|${clip(res.error, 160)}`);
    }
  }
  // about:glean: every menu entry clicked, then the submit button pressed (parent-process page)
  if (name == "glean") {
    for (const cat of [...doc.querySelectorAll(".category")]) {
      cat.click();
      await sleep(1200);
      const id = cat.id.replace(/^category-/, "");
      const sec = doc.getElementById(id);
      const shown = sec && !sec.hidden && sec.getBoundingClientRect().height > 0;
      const t = sec ? clip(sec.innerText, 100000) : "";
      say(`GLEAN-MENU|${id}|${shown ? "shown" : "NOT SHOWN"}|${t.length}|${t.slice(0, 160)}`);
    }
    const testing = doc.getElementById("category-manual-testing");
    if (testing) { testing.click(); await sleep(800); }
    const tag = doc.getElementById("tag-pings"), btn = doc.getElementById("controls-submit");
    if (tag && btn) {
      const n0 = net.length;
      tag.value = "gorilla-probe";
      tag.dispatchEvent(new win.Event("change", { bubbles: true }));
      btn.click();
      await sleep(4000);
      const after = clip((doc.getElementById("manual-testing") || doc.body).innerText, 100000);
      say(`GLEAN-SUBMIT|${after.slice(0, 300)}|${net.length - n0}`);
    } else { say("GLEAN-SUBMIT|no submit controls found|0"); }
  }
}

const SYS = { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() };
let count = 0;
if (!WALK) {
  const names = Object.keys(Cc).filter(c => c.startsWith(PFX)).map(c => c.slice(PFX.length))
    .filter(n => n && !n.startsWith("crash") && n != "blank" && n != "srcdoc" && (!ONLY.length || ONLY.includes(n))).sort();
  for (const name of names) {
    count++;
    current = name;
    const e0 = errs.length;
    say(`ABOUT-NOW|${name}`);
    let tab;
    try {
      tab = win.gBrowser.addTab("about:" + name, SYS);
      win.gBrowser.selectedTab = tab;
      await sleep(DWELL);
      await report(name, tab, hiddenOf(name));
    } catch (e) {
      say(`ABOUT|${name}|?|(error ${clip(String(e), 120)})|||||${hiddenOf(name)}`);
    } finally {
      // what the page raises while its tab closes is told apart: "<name> (closing)"
      current = name + " (closing)";
      if (tab) { try { win.gBrowser.removeTab(tab); } catch (e) {} }
      await sleep(800);
    }
    if (errs.length > e0) { withErr++; }
  }
} else {
  // the way a person does it: one tab, about:about, click the link, read, back
  const tab = win.gBrowser.selectedTab;
  const home = async () => {
    current = "about";
    tab.linkedBrowser.fixupAndLoadURIString("about:about", SYS);
    for (let i = 0; i < 40 && !(tab.linkedBrowser.contentDocument && tab.linkedBrowser.contentDocument.documentURI == "about:about"
                                 && tab.linkedBrowser.contentDocument.querySelector("a[href^='about:']")); i++) { await sleep(250); }
    return tab.linkedBrowser.contentDocument;
  };
  let doc = await home();
  const links = doc ? [...doc.querySelectorAll("a[href^='about:']")].map(a => a.getAttribute("href")) : [];
  say(`ABOUT-WALK|${links.length} links on about:about|${DWELL} ms per page`);
  for (const href of links) {
    const name = href.slice(6).split(/[?#]/)[0];
    if (!name || name.startsWith("crash") || (ONLY.length && !ONLY.includes(name))) { continue; }
    count++;
    doc = await home();
    const a = doc && [...doc.querySelectorAll("a[href^='about:']")].find(x => x.getAttribute("href") == href);
    if (!a) { say(`ABOUT|${name}|?|(link not found on about:about)|||||listed`); continue; }
    current = name;
    const e0 = errs.length;
    say(`ABOUT-NOW|${name}`);
    try {
      a.scrollIntoView({ block: "center" });
      await sleep(400);
      a.click();
      await sleep(DWELL);
      await report(name, tab, hiddenOf(name));
    } catch (e) {
      say(`ABOUT|${name}|?|(error ${clip(String(e), 120)})|||||listed`);
    }
    current = name + " (closing)";
    if (errs.length > e0) { withErr++; }
  }
}
current = "(end)";
await sleep(1000);
if (PD) { PD.removeUncaughtRejectionObserver(rejObs); }
Services.obs.removeObserver(netObs, "http-on-opening-request");
Services.console.unregisterListener(conObs);
say(`ABOUT-SUMMARY|${count}|${blocked}|${blank}|${withErr}|${net.length}`);
