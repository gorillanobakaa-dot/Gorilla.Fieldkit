// Where a page draws the build's own pictures (chrome://branding/), element by element, pseudo-elements included, with
// the drawn size: the question behind a decoded-memory number (2026-10-08: on about:addons the 1400 px master logo
// was decoded at full size in the main process, 11.9 MB, while every other page decoded it at the size it was drawn;
// a picture drawn at its natural size, or under image-rendering: crisp-edges/pixelated, is decoded in full).
// Pages: gprobe-branding-pages.txt beside firefox.exe (one per line) when present, else about:addons and
// about:preferences. Pages in content processes are not reachable from here and are reported as such.
// Output lines:
//   BRAND|<page>|<element>|<pseudo or ->|<property>|<url>|<box w>x<box h>|<background-size>|<image-rendering>|<display>
//   BRAND-PAGE|<page>|<count>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("BRAND-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const BR = /chrome:\/\/branding\/[^"')\s]+/;
let pages = ["about:addons", "about:preferences"];
try {
  const f = Services.dirsvc.get("GreD", Ci.nsIFile);
  f.append("gprobe-branding-pages.txt");
  if (f.exists()) { pages = (await win.IOUtils.readUTF8(f.path)).split(/\s+/).filter(Boolean); }
} catch (e) { say(`BRAND-ERROR|pages file|${e}`); }

function describe(el) {
  return el.localName + (el.id ? "#" + el.id : "") + (el.classList && el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : "");
}

for (const url of pages) {
  const tab = win.gBrowser.addTab(url, { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
  win.gBrowser.selectedTab = tab;
  await sleep(5000);
  const doc = tab.linkedBrowser.contentDocument;
  if (!doc) { say(`BRAND-ERROR|${url}|in a content process, not reachable from the parent`); continue; }
  const w = doc.defaultView;
  let n = 0;
  const walk = root => {
    // a shadow root lists only its own elements (its host was visited outside it; revisiting it recursed for ever)
    for (const el of root.documentElement ? [root.documentElement, ...root.querySelectorAll("*")] : [...root.querySelectorAll("*")]) {
      if (!el || !el.localName) { continue; }
      for (const pseudo of [null, "::before", "::after"]) {
        const cs = w.getComputedStyle(el, pseudo);
        for (const prop of ["backgroundImage", "listStyleImage", "content", "maskImage", "borderImageSource"]) {
          const m = String(cs[prop] || "").match(BR);
          if (!m) { continue; }
          const r = el.getBoundingClientRect();
          say(`BRAND|${url}|${describe(el)}|${pseudo || "-"}|${prop}|${m[0]}|${Math.round(r.width)}x${Math.round(r.height)}|` +
              `${cs.backgroundSize}|${cs.imageRendering}|${cs.display}`);
          n++;
        }
      }
      if (el.localName == "img" && BR.test(el.currentSrc || el.src || "")) {
        const r = el.getBoundingClientRect();
        say(`BRAND|${url}|${describe(el)}|-|img|${(el.currentSrc || el.src).match(BR)[0]}|${Math.round(r.width)}x${Math.round(r.height)}|` +
            `-|${w.getComputedStyle(el).imageRendering}|${w.getComputedStyle(el).display}`);
        n++;
      }
      if (el.shadowRoot) { walk(el.shadowRoot); }
    }
  };
  walk(doc);
  say(`BRAND-PAGE|${url}|${n}`);
  win.gBrowser.removeTab(tab);
}
