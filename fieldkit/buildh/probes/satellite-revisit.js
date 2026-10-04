// Very slow link opens a page it already has from the cache, without asking the site (D-157-33 B), even when the page
// says "private, max-age=0, must-revalidate"; Reload still asks. Needs a local page on 127.0.0.1:8766 that sends that
// header and logs every request (the netbench B1 "warm" visit cannot show this: each visit there has a new address).
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const sys = Services.scriptSecurityManager.getSystemPrincipal();
const URL = "http://127.0.0.1:8766/front-page";
async function open(tag) {
  const tab = win.gBrowser.addTab(URL, { triggeringPrincipal: sys });
  win.gBrowser.selectedTab = tab;
  for (let i = 0; i < 40 && tab.linkedBrowser.contentTitle != "revisit"; i++) { await sleep(250); }
  say(tag, "| loaded:", tab.linkedBrowser.contentTitle == "revisit");
  return tab;
}
for (const level of [0, 2]) {
  Services.prefs.setIntPref("gorilla.linkmode", level);
  await sleep(500);
  Services.cache2.clear();
  win.gBrowser.removeTab(await open(`level ${level} first visit`));
  const t = await open(`level ${level} same page again`);
  t.linkedBrowser.reload();
  await sleep(2000);
  say(`level ${level} reload done`);
  win.gBrowser.removeTab(t);
  say(`MARK level ${level} end`);
}
Services.prefs.setIntPref("gorilla.linkmode", 0);
