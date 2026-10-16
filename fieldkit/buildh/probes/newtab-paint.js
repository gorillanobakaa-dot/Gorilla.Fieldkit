// Version: 1.0.0 · updated 26-10-15-22-29
// Does the new tab actually DRAW? Pixels, not text (2026-10-15: the owner's 158 new tab was black, sometimes, while
// every text-reading probe passed: the page's text and picture boxes exist even when nothing is visible).
// Opens the new tab the ways a person does (the first tab at start, the + button, again and again), snapshots what
// each one paints (WindowGlobalParent.drawSnapshot, the page's own rendering), and counts the pixels that are not
// black. One line per tab: NTPAINT|<how>|<url>|<lit pixels>|<total>|<verdict>. lit < 0.5% of the page = BLACK.
const win = Services.wm.getMostRecentWindow("navigator:browser");
const gb = win.gBrowser;
const sleep = ms => new Promise(r => win.setTimeout(r, ms));

async function settled(browser, ms = 12000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const u = browser.currentURI && browser.currentURI.spec;
    if (u && u.startsWith("about:") && u !== "about:blank" && !browser.webProgress?.isLoadingDocument) {
      return u;
    }
    await sleep(200);
  }
  return browser.currentURI ? browser.currentURI.spec : "?";
}

async function lit(browser) {
  const wg = browser.browsingContext && browser.browsingContext.currentWindowGlobal;
  if (!wg) return { lit: -1, total: 0, why: "no window global" };
  const bmp = await wg.drawSnapshot(null, 0.25, "rgb(0,0,0)");
  const c = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
  c.width = bmp.width; c.height = bmp.height;
  const cx = c.getContext("2d");
  cx.drawImage(bmp, 0, 0);
  const d = cx.getImageData(0, 0, c.width, c.height).data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) if (d[i] + d[i + 1] + d[i + 2] > 60) n++;
  return { lit: n, total: d.length / 4 };
}

async function measure(how, browser) {
  const url = await settled(browser);
  await sleep(2500);                                  // what a person sees after a couple of seconds
  const r = await lit(browser);
  const verdict = r.total && r.lit / r.total >= 0.005 ? "DRAWN" : "BLACK";
  say(`NTPAINT|${how}|${url}|${r.lit}|${r.total}|${verdict}`);
}

await measure("first tab at start", gb.selectedBrowser);
for (let i = 1; i <= 5; i++) {
  if (win.BrowserCommands && win.BrowserCommands.openTab) {
    win.BrowserCommands.openTab();                    // the + button: may use the preloaded new tab
  } else {
    win.BrowserOpenTab();
  }
  await measure(`new tab ${i} (+ button)`, gb.selectedBrowser);
}
gb.selectedTab = gb.addTrustedTab("about:home");
await measure("about:home opened by address", gb.selectedBrowser);
