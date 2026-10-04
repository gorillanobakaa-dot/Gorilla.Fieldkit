// Real sites over the real link: how many bytes does each page cost (a) as shipped, (b) in Very slow link with the
// desktop page, (c) in Very slow link with the mobile page (D-157-33)? Bytes are counted in the parent process
// (http-on-stop-request, transferSize of every request the tab made), with the cache emptied before every load so
// nothing is served from disk. The time at 5 KB/s is derived (bytes / 5000), not measured on a 5 KB/s link.
// Spends the owner's data (phone hotspot): stops before a load once BUDGET_MB has been used
// (maintainer approval 2026-10-04: "D yes", cap 50 MB).
const SITES = [
  "https://en.wikipedia.org/wiki/Starlink",
  "https://www.bbc.co.uk/news",
  "https://www.theguardian.com/international",
  "https://edition.cnn.com/",
  "https://www.reuters.com/",
  "https://duckduckgo.com/?q=satellite+internet",
];
// CAP_MB: the owner's data cap for one run (approval 2026-10-04: 50 MB; the probe stops loading at the cap, and
// starts no new page past BUDGET_MB). Every request counts, background ones (filter lists) included.
const CAP_MB = 200; // owner 2026-10-04 (second run): "spend as much data as possible", 200 MB as a runaway guard
const BUDGET_MB = CAP_MB - 5;
// [name, gorilla.linkmode, mobile pages, JavaScript on]. Run 1 (2026-10-04) measured the first three; mobile pages
// barely helped (most sites now send one responsive page), so run 2 measures JavaScript off.
const MODES = [
  ["very-slow desktop no-js", 2, false, false],
  ["very-slow mobile no-js", 2, true, false],
];
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const { PathUtils, IOUtils } = win;
const SHOTS = PathUtils.join(PathUtils.tempDir, "gprobe-shots", "realsite");
const sleep = ms => new Promise(r => wait(r, ms));
const sys = Services.scriptSecurityManager.getSystemPrincipal();
let total = 0;
let counting = null;
const obs = {
  observe(subject) {
    try {
      const ch = subject.QueryInterface(Ci.nsIHttpChannel);
      const top = ch.loadInfo.browsingContext?.top;
      const size = ch.transferSize || 0;
      total += size;
      if (counting && top && top.id == counting.bc) { counting.bytes += size; counting.requests++; }
      // the cap is hard: a page still loading when it is reached is stopped there (2026-10-04: checking only
      // before each page let one 8 MB page and the filter-list downloads carry the run to 51 MB of a 50 MB cap)
      if (total / 2 ** 20 >= CAP_MB && counting) { counting.capped = true; counting.tab.linkedBrowser.stop(); }
    } catch (e) {}
  },
};
Services.obs.addObserver(obs, "http-on-stop-request");
try {
  await IOUtils.makeDirectory(SHOTS, { ignoreExisting: true });
  for (const [name, level, mobile, js] of MODES) {
    Services.prefs.setBoolPref("javascript.enabled", js);
    Services.prefs.setBoolPref("gorilla.linkmode.mobile_pages", mobile);
    Services.prefs.setIntPref("gorilla.linkmode", level);
    await sleep(500);
    for (const url of SITES) {
      if (total / 2 ** 20 > BUDGET_MB) { say(`STOPPED: ${(total / 2 ** 20).toFixed(1)} MB used, budget reached`); return; }
      Services.cache2.clear();
      const tab = win.gBrowser.addTab("about:blank", { triggeringPrincipal: sys });
      win.gBrowser.selectedTab = tab;
      await sleep(300);
      counting = { bc: tab.linkedBrowser.browsingContext.id, bytes: 0, requests: 0, tab };
      const t0 = Date.now();
      tab.linkedBrowser.fixupAndLoadURIString(url, { triggeringPrincipal: sys });
      // wait for the load to finish (busy flag gone) or 40 s, then 3 s more for late requests
      await sleep(1000);
      while (tab.hasAttribute("busy") && Date.now() - t0 < 40000) { await sleep(250); }
      const loadS = (Date.now() - t0) / 1000;
      await sleep(3000);
      const c = counting; counting = null;
      // a picture of the top of the page, drawn by Firefox (no desktop screenshot), to judge whether it is usable
      try {
        const bmp = await tab.linkedBrowser.browsingContext.currentWindowGlobal.drawSnapshot(
          new win.DOMRect(0, 0, 1200, 900), 0.6, "white");
        const cv = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
        cv.width = bmp.width; cv.height = bmp.height; cv.getContext("2d").drawImage(bmp, 0, 0);
        const blob = await new Promise(r => cv.toBlob(r, "image/png"));
        const host = Services.io.newURI(url).host.replace(/^www\./, "").split(".")[0];
        await IOUtils.write(PathUtils.join(SHOTS, `${name.replace(/ /g, "_")}-${host}.png`), new Uint8Array(await blob.arrayBuffer()));
      } catch (e) { say("no picture:", String(e)); }
      say(`${name} | ${url} | ${(c.bytes / 1024).toFixed(0)} KB in ${c.requests} requests | loaded in ${loadS.toFixed(1)} s here` +
          ` | at 5 KB/s about ${(c.bytes / 5000).toFixed(0)} s | ${tab.linkedBrowser.contentTitle.slice(0, 40)}` + (c.capped ? " | STOPPED AT THE DATA CAP" : ""));
      if (c.capped) { win.gBrowser.removeTab(tab); return; }
      win.gBrowser.removeTab(tab);
    }
  }
} finally {
  Services.obs.removeObserver(obs, "http-on-stop-request");
  Services.prefs.clearUserPref("javascript.enabled");
  say("pictures:", SHOTS);
  say(`data used by this probe: ${(total / 2 ** 20).toFixed(1)} MB (all requests, including background ones)`);
}
