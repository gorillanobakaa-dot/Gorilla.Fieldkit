// Why a surface is not painted in the theme's colours: what really paints it (2026-10-18). Born when build 2 shipped
// a theme block that only set variables: the search-mode list and the Satellite menu stayed the system's light Menu
// colour (a popup with appearance other than none is drawn by the system), app-menu hovers read black and the app
// menu still cut a label at 30em. For each surface: the popup's appearance, background and the variables it reads,
// one item's painted colours, the hover after an InspectorUtils :hover lock (or the error that lock raised), and the
// width the label needs against the width it has.
//   DIAG|<surface>|<key>|<value>
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("DIAG|window|error|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const sleep = ms => new Promise(r => wait(r, ms));
const doc = win.document;
win.resizeTo(1400, 800);
await sleep(1500);
const cs = (el, w = win) => w.getComputedStyle(el);
function popupFacts(surface, p) {
  const s = cs(p);
  say(`DIAG|${surface}|element|${p.localName}${p.id ? "#" + p.id : ""}.${[...p.classList].join(".")} type=${p.getAttribute("type")}`);
  for (const k of ["appearance", "background-color", "color", "width", "min-width", "max-width"]) {
    say(`DIAG|${surface}|${k}|${s.getPropertyValue(k)}`);
  }
  for (const v of ["--panel-background", "--panel-color", "--panel-border-color", "--background-color-box",
                   "--panel-list-background-color", "--menu-panel-width", "--menu-panel-width-wide", "--color-accent-primary"]) {
    say(`DIAG|${surface}|${v}|${s.getPropertyValue(v).trim()}`);
  }
  const part = p.shadowRoot && p.shadowRoot.querySelector("[part~='content'], .panel-arrowcontent, slot");
  if (part) {
    const ps = cs(part);
    say(`DIAG|${surface}|content part|${part.localName} bg=${ps.backgroundColor} appearance=${ps.appearance} width=${ps.width}`);
  }
}
async function itemFacts(surface, it) {
  const painter = (it.localName == "panel-item" && it.shadowRoot && it.shadowRoot.querySelector("button")) || it;
  const s = cs(painter);
  say(`DIAG|${surface}|item|${(it.getAttribute("label") || it.textContent || "").trim().slice(0, 50)} <${painter.localName}> bg=${s.backgroundColor} color=${s.color} appearance=${s.appearance}`);
  const IU = win.InspectorUtils;
  say(`DIAG|${surface}|InspectorUtils|${typeof IU}`);
  try {
    if (painter.localName == "menuitem" || painter.localName == "menu") {
      painter.setAttribute("_moz-menuactive", "true");
      await sleep(250);              // a transition (even 0.01 ms) reports its first frame when read at once
      say(`DIAG|${surface}|hover (_moz-menuactive)|bg=${cs(painter).backgroundColor} color=${cs(painter).color}`);
      painter.removeAttribute("_moz-menuactive");
    } else {
      IU.addPseudoClassLock(painter, ":hover");
      await sleep(250);
      say(`DIAG|${surface}|hover (:hover lock)|bg=${cs(painter).backgroundColor} color=${cs(painter).color}`);
      IU.removePseudoClassLock(painter, ":hover");
    }
  } catch (x) { say(`DIAG|${surface}|hover error|${x}`); }
  const lab = it.querySelector(".toolbarbutton-text, .menu-text, .menu-iconic-text, label") ||
              (it.shadowRoot && it.shadowRoot.querySelector("label, .toolbarbutton-text")) || it;
  say(`DIAG|${surface}|label width|needs ${lab.scrollWidth} has ${lab.clientWidth} (item ${Math.round(it.getBoundingClientRect().width)})`);
}
async function open(p, opener) {
  const shown = new Promise(r => p.addEventListener("popupshown", r, { once: true }));
  opener();
  await Promise.race([shown, sleep(3000)]);
  await sleep(300);
}
// the search-mode list
try {
  const btn = doc.querySelector("#urlbar .searchmode-switcher") || doc.querySelector(".searchmode-switcher");
  btn.click();
  let sm = null;
  for (let i = 0; i < 20 && !(sm = doc.querySelector(".searchmode-switcher-panel")); i++) { await sleep(150); }
  for (let i = 0; i < 20 && sm && sm.state != "open"; i++) { await sleep(150); }
  await sleep(300);
  popupFacts("search-mode list", sm);
  const it = sm.querySelector("panel-item");
  if (it) { await itemFacts("search-mode list", it); }
  sm.hidePopup();
  await sleep(300);
} catch (x) { say(`DIAG|search-mode list|error|${x}`); }
// the Satellite toolbar menu (and any toolbar-button menupopup)
try {
  const b = doc.getElementById("gorilla-satellite-button");
  const p = b && b.querySelector(":scope > menupopup");
  if (p) {
    await open(p, () => { b.open = true; });
    popupFacts("satellite menu", p);
    const it = p.querySelector("menuitem");
    if (it) { await itemFacts("satellite menu", it); }
    p.hidePopup();
    await sleep(300);
  } else { say("DIAG|satellite menu|error|no gorilla-satellite-button menupopup"); }
} catch (x) { say(`DIAG|satellite menu|error|${x}`); }
// the app menu
try {
  const panel = doc.getElementById("appMenu-popup");
  await open(panel, () => win.PanelUI.show());
  popupFacts("app menu", panel);
  const view = doc.getElementById("appMenu-mainView");
  say(`DIAG|app menu|mainView|width=${cs(view).width} min=${cs(view).minWidth} max=${cs(view).maxWidth}`);
  const items = [...doc.querySelectorAll("#appMenu-mainView toolbarbutton.subviewbutton")];
  const longest = items.filter(i => i.getBoundingClientRect().width).sort((a, b) =>
    (b.getAttribute("label") || "").length - (a.getAttribute("label") || "").length)[0];
  if (longest) { await itemFacts("app menu", longest); }
  panel.hidePopup();
} catch (x) { say(`DIAG|app menu|error|${x}`); }
