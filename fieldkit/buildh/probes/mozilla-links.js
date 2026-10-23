// Version: 1.0.0 · updated 26-10-23-21-40
// D-158-22 (owner 2026-10-23): "let's get rid of 'learn more' or any links to mozilla in the build". Opens the pages a
// person meets and counts what can still be clicked to reach Mozilla: a visible link or text-link whose address is a
// Mozilla site, a visible support link (moz-support-link, a.learnMore), and the link prefs' values. The licence page
// and the developer tools are out of scope by the owner's choice (the MPL text keeps its notice).
// Output lines:
//   LINK|<page>|<what>|<address or text>     one per visible item that reaches Mozilla
//   PAGE|<page>|<n items>|<how many elements were looked at>
//   PREF|<name>|<value>                     a link pref whose value still names a Mozilla site
//   LINKS|verdict|ok or FAIL|<total>
const win = Services.wm.getMostRecentWindow("navigator:browser");
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const MOZ = /(^|[./])(mozilla\.(org|com|net)|firefox\.com)([/:?#]|$)/i;
let total = 0;
function visible(el) {
  const w = el.ownerGlobal || el.ownerDocument.defaultView;
  if (!el.isConnected || el.closest("[hidden]")) { return false; }
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) { return false; }
  const cs = w.getComputedStyle(el);
  return cs.display != "none" && cs.visibility != "hidden";
}
function* deep(root) {
  for (const el of root.querySelectorAll("*")) {
    yield el;
    if (el.shadowRoot) { yield* deep(el.shadowRoot); }
  }
}
function judge(page, doc) {
  let n = 0, seen = 0;
  for (const el of deep(doc)) {
    const tag = el.localName;
    const href = el.getAttribute("href") || (tag == "a" ? el.href : "") || "";
    const support = tag == "moz-support-link" || el.getAttribute("is") == "moz-support-link" ||
      (tag == "a" && el.classList.contains("learnMore"));
    const link = (tag == "a" || (tag == "label" && (el.getAttribute("is") == "text-link" || el.classList.contains("text-link"))));
    if (!support && !(link && MOZ.test(href))) { continue; }
    seen++;
    if (!visible(el)) { continue; }
    n++;
    say(`LINK|${page}|${support ? "support link" : "link to Mozilla"}|${(href || el.textContent || "").trim().slice(0, 120)}`);
  }
  say(`PAGE|${page}|${n}|${seen}`);
  total += n;
}
async function open(url, settle = 2500) {
  const tab = win.gBrowser.addTrustedTab(url);
  win.gBrowser.selectedTab = tab;
  const b = tab.linkedBrowser;
  for (let i = 0; i < 40 && (b.currentURI.spec == "about:blank" || b.webProgress?.isLoadingDocument); i++) { await sleep(250); }
  await sleep(settle);
  return { tab, b };
}
// pages in a content process (about:protections, about:privatebrowsing, about:logins, about:home, about:newtab):
// the same judgement runs in the page through a frame script and sends its findings back
const JUDGE_IN_PAGE = `
  const MOZ = ${MOZ};
  const items = []; let seen = 0;
  const visible = el => { if (!el.isConnected || el.closest("[hidden]")) return false;
    const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const cs = content.getComputedStyle(el); return cs.display != "none" && cs.visibility != "hidden"; };
  const deep = function* (root) { for (const el of root.querySelectorAll("*")) { yield el; if (el.shadowRoot) yield* deep(el.shadowRoot); } };
  for (const el of deep(content.document)) {
    const tag = el.localName, href = el.getAttribute("href") || (tag == "a" ? el.href : "") || "";
    const support = tag == "moz-support-link" || el.getAttribute("is") == "moz-support-link" || (tag == "a" && el.classList.contains("learnMore"));
    const link = tag == "a" || (tag == "label" && (el.getAttribute("is") == "text-link" || el.classList.contains("text-link")));
    if (!support && !(link && MOZ.test(href))) continue;
    seen++; if (!visible(el)) continue;
    items.push([support ? "support link" : "link to Mozilla", (href || el.textContent || "").trim().slice(0, 120)]);
  }
  sendAsyncMessage("gprobe:mozlinks", { items, seen });`;
function judgeRemote(page, b) {
  return new Promise(resolve => {
    const mm = b.messageManager;
    let settled = false;
    const done = m => {
      if (settled) { return; }
      settled = true; mm.removeMessageListener("gprobe:mozlinks", done);
      for (const [what, target] of m.data.items) { say(`LINK|${page}|${what}|${target}`); }
      say(`PAGE|${page}|${m.data.items.length}|${m.data.seen}`);
      total += m.data.items.length;
      resolve();
    };
    mm.addMessageListener("gprobe:mozlinks", done);
    mm.loadFrameScript("data:application/javascript," + encodeURIComponent(JUDGE_IN_PAGE), false);
    wait(() => { if (!settled) { settled = true; say(`PAGE|${page}|unreadable|0`); resolve(); } }, 6000);
  });
}
// parent-process about: pages are read directly
const PAGES = ["about:preferences", "about:preferences#privacy", "about:preferences#search", "about:preferences#home",
  "about:addons", "about:protections", "about:support", "about:privatebrowsing", "about:logins", "about:policies",
  "about:downloads", "about:profiles", "about:home", "about:newtab"];
for (const url of PAGES) {
  try {
    const { tab, b } = await open(url);
    const doc = b.contentDocument;
    if (doc) { judge(url, doc); } else { await judgeRemote(url, b); }
    win.gBrowser.removeTab(tab);
  } catch (x) { say(`PAGE|${url}|error ${x}|0`); }
}
// the About window (Help > About)
win.openAboutDialog();
let about = null;
for (let i = 0; i < 40 && !(about = Services.wm.getMostRecentWindow("Browser:About")); i++) { await sleep(250); }
if (about) { await sleep(1500); judge("Help > About", about.document); about.close(); }
else { say("PAGE|Help > About|did not open|0"); }
// the link prefs: none may still name a Mozilla site
for (const name of Services.prefs.getChildList("")) {
  if (Services.prefs.getPrefType(name) != Services.prefs.PREF_STRING) { continue; }
  if (!/(url|uri|link|learnmore|infourl|baseurl)$/i.test(name.replace(/[._-]/g, ""))) { continue; }
  if (/^(devtools|identity\.fxaccounts|identity\.sync|services\.|toolkit\.telemetry|network\.|security\.|browser\.safebrowsing\.provider|media\.gmp|extensions\.(update|systemAddon|getAddons\.(get|discovery|browserMappings))|browser\.ml|browser\.region|browser\.topsites|browser\.newtabpage\.activity-stream\.(fxaccounts|unifiedAds)|browser\.ipProtection|browser\.search\.searchEnginesURL|signon\.firefoxRelay\.base_url|browser\.contentblocking\.report\.endpoint_url|dom\.push\.|webextensions\.storage\.|extensions\.addonAbuseReport\.)/.test(name)) { continue; }
  const v = Services.prefs.getStringPref(name, "");
  if (MOZ.test(v)) { say(`PREF|${name}|${v.slice(0, 120)}`); total++; }
}
say(`LINKS|verdict|${total == 0 ? "ok" : "FAIL"}|${total}`);
