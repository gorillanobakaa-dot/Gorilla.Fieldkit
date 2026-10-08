// The browser's error pages, each produced by its REAL cause and drawn by Firefox itself into a picture (2026-10-08:
// opened by their address, about:neterror and about:certerror are empty - they only fill in from a failed load).
// Everything stays on this machine:
//   neterror        a connection refused by 127.0.0.1:49151 (nothing listens there), and port 9 (a port Firefox
//                   refuses outright)
//   certerror       the bad-cert page: HTTPS on 127.0.0.1 with a self-signed certificate made for the run
//   httpsonlyerror  HTTPS-Only Mode on, a plain-http address whose upgraded https load is refused. Not 127.0.0.1:
//                   HTTPS-Only never upgrades a loopback address whatever its prefs (nsHTTPSOnlyUtils::
//                   LoopbackOrLocalException; 2026-10-09: the first picture showed the plain site), so a made-up public
//                   name is resolved to 127.0.0.1 by network.dns.localDomains: no DNS query leaves the machine
//   unknown-about   an about: address nothing registers (the "address isn't valid" page): what a removed page shows
//   removed-page    about:fingerprintingprotection, removed by D-157-40: on a build without it, the same page
// Pictures go to <temp>/gprobe-shots/error-pages/<name>.png; the run prints where.
// Output lines:
//   ERRSHOT|<name>|<landed on (documentURI)>|<png path or why not>|<first 200 characters of the text>
// gprobe-server: echo-ua 8765
// gprobe-server: bad-cert 8766
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("ERRSHOT|error|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const { PathUtils, IOUtils } = win;
const SHOTS = PathUtils.join(PathUtils.tempDir, "gprobe-shots", "error-pages");
await IOUtils.makeDirectory(SHOTS, { ignoreExisting: true });
const sys = Services.scriptSecurityManager.getSystemPrincipal();
const clip = (s, n) => String(s || "").replace(/[\r\n|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, n);

async function textOf(browser) {
  // the error card is a web component in a content process: ask the page itself through a frame script
  return new Promise(resolve => {
    const mm = browser.messageManager;
    const done = m => { mm.removeMessageListener("gprobe:err", done); resolve(m.data); };
    mm.addMessageListener("gprobe:err", done);
    mm.loadFrameScript("data:application/javascript," + encodeURIComponent(`
      const parts = [];
      const walk = r => {
        const tw = content.document.createTreeWalker(r, 4);
        for (let n = tw.nextNode(); n; n = tw.nextNode()) { if (n.data.trim() && n.parentElement && n.parentElement.getClientRects().length) { parts.push(n.data.trim()); } }
        for (const el of r.querySelectorAll("*")) { if (el.shadowRoot) { walk(el.shadowRoot); } }
      };
      walk(content.document.body || content.document.documentElement);
      sendAsyncMessage("gprobe:err", { uri: content.document.documentURI, text: parts.join(" ") });`), false);
    wait(() => resolve({ uri: "?", text: "" }), 5000);
  });
}

async function shoot(name, url) {
  const tab = win.gBrowser.addTab("about:blank", { triggeringPrincipal: sys });
  win.gBrowser.selectedTab = tab;
  await sleep(500);
  tab.linkedBrowser.fixupAndLoadURIString(url, { triggeringPrincipal: sys });
  await sleep(5000);
  const r = await textOf(tab.linkedBrowser);
  let where = "";
  try {
    const d = win.document.documentElement;
    const bmp = await win.browsingContext.currentWindowGlobal.drawSnapshot(new win.DOMRect(0, 0, d.clientWidth, d.clientHeight), 1, "black");
    const cv = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
    cv.width = bmp.width; cv.height = bmp.height; cv.getContext("2d").drawImage(bmp, 0, 0);
    const blob = await new Promise(res => cv.toBlob(res, "image/png"));
    where = PathUtils.join(SHOTS, `${name}.png`);
    await IOUtils.write(where, new Uint8Array(await blob.arrayBuffer()));
  } catch (e) { where = "(no picture: " + clip(String(e), 100) + ")"; }
  say(`ERRSHOT|${name}|${clip(r.uri, 100)}|${where}|${clip(r.text, 200)}`);
  win.gBrowser.removeTab(tab);
  await sleep(300);
}

await shoot("neterror-connection-refused", "http://127.0.0.1:49151/");   // nothing listens there
await shoot("neterror-restricted-port", "http://127.0.0.1:9/");          // a port Firefox refuses outright
await shoot("certerror-self-signed", "https://127.0.0.1:8766/");
const PROBE_HOST = "gorilla-probe.example.com";               // resolved locally, see the header
Services.prefs.setStringPref("network.dns.localDomains", PROBE_HOST);
Services.prefs.setBoolPref("dom.security.https_only_mode", true);
await shoot("httpsonlyerror-plain-http-site", `http://${PROBE_HOST}:49151/`);   // https on 49151: refused
Services.prefs.clearUserPref("dom.security.https_only_mode");
Services.prefs.clearUserPref("network.dns.localDomains");
await shoot("neterror-unknown-about-address", "about:gorilla-no-such-page");
await shoot("removed-page-fingerprintingprotection", "about:fingerprintingprotection");
say(`ERRSHOT|folder|${SHOTS}||`);
