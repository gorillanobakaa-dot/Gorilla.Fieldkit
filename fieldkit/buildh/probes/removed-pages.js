// The pages the owner's register removes are really gone from the browser (D-157-40): each about: address lands on the
// network error page with code malformedURI ("Hmm. That address doesn't look right." - the page any unknown about:
// address gets), and each removed page's own chrome:// file opens nothing (its files are not packaged).
// The harness fills ADDRESSES before the run (removedpages.py).
// Output lines:
//   GONE|<address>|<landed: the document's URI>|<first 160 characters of what the page shows>
//   GONE-DONE|<count>
const ADDRESSES = [];
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("GONE|error|no browser window|"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const sys = Services.scriptSecurityManager.getSystemPrincipal();
const clip = (s, n) => String(s || "").replace(/[\r\n|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, n);

async function read(browser) {
  // the page may be an error page in a content process: ask it through a frame script
  return new Promise(resolve => {
    const mm = browser.messageManager;
    const done = m => { mm.removeMessageListener("gprobe:gone", done); resolve(m.data); };
    mm.addMessageListener("gprobe:gone", done);
    mm.loadFrameScript("data:application/javascript," + encodeURIComponent(`
      const d = content.document;
      sendAsyncMessage("gprobe:gone", { uri: d.documentURI, text: (d.body ? d.body.innerText : "") || "" });`), false);
    wait(() => resolve({ uri: browser.currentURI ? browser.currentURI.spec : "?", text: "" }), 5000);
  });
}

for (const address of ADDRESSES) {
  const tab = win.gBrowser.addTab("about:blank", { triggeringPrincipal: sys });
  win.gBrowser.selectedTab = tab;
  await sleep(300);
  try {
    tab.linkedBrowser.fixupAndLoadURIString(address, { triggeringPrincipal: sys });
  } catch (e) {
    say(`GONE|${address}|refused to load: ${clip(e, 100)}|`);
    win.gBrowser.removeTab(tab);
    continue;
  }
  await sleep(2500);
  const r = await read(tab.linkedBrowser);
  say(`GONE|${address}|${clip(r.uri, 160)}|${clip(r.text, 160)}`);
  win.gBrowser.removeTab(tab);
  await sleep(200);
}
say(`GONE-DONE|${ADDRESSES.length}`);
