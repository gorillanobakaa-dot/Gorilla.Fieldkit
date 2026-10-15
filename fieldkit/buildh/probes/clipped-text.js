// Version: 1.1.0 · updated 26-10-15-16-03
// Clipped text in the browser's own dialogs: does every message fit, and if not, WHY.
// Born 2026-10-15: the owner's screenshot of web.whatsapp.com's "Leave page?" dialog showed its message cut to a
// sliver (needed 40 px, got 8 px; frame 124 px where stock Firefox 158 gives 156 px). Found by hand in eight steps,
// all of which this probe now does by itself (clipcheck.py explains each and compares with stock):
//   1. measure every text element in the opened dialog (need vs got)
//   2. hook SubDialog.prototype.resizeVertically: the dialog's height is measured ONCE there, so record the box of
//      every element AT THAT MOMENT, and again when the dialog has settled
//   3. an element whose box changed between the two moments was measured too early (the 2026-10-15 cause:
//      dialog#commonDialog padding 0 at measure time, 16 px after)
//   4. list the CSS transitions and animations running at measure time (document.getAnimations()): while a
//      transition runs, getComputedStyle returns the IN-BETWEEN value, so the rule says 16 px and the box is 0
//   5. name the CSS rule behind each property (InspectorUtils.getMatchingCSSRules + getRuleLine: sheet:line selector)
// Engine notes, learnt the hard way the same day:
//   - InspectorUtils is a WebIDL namespace of WINDOW globals: use win.InspectorUtils; it is not defined in this
//     autoconfig sandbox ("InspectorUtils is not defined")
//   - Firefox 158 has getMatchingCSSRules(el); the old getCSSStyleRules is gone. Its list mixes CSSRule and
//     InspectorDeclaration items: keep those with cssText; getRuleLine(rule) gives the line in its sheet
//   - a page that calls removeTab while a beforeunload dialog is up spins a nested event loop: call it from a timer
//     (wait(() => gB.removeTab(tab), 0)) or the probe never continues
// Output lines (parsed by clipcheck.py):
//   CLIP|surface|<surface>|<frame W>x<H>|<elements measured>
//   CLIP|frame|<surface>|<document height>|<frame height>
//   CLIP|text|<surface>|<element>|<needed px>|<given px>|<text>
//   CLIP|moved|<surface>|<element>|<property>|<at measure>|<settled>
//   CLIP|anim|<surface>|<element>|<kind>|<property or name>|<duration ms>
//   CLIP|rule|<surface>|<element>|<property>|<sheet:line>|<selector>|<declared value>
//   CLIP|box|<surface>|<moment>|<element>|<height>|<padding t/b>|<margin t/b>     (the tree, for a stock diff)
//   CLIP|error|<surface>|<message>         CLIP|pref|<name>|<value>
// Env GPROBE_PREFS="name=value;name=value" sets prefs first (an A/B without editing this file).
const win = Services.wm.getMostRecentWindow("navigator:browser");
if (!win) { say("CLIP|error|window|no browser window"); return; }
const { setTimeout: wait } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");
const { SubDialog } = ChromeUtils.importESModule("resource://gre/modules/SubDialog.sys.mjs");
const IU = win.InspectorUtils;
const sleep = ms => new Promise(r => wait(r, ms));
const gB = win.gBrowser;
Services.prefs.setBoolPref("dom.require_user_interaction_for_beforeunload", false);
for (const kv of (Services.env.get("GPROBE_PREFS") || "").split(";").filter(Boolean)) {
  const [k, v] = kv.split("=");
  if (/^-?\d+$/.test(v)) { Services.prefs.setIntPref(k, Number(v)); }
  else if (v === "true" || v === "false") { Services.prefs.setBoolPref(k, v === "true"); }
  else { Services.prefs.setStringPref(k, v); }
  say(`CLIP|pref|${k}|${v}`);
}
const LONG = "This message is long on purpose so that it has to wrap onto more than one line inside the dialog box.";
const SURFACES = {
  "leave-page": { page: "<p>leave</p><script>onbeforeunload=e=>{e.preventDefault();e.returnValue='x'}</script>",
                  open: tab => wait(() => gB.removeTab(tab), 0) },
  alert: { page: `<script>setTimeout(()=>alert(${JSON.stringify(LONG)}),300)</script>` },
  confirm: { page: `<script>setTimeout(()=>confirm(${JSON.stringify(LONG)}),300)</script>` },
  prompt: { page: `<script>setTimeout(()=>prompt(${JSON.stringify(LONG)},"answer"),300)</script>` },
};
const BOX = ["height", "padding-top", "padding-bottom", "margin-top", "margin-bottom"];
// the KEY of an element is stable across the two moments (classes change: .sizeDetermined is added after sizing)
const keyOf = el => el.localName + (el.id ? "#" + el.id : ":" + [...(el.parentElement?.children || [])].indexOf(el));
const label = el => el.localName + (el.id ? "#" + el.id : "") +
  (typeof el.className == "string" && el.className ? "." + el.className.trim().split(/\s+/).join(".") : "");

// the box of every displayed element (depth <= 6), keyed by its path so two moments and two builds line up
function boxes(doc) {
  const out = new Map(), view = doc.defaultView;
  const walk = (el, path, depth) => {
    const cs = view.getComputedStyle(el);
    if (depth > 6 || cs.display == "none") { return; }
    const key = path + "/" + keyOf(el);
    out.set(key, { el, label: label(el), height: Math.round(el.getBoundingClientRect().height) + "px",
      "padding-top": cs.paddingTop, "padding-bottom": cs.paddingBottom,
      "margin-top": cs.marginTop, "margin-bottom": cs.marginBottom });
    [...el.children].forEach(c => walk(c, key, depth + 1));
  };
  walk(doc.documentElement, "", 0);
  return out;
}

