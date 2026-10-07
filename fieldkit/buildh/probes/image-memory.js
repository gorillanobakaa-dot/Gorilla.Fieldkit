// What the build's own pictures cost in memory, per page, measured by Firefox's memory reporter (the numbers
// about:memory shows), in every process. Born 2026-10-07: the Gorilla logo was meant to be ONE image referenced by
// the whole theme (--gorilla-master-icon), but about 30 places load about-logo.svg (a 1400x1400 photo inside an
// SVG, 3.9 MB), about-logo.png or its 2x PNG directly, and each URL is decoded on its own.
// For each page: open it in a fresh tab, let it settle, then report every decoded image whose URL is chrome:// or
// resource:// (the build's own pictures), with the process it lives in and its decoded bytes.
// Output lines:
//   IMG|<page>|<process>|<url>|<WxH>|<bytes>
//   IMG-PAGE|<page>|<images>|<total bytes>
//   IMG-PICTURE|<page>|<png path>
//   IMG-ERROR|<what>|<message>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("IMG-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const mgr = Cc["@mozilla.org/memory-reporter-manager;1"].getService(Ci.nsIMemoryReporterManager);

function report() {
  return new Promise(resolve => {
    const rows = [];
    const handle = (process, path, kind, units, amount) => {
      if (units != Ci.nsIMemoryReporter.UNITS_BYTES || !path.includes("images/")) { return; }
      const m = path.match(/image\((\d+x\d+), ([^)]*?)\)/);
      if (!m || !/^(chrome|resource|moz-extension):/.test(m[2])) { return; }
      rows.push({ process: process || "parent", url: m[2], size: m[1], path, amount });
    };
    mgr.getReports(handle, null, () => resolve(rows), null, false);
  });
}

function summarise(page, rows) {
  // the reporter splits one image into several leaves (raster/used/..., vector/..., source/...): add them up
  const by = new Map();
  for (const r of rows) {
    const key = `${r.process.replace(/ \(pid \d+\)/, "")}|${r.url}|${r.size}`;
    by.set(key, (by.get(key) || 0) + r.amount);
  }
  let total = 0;
  for (const [key, bytes] of [...by].sort((a, b) => b[1] - a[1])) {
    const [proc, url, size] = key.split("|");
    say(`IMG|${page}|${proc}|${url}|${size}|${bytes}`);
    total += bytes;
  }
  say(`IMG-PAGE|${page}|${by.size}|${total}`);
}

// a picture of each page, drawn by Firefox itself (no desktop screenshot), to judge sharpness by eye
const IO = win.IOUtils;                 // not defined in the autoconfig sandbox; the chrome window exposes it
const stamp = Date.now();
async function picture(page, tab) {
  try {
    const bmp = await tab.linkedBrowser.browsingContext.currentWindowGlobal.drawSnapshot(null, 1, "black");
    const c = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
    c.width = bmp.width; c.height = bmp.height;
    c.getContext("2d").drawImage(bmp, 0, 0);
    const blob = await new Promise(r => c.toBlob(r, "image/png"));
    const f = Services.dirsvc.get("TmpD", Ci.nsIFile);
    f.append("gprobe-shots");
    if (!f.exists()) { f.create(Ci.nsIFile.DIRECTORY_TYPE, 0o755); }
    f.append(`image-memory-${stamp}-${page.replace(/[^a-z]/g, "")}.png`);
    await IO.write(f.path, new Uint8Array(await blob.arrayBuffer()));
    say(`IMG-PICTURE|${page}|${f.path}`);
  } catch (e) { say(`IMG-ERROR|picture ${page}|${e}`); }
}

const pages = ["about:blank", "about:newtab", "about:home", "about:preferences", "about:addons", "about:privatebrowsing"];
for (const url of pages) {
  try {
    // a fresh window state per page: close every other tab, minimise memory, then open the page
    for (const t of [...win.gBrowser.tabs].slice(1)) { win.gBrowser.removeTab(t); }
    await new Promise(r => mgr.minimizeMemoryUsage(r));
    const tab = win.gBrowser.addTab(url, { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
    win.gBrowser.selectedTab = tab;
    await sleep(6000);
    summarise(url, await report());
    if (url != "about:blank") { await picture(url, tab); }
  } catch (e) { say(`IMG-ERROR|${url}|${e}`); }
}
