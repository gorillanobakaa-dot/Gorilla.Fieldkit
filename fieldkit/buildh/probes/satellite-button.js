// Gorilla.Satellite mode toolbar button: is it next to the address bar, with its words, does its menu set the
// level, and does each level carry its explanation on hover? Ends with a picture of the toolbar drawn by Firefox
// itself (drawSnapshot, no desktop screenshot), once per level, for a person to look at.
// (2026-10-04, maintainer: "easy to find and quite prominent", in the empty space next to the address bar)
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const { PathUtils, IOUtils } = win; // the autoconfig sandbox has neither; the chrome window has both
const sleep = ms => new Promise(r => wait(r, ms));
win.resizeTo(1400, 800);
await sleep(1500);
const doc = win.document;
const button = doc.getElementById("gorilla-satellite-button");
say("button found:", !!button);
if (!button) { return; }
const prev = button.previousElementSibling;
say("placed in:", button.parentNode.id || button.parentNode.localName, "| after:", prev ? prev.id : "(first)",
    "| shown as:", button.getAttribute("cui-areatype"), button.hasAttribute("overflowedItem") ? "(overflowed)" : "");
const text = button.querySelector(".toolbarbutton-text");
const icon = button.querySelector(".toolbarbutton-icon");
say("words:", button.getAttribute("label"), "| visible:", text ? win.getComputedStyle(text).display != "none" : "no text box",
    "| width px:", Math.round(button.getBoundingClientRect().width));
say("icon:", win.getComputedStyle(button).listStyleImage, "| icon box px:", icon ? Math.round(icon.getBoundingClientRect().width) : "none");
say("hover on button:", (button.getAttribute("tooltiptext") || "(none)").slice(0, 100));

async function picture(tag) {
  const bar = doc.getElementById("nav-bar").getBoundingClientRect();
  const bmp = await win.browsingContext.currentWindowGlobal.drawSnapshot(
    new win.DOMRect(bar.x, bar.y, bar.width, bar.height), 1, "white");
  const c = doc.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
  c.width = bmp.width; c.height = bmp.height;
  c.getContext("2d").drawImage(bmp, 0, 0);
  const blob = await new Promise(r => c.toBlob(r, "image/png"));
  const dir = PathUtils.join(PathUtils.tempDir, "gprobe-shots");
  await IOUtils.makeDirectory(dir, { ignoreExisting: true });
  const out = PathUtils.join(dir, `satellite-button-${tag}.png`);
  await IOUtils.write(out, new Uint8Array(await blob.arrayBuffer()));
  say("picture:", out);
}
await picture("level0");

const popup = button.querySelector("menupopup");
const shown = new Promise(r => popup.addEventListener("popupshown", r, { once: true }));
button.open = true;
await Promise.race([shown, sleep(3000)]);
say("menu open:", popup.state);
const items = [...popup.querySelectorAll("menuitem")];
{ const cs = win.getComputedStyle(popup); say("menu colours: background", cs.backgroundColor, "| text", cs.color, "| appearance", cs.appearance,
    "| content background", cs.getPropertyValue("--panel-background-color")); }
for (const it of items) {
  say("  item", it.getAttribute("value") ?? "-", "|", it.getAttribute("label"), it.getAttribute("checked") == "true" ? "[ticked]" : "", it.hidden ? "[HIDDEN]" : "",
      "| hover:", (it.getAttribute("tooltiptext") || "(none)").slice(0, 70));
}
// does the hover text really appear? move the mouse onto "Very slow link" and wait for the tooltip
const slow = items.find(i => i.getAttribute("value") == "2");
// tooltiptext is shown by the window's default tooltip (anonymous, not #aHTMLTooltip): catch any tooltip
let tip = null;
const tipShown = new Promise(r => win.addEventListener("popupshown", function seen(e) {
  const t = e.originalTarget;
  if (t.localName == "tooltip") { tip = t; win.removeEventListener("popupshown", seen, true); r(); }
}, true));
const r = slow.getBoundingClientRect();
// a DOM mousemove sent by chrome code is trusted and reaches the tooltip listener; the real (OS) cursor never
// moves, so this runs without taking the owner's mouse (native events would, see announce-before-keyboard tests)
for (const dx of [5, 10, 15]) {
  const x = r.left + r.width / 2 + dx, y = r.top + r.height / 2;
  slow.dispatchEvent(new win.MouseEvent("mousemove", { bubbles: true, view: win, clientX: x, clientY: y,
    screenX: win.mozInnerScreenX + x, screenY: win.mozInnerScreenY + y }));
  await sleep(150);
}
const tipped = await Promise.race([tipShown.then(() => true), sleep(4000).then(() => false)]);
say("hover text appeared:", tipped, tipped ? "| " + (tip.getAttribute("label") || tip.textContent || "").slice(0, 80) : "");
if (tip && tip.state == "open") { tip.hidePopup(); }

for (const level of ["2", "1", "0"]) {
  if (popup.state != "open") {
    const again = new Promise(r => popup.addEventListener("popupshown", r, { once: true }));
    button.open = true;
    await Promise.race([again, sleep(3000)]);
  }
  popup.activateItem(items.find(i => i.getAttribute("value") == level));
  await sleep(800);
  const d = Services.prefs.getDefaultBranch("");
  say(`chose ${level}: pref ${Services.prefs.getIntPref("gorilla.linkmode")}, words "${button.getAttribute("label")}", ` +
      `level attr ${button.getAttribute("gorilla-level")}, background ${win.getComputedStyle(text).backgroundColor}, ` +
      `images ${d.getIntPref("permissions.default.image")}, menu ${popup.state}`);
  if (level != "0") { await picture("level" + level); }
}
