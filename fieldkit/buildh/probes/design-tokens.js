// Every design token as the build really computes it (2026-10-07, theme item 1 of build 28). The token files
// (toolkit/themes/shared/design-system/dist/tokens-*.css) had been replaced by 155-era copies: a layer renamed,
// two @media/@layer openers dropped (unbalanced braces), --color-gray-0 renamed to gray-05 and some 40 tokens 157
// uses deleted. No check looked at what the browser computed from them, so nothing showed it. This probe does: for
// the browser frame, Settings and Add-ons it reports each token's computed value (var() already substituted) and the
// branch of light-dark() the surface's colour scheme picks. Run it before and after a token change (probe file=...)
// and compare: every difference must be intended. Resolving through an element's style was tried and read back
// currentColor for every token in this context, so the scheme branch is taken in plain JavaScript.
// Names: every token the loaded token files declare, plus the names in gprobe-token-names.txt beside firefox.exe
// when present (add=gprobe-token-names.txt=<file>), so a token a broken file lost is still asked for.
// Output lines:  TOK|<surface>|<name>|<computed value>|<value in this scheme>   and   TOK-SURFACE|<surface>|<scheme>|<count>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("TOK-ERROR|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const IO = win.IOUtils;                 // not defined in the autoconfig sandbox; the chrome window exposes it

const names = new Set();
for (const f of ["brand", "platform", "shared"]) {
  try {
    const text = await (await win.fetch(`chrome://global/skin/design-system/tokens-${f}.css`)).text();
    for (const m of text.matchAll(/(--[a-z0-9-]+)\s*:/gi)) { names.add(m[1]); }
  } catch (e) { say(`TOK-ERROR|tokens-${f}.css|${e}`); }
}
try {
  const f = Services.dirsvc.get("GreD", Ci.nsIFile);                  // beside firefox.exe (XCurProcD is browser)
  f.append("gprobe-token-names.txt");
  const before = names.size;
  if (f.exists()) {
    for (const n of (await IO.readUTF8(f.path)).split(/\s+/)) { if (n.startsWith("--")) { names.add(n); } }
  }
  say(`TOK-NAMES|${before} from the loaded token files|${names.size - before} more from gprobe-token-names.txt (${f.exists() ? "read" : "absent"})`);
} catch (e) { say(`TOK-ERROR|names file|${e}`); }
const sorted = [...names].sort();

// light-dark(a, b) -> a or b, innermost first, splitting on the top-level comma only
function pick(v, dark) {
  for (let guard = 0; guard < 20; guard++) {
    const at = v.lastIndexOf("light-dark(");
    if (at < 0) { return v; }
    let depth = 0, comma = -1, end = -1;
    for (let i = at + 11; i < v.length; i++) {
      const c = v[i];
      if (c == "(") { depth++; } else if (c == ")") { if (depth == 0) { end = i; break; } depth--; } else if (c == "," && depth == 0 && comma < 0) { comma = i; }
    }
    if (comma < 0 || end < 0) { return v; }
    v = v.slice(0, at) + (dark ? v.slice(comma + 1, end) : v.slice(at + 11, comma)).trim() + v.slice(end + 1);
  }
  return v;
}

function report(surface, w) {
  const d = w.document;
  const root = d.documentElement;
  const cs = w.getComputedStyle(root);
  const dark = cs.colorScheme.includes("dark") && (!cs.colorScheme.includes("light") || w.matchMedia("(prefers-color-scheme: dark)").matches);
  let n = 0;
  for (const name of sorted) {
    const v = cs.getPropertyValue(name).trim().replace(/\s+/g, " ");
    say(`TOK|${surface}|${name}|${v.slice(0, 160) || "(unset)"}|${v ? pick(v, dark).slice(0, 80) : "(unset)"}`);
    n++;
  }
  say(`TOK-SURFACE|${surface}|${dark ? "dark" : "light"}|${n}`);
}

// a picture of each surface, drawn by Firefox itself (no desktop screenshot), for a person to compare
const stamp = Date.now();
async function picture(surface, wgp, rect) {
  try {
    const bmp = await wgp.drawSnapshot(rect, 1, "white");
    const c = win.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
    c.width = bmp.width; c.height = bmp.height;
    c.getContext("2d").drawImage(bmp, 0, 0);
    const blob = await new Promise(r => c.toBlob(r, "image/png"));
    const dir = Services.dirsvc.get("TmpD", Ci.nsIFile);
    dir.append("gprobe-shots");
    if (!dir.exists()) { dir.create(Ci.nsIFile.DIRECTORY_TYPE, 0o755); }
    dir.append(`design-tokens-${stamp}-${surface}.png`);
    await IO.write(dir.path, new Uint8Array(await blob.arrayBuffer()));
    say(`TOK-PICTURE|${surface}|${dir.path}`);
  } catch (e) { say(`TOK-ERROR|picture ${surface}|${e}`); }
}

report("frame", win);
const box = win.document.getElementById("navigator-toolbox").getBoundingClientRect();
await picture("frame", win.browsingContext.currentWindowGlobal, new win.DOMRect(box.x, box.y, box.width, box.height));
for (const [surface, url] of [["settings", "about:preferences#general"], ["addons", "about:addons"]]) {
  const tab = win.gBrowser.addTab(url, { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
  win.gBrowser.selectedTab = tab;
  for (let i = 0; i < 80 && tab.linkedBrowser.contentDocument?.readyState != "complete"; i++) { await sleep(250); }
  await sleep(1500);
  const cw = tab.linkedBrowser.contentWindow;
  if (cw && cw.document) {
    report(surface, cw);
    await picture(surface, tab.linkedBrowser.browsingContext.currentWindowGlobal, null);
  } else { say(`TOK-ERROR|${surface}|page not reachable from the parent`); }
  win.gBrowser.removeTab(tab);
}