function rulesFor(name, key, el, prop) {
  for (const r of IU.getMatchingCSSRules(el)) {
    if (!r.cssText || !r.style) { continue; }
    const short = prop.replace(/-(top|bottom)$/, "");
    const v = r.style.getPropertyValue(prop) || r.style.getPropertyValue(short) ||
              r.style.getPropertyValue(short + "-block");
    if (v) {
      say(`CLIP|rule|${name}|${key}|${prop}|${(r.parentStyleSheet && r.parentStyleSheet.href) || "inline"}:${IU.getRuleLine(r)}|${r.selectorText}|${v}`);
    }
  }
}

const atMeasure = new Map();       // frame document -> {boxes, anims}
const orig = SubDialog.prototype.resizeVertically;
SubDialog.prototype.resizeVertically = function () {
  try {
    const doc = this._frame.contentDocument;
    const anims = doc.getAnimations().map(a => ({
      el: a.effect && a.effect.target ? label(a.effect.target) : "?",
      kind: a.constructor.name, what: a.transitionProperty || a.animationName || "",
      ms: a.effect ? a.effect.getComputedTiming().duration : 0, target: a.effect && a.effect.target }));
    atMeasure.set(doc, { boxes: boxes(doc), anims });
  } catch (e) { say(`CLIP|error|hook|${String(e).slice(0, 160)}`); }
  return orig.apply(this, arguments);
};

function measure(name, dlg) {
  const doc = dlg._frame.contentDocument, view = doc.defaultView;
  const frameH = Math.round(dlg._frame.getBoundingClientRect().height);
  const frameW = Math.round(dlg._frame.getBoundingClientRect().width);
  const docH = (doc.body || doc.documentElement).scrollHeight;
  if (docH > frameH + 1) { say(`CLIP|frame|${name}|${docH}|${frameH}`); }
  let n = 0;
  for (const el of doc.querySelectorAll("*")) {
    const own = [...el.childNodes].filter(c => c.nodeType == 3).map(c => c.data).join("").trim();
    if (!own || !el.getClientRects().length) { continue; }
    const cs = view.getComputedStyle(el);
    if (cs.display == "none" || cs.visibility != "visible") { continue; }
    n++;
    const r = el.getBoundingClientRect();
    const need = el.scrollHeight, got = Math.round(Math.min(el.clientHeight || r.height, view.innerHeight - r.top));
    if (need > got + 1) { say(`CLIP|text|${name}|${label(el)}|${need}|${got}|${own.slice(0, 60)}`); }
  }
  // why: what changed between the moment the height was measured and now, and what was animating then
  const then = atMeasure.get(doc), now = boxes(doc);
  if (!then) {
    say(`CLIP|error|${name}|resizeVertically was not called for this dialog (sized another way)`);
  } else {
    for (const [key, b] of then.boxes) {
      say(`CLIP|box|${name}|measure|${key.replace(/\|/g, "")}|${b.height}|${b["padding-top"]}/${b["padding-bottom"]}|${b["margin-top"]}/${b["margin-bottom"]}`);
      const a = now.get(key);
      if (!a) { continue; }
      for (const p of BOX.slice(1)) {          // height follows the frame, so it is not a cause on its own
        if (a[p] != b[p]) {
          say(`CLIP|moved|${name}|${a.label}|${p}|${b[p]}|${a[p]}`);
          rulesFor(name, a.label, a.el, p);
        }
      }
    }
    for (const x of then.anims) {
      say(`CLIP|anim|${name}|${x.el}|${x.kind}|${x.what}|${x.ms}`);
    }
    // the rule that makes things animate at all, once per element
    for (const t of new Set(then.anims.map(x => x.target).filter(Boolean))) {
      for (const r of IU.getMatchingCSSRules(t)) {
        if (r.cssText && r.style && /transition|animation/.test(r.cssText)) {
          const v = ["transition-duration", "transition-property", "transition", "animation-duration"]
            .map(p => r.style.getPropertyValue(p)).filter(Boolean).join(" ");
          if (!v) { continue; }                       // a rule that only mentions the word, setting nothing here
          say(`CLIP|rule|${name}|${label(t)}|transition|${(r.parentStyleSheet && r.parentStyleSheet.href) || "inline"}:${IU.getRuleLine(r)}|${r.selectorText}|${v}`);
        }
      }
    }
  }
  say(`CLIP|surface|${name}|${frameW}x${frameH}|${n}`);
}

try {
  for (const [name, s] of Object.entries(SURFACES)) {
    try {
      const tab = gB.addTab("data:text/html," + encodeURIComponent(s.page),
        { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
      gB.selectedTab = tab;
      await sleep(2500);
      if (s.open) { s.open(tab); }
      let dlg = null;
      for (let i = 0; i < 40 && !dlg; i++) {             // up to 10 s for the dialog to open and size itself
        await sleep(250);
        dlg = gB.getTabDialogBox(tab.linkedBrowser).getContentDialogManager()._dialogs
          .find(d => d._frame?.contentDocument?.readyState == "complete") || null;
      }
      if (!dlg) { say(`CLIP|error|${name}|no dialog opened`); continue; }
      await sleep(1000);
      measure(name, dlg);
      dlg._frame.contentWindow.close();                  // cancel: a leave-page dialog then keeps the tab
      await sleep(500);
      if (tab.isConnected) { gB.removeTab(tab, { skipPermitUnload: true }); }
    } catch (e) {
      say(`CLIP|error|${name}|${String(e).slice(0, 160)}`);
    }
  }
} finally {
  SubDialog.prototype.resizeVertically = orig;
}
