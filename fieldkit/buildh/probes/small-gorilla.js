// No small Gorilla drawn by the browser frame (D-157-35, owner 2026-10-08). The visual runtime check measures pictures
// INSIDE pages; this probe reads what the frame draws for them: each page's tab icon and, on browser pages, the
// address-bar chip (#identity-icon). A branding picture there is the Gorilla shrunk to 16 px.
// Pages: New Tab, Home, Settings, Add-ons, Processes, Support, Logins, About. For pages reachable from the parent
// process it also lists every element painting a chrome://branding/ picture smaller than 64 CSS px.
// Output lines:
//   SMALL|tab|<page>|<tab image or none>|ok or FAIL
//   SMALL|chip|<page>|<display>|<list-style-image>|ok or FAIL
//   SMALL|page|<page>|<selector>|<url>|<w>x<h>|FAIL
//   SMALL-SUMMARY|<pages>|<fails>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("SMALL-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const BRAND = /chrome:\/\/branding\//;
let fails = 0;
const verdict = bad => { if (bad) { fails++; } return bad ? "FAIL" : "ok"; };

function pagePictures(page, doc) {
  const w = doc.defaultView;
  const walk = root => {
    for (const el of root.querySelectorAll("*")) {
      const cs = w.getComputedStyle(el);
      const urls = [cs.backgroundImage, cs.listStyleImage, cs.content, el.localName == "img" ? el.currentSrc || el.src : ""]
        .filter(v => v && BRAND.test(v));
      if (urls.length) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && Math.max(r.width, r.height) < 64) {
          say(`SMALL|page|${page}|${el.localName}${el.id ? "#" + el.id : ""}|${urls[0].slice(0, 80)}|${Math.round(r.width)}x${Math.round(r.height)}|${verdict(true)}`);
        }
      }
      if (el.shadowRoot) { walk(el.shadowRoot); }
    }
  };
  walk(doc);
}

const pages = ["about:newtab", "about:home", "about:preferences", "about:addons", "about:processes", "about:support",
               "about:logins", "about:about"];
for (const url of pages) {
  try {
    const tab = win.gBrowser.addTab(url, { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
    win.gBrowser.selectedTab = tab;
    await sleep(4000);
    const image = tab.getAttribute("image") || "";
    say(`SMALL|tab|${url}|${image || "none"}|${verdict(BRAND.test(image))}`);
    const icon = win.document.getElementById("identity-icon");
    const cs = win.getComputedStyle(icon);
    const drawn = cs.display != "none" && BRAND.test(cs.listStyleImage);
    say(`SMALL|chip|${url}|${cs.display}|${cs.listStyleImage.slice(0, 80)}|${verdict(drawn)}`);
    const doc = tab.linkedBrowser.contentDocument;
    if (doc) { pagePictures(url, doc); }
    win.gBrowser.removeTab(tab);
  } catch (e) { say(`SMALL-ERROR|${url}|${e}`); }
}
say(`SMALL-SUMMARY|${pages.length}|${fails}`);
