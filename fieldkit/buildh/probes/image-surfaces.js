// Every decoded copy (surface) of the build's own pictures, as Firefox's memory reporter lists them: size, flags and
// bytes per process. The question behind "why is this picture decoded at full size here" (2026-10-08: on about:addons
// the 1400 px master logo was decoded in full in the main process, 11.9 MB, for one 650 px watermark; a decode is
// shrunk to the drawn size only when the draw asks for high-quality scaling).
// Pages: gprobe-branding-pages.txt beside firefox.exe (one per line) when present, else about:addons.
// Output lines:
//   SURF|<page>@<6s|30s|75s|minimised>|<process>|<image url>|<image size>|<surface description from the reporter>|<bytes>
//   SURF-PAGE|<page>|<surfaces>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("SURF-ERROR|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const mgr = Cc["@mozilla.org/memory-reporter-manager;1"].getService(Ci.nsIMemoryReporterManager);
let pages = ["about:addons"];
try {
  const f = Services.dirsvc.get("GreD", Ci.nsIFile);
  f.append("gprobe-branding-pages.txt");
  if (f.exists()) { pages = (await win.IOUtils.readUTF8(f.path)).split(/\s+/).filter(Boolean); }
} catch (e) { say(`SURF-ERROR|pages file|${e}`); }

for (const url of pages) {
  const tab = win.gBrowser.addTab(url, { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
  win.gBrowser.selectedTab = tab;
  // a timeline: Firefox decodes a CSS picture once at its full size when it is first shown (nsImageRenderer::
  // PrepareImage -> StartDecodingWithResult -> RequestDecodeForSize(mSize)), then at the drawn size; the full copy is
  // left unlocked and the surface cache drops it after image.mem.surfacecache.min_expiration_ms (60 s) unused
  for (const [label, waitMs, minimise] of [["6s", 6000, false], ["30s", 24000, false], ["75s", 45000, false], ["minimised", 0, true]]) {
  await sleep(waitMs);
  if (minimise) { await new Promise(r => mgr.minimizeMemoryUsage(r)); await sleep(1000); }
  const rows = await new Promise(resolve => {
    const out = [];
    mgr.getReports((process, path, kind, units, amount) => {
      if (units != Ci.nsIMemoryReporter.UNITS_BYTES || !path.includes("images/")) { return; }
      const m = path.match(/image\((\d+x\d+), ([^)]*?branding[^)]*?)\)\/?(.*)$/);
      if (m) { out.push(`SURF|${url}@${label}|${(process || "parent").replace(/ \(pid \d+\)/, "")}|${m[2]}|${m[1]}|${m[3] || "-"}|${amount}`); }
    }, null, () => resolve(out), null, false);
  });
  for (const r of rows) { say(r); }
  say(`SURF-PAGE|${url}@${label}|${rows.length}`);
  }
}
