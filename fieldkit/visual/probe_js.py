"""The JavaScript the runtime layer writes into a THROWAWAY copy of the build (never the install).

AUTOCONFIG_JS  defaults/pref/autoconfig.js: points the browser at gvisual.cfg and turns the autoconfig sandbox off
               so the script runs with chrome privileges.
CFG_JS         gvisual.cfg (the first line must be a comment): on the first browser window it maps
               resource://gvisual/ to the probe folder, registers the GVisual window actor, reads the page list
               from about:about itself, opens every page in a tab, asks the actor to measure it, opens the app menu
               and the toolbar context menu and measures those, writes JSON after every page, then quits.
MEASURE_MJS    GVisualMeasure.sys.mjs: the measurements, shared by the actor (pages) and the cfg (menus).
CHILD_MJS      GVisualChild.sys.mjs: the window actor; waits for the page to settle, then measures it.

Placeholders %OUT%, %DIR%, %DPR%, %PAGE_MS%, %ONLY% are filled by runtime.write_probe().
"""

AUTOCONFIG_JS = """// written by fieldkit visual into a THROWAWAY copy of the build; never into an installed browser
pref("general.config.filename", "gvisual.cfg");
pref("general.config.obscure_value", 0);
pref("general.config.sandbox_enabled", false);
"""

CFG_JS = r"""// gvisual.cfg - fieldkit visual runtime probe; written only into a throwaway copy of the build
(function () {
  const Cc = Components.classes, Ci = Components.interfaces;
  const OUT = "%OUT%";
  const DIR = "%DIR%";
  const DPR = %DPR%;
  const PAGE_MS = %PAGE_MS%;
  const ONLY = %ONLY%;
  const Svc = (typeof Services !== "undefined") ? Services
    : ChromeUtils.importESModule("resource://gre/modules/Services.sys.mjs").Services;

  function write(path, text) {
    const f = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
    f.initWithPath(path);
    const tmp = f.clone();
    tmp.leafName = f.leafName + ".part";
    const os = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
    os.init(tmp, 0x02 | 0x08 | 0x20, 0o644, 0);
    const conv = Cc["@mozilla.org/intl/converter-output-stream;1"].createInstance(Ci.nsIConverterOutputStream);
    conv.init(os, "UTF-8");
    conv.writeString(text);
    conv.close();
    tmp.moveTo(null, f.leafName);
  }
  function stamp(name, text) { try { write(DIR + "\\" + name, text); } catch (e) {} }
  stamp("started.txt", "autoconfig ran " + new Date().toISOString());

  const res = { schema: 1, dpr_wanted: DPR, started: Date.now(), complete: false, list_source: null,
                listed: [], pages: [], chrome: [], errors: [] };
  function flush() { res.updated = Date.now(); write(OUT, JSON.stringify(res)); }
  function sleep(win, ms) { return new Promise(r => win.setTimeout(r, ms)); }
  function timeout(win, p, ms, what) {
    return Promise.race([p, new Promise((_, rej) => win.setTimeout(() => rej(new Error(what + ": no answer in " + ms + " ms")), ms))]);
  }

  async function measurePage(win, url, wantList) {
    const t0 = Date.now();
    const out = { url, loaded: false, error: null, ms: 0 };
    let tab = null;
    try {
      tab = win.gBrowser.addTab(url, { triggeringPrincipal: Svc.scriptSecurityManager.getSystemPrincipal() });
      win.gBrowser.selectedTab = tab;
      const br = tab.linkedBrowser;
      const deadline = Date.now() + PAGE_MS;
      let wg = null;
      while (Date.now() < deadline) {
        await sleep(win, 150);
        wg = br.browsingContext && br.browsingContext.currentWindowGlobal;
        const doc = wg && wg.documentURI && wg.documentURI.spec;
        if (doc && (doc !== "about:blank" || url === "about:blank") && !(br.webProgress && br.webProgress.isLoadingDocument)) break;
        wg = null;
      }
      if (!wg) throw new Error("did not load in " + PAGE_MS + " ms");
      out.final_url = wg.documentURI.spec;
      out.remote_type = br.remoteType || "parent";
      const actor = wg.getActor("GVisual");
      const m = await timeout(win, actor.sendQuery("measure", { list: !!wantList }), PAGE_MS, "measure");
      Object.assign(out, m);
      out.loaded = /^about:(neterror|certerror|blocked|httpsonlyerror|tabcrashed)/.test(out.final_url) ? false : true;
      if (!out.loaded) out.error = "an error page loaded instead: " + out.final_url;
    } catch (e) {
      out.error = String(e && e.message || e);
    }
    out.ms = Date.now() - t0;
    try { if (tab) win.gBrowser.removeTab(tab, { animate: false }); } catch (e) {}
    return out;
  }

  async function popup(win, name, open, el, close) {
    const out = { surface: name, opened: false, error: null };
    try {
      const shown = new Promise(r => el.addEventListener("popupshown", r, { once: true }));
      await open();
      await timeout(win, shown, 8000, name + " popupshown");
      await sleep(win, 400);
      out.opened = true;
      const M = ChromeUtils.importESModule("resource://gvisual/GVisualMeasure.sys.mjs");
      out.metrics = await M.measure(win, el, { chrome: true });
      out.dpr = win.devicePixelRatio;
    } catch (e) {
      out.error = String(e && e.message || e);
    }
    try { await close(); } catch (e) {}
    await sleep(win, 300);
    return out;
  }

  async function run(win) {
    try {
      try { win.resizeTo(1280, 900); } catch (e) {}
      await sleep(win, 500);
      res.chrome_dpr = win.devicePixelRatio;
      res.window = [win.innerWidth, win.innerHeight];
      const rp = Svc.io.getProtocolHandler("resource").QueryInterface(Ci.nsIResProtocolHandler);
      const d = Cc["@mozilla.org/file/local;1"].createInstance(Ci.nsIFile);
      d.initWithPath(DIR);
      rp.setSubstitution("gvisual", Svc.io.newFileURI(d));
      ChromeUtils.registerWindowActor("GVisual", {
        child: { esModuleURI: "resource://gvisual/GVisualChild.sys.mjs" },
        allFrames: false,
        safeForUntrustedWebProcess: true,   // the control page loads in a web process; this copy only
      });
      let urls = [];
      const about = await measurePage(win, "about:about", true);
      if (about.links && about.links.length) {
        res.list_source = "about:about";
        urls = about.links;
      } else {
        res.errors.push("about:about gave no list: " + about.error + "; falling back to the about module registrations");
        res.list_source = "registrations";
        for (const k of Object.keys(Cc)) {
          const m = /^@mozilla\.org\/network\/protocol\/about;1\?what=(.+)$/.exec(k);
          if (!m) continue;
          try {
            const mod = Cc[k].getService(Ci.nsIAboutModule);
            const flags = mod.getURIFlags(Svc.io.newURI("about:" + m[1]));
            if (!(flags & Ci.nsIAboutModule.HIDE_FROM_ABOUTABOUT)) urls.push("about:" + m[1]);
          } catch (e) { urls.push("about:" + m[1]); }
        }
      }
      urls = [...new Set(urls)].sort();
      res.listed = urls.slice();
      // the self-control: a page with one planted defect of every kind; a probe that misses one is blind to it
      res.control = await measurePage(win, "resource://gvisual/control.html", false);
      flush();
      if (ONLY.length) urls = urls.filter(u => ONLY.includes(u));
      flush();
      for (const u of urls) {
        res.pages.push(u === "about:about" ? about : await measurePage(win, u, false));
        flush();
      }
      const doc = win.document;
      res.chrome.push(await popup(win, "app-menu", () => win.PanelUI.show(),
        doc.getElementById("appMenu-popup"), () => win.PanelUI.hide()));
      flush();
      const tcm = doc.getElementById("toolbar-context-menu");
      res.chrome.push(await popup(win, "toolbar-context-menu",
        () => tcm.openPopup(doc.getElementById("nav-bar"), "after_start", 40, 0, true, false, null),
        tcm, () => tcm.hidePopup()));
      res.complete = true;
    } catch (e) {
      res.errors.push("run: " + String(e && e.stack || e));
    }
    try { flush(); } catch (e) { stamp("flush-error.txt", String(e)); }
    Svc.startup.quit(Ci.nsIAppStartup.eForceQuit);
  }

  let fired = false;
  Svc.obs.addObserver(function obs(subject) {
    if (fired) return;
    fired = true;
    Svc.obs.removeObserver(obs, "browser-delayed-startup-finished");
    subject.setTimeout(() => { run(subject); }, 1500);
  }, "browser-delayed-startup-finished");
})();
"""

# The self-control page: one planted defect per measurement. %PNG% is an 8x8 PNG as a data: URI.
CONTROL_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>gvisual control</title>
<style>
body { margin: 0; font: 14px sans-serif; }
#up { width: 64px; height: 64px; }
#clip { width: 40px; overflow: hidden; white-space: nowrap; }
#pair button { width: 80px; }
#pair button + button { margin-left: -30px; }
#far { position: relative; left: 1500px; width: 100px; }
#zero { width: 0; height: 20px; background-image: url("%PNG%"); }
.mi { display: block; }
.mi label { display: inline-block; }
#m2 label { margin-left: 30px; }
#spillbox { width: 60px; }
#spill { width: 40px; white-space: nowrap; }
#widebox { overflow: hidden; width: 2000px; }
#cutoff { position: relative; left: 1500px; width: 100px; }
#parkbox { position: relative; overflow: hidden; width: 200px; margin-left: 300px; }
#parkbox input { position: absolute; left: -100px; top: 0; }
#inkpair a { display: inline-block; width: 100px; }
#ink2 { margin-left: -20px; text-align: right; }
#fitbox { width: 200px; }
#fits { display: inline-block; width: 16px; white-space: nowrap; }
#boxbox { width: 100px; }
#boxover { width: 150px; height: 10px; background: #ccc; }
#badgebox { position: relative; width: 100px; height: 20px; }
#badge { position: absolute; right: -10px; top: 0; width: 30px; height: 10px; }
#bleedbox { width: 100px; padding: 0 20px; }
#bleed { margin-inline: -20px; height: 10px; background: #ccc; }
</style></head><body>
<img id="up" src="%PNG%" alt="">
<img id="broken" src="resource://gvisual/does-not-exist.png" alt="" width="16" height="16">
<div id="clip">this text is much wider than forty pixels</div>
<div id="pair"><button>one</button><button>two</button></div>
<div id="far"><button>far away</button></div>
<div id="zero"></div>
<div role="menu" id="menu">
  <div role="menuitem" class="mi" id="m1"><label>first</label></div>
  <div role="menuitem" class="mi" id="m2"><label>second</label></div>
  <div role="menuitem" class="mi" id="m3"><label>third</label></div>
  <div role="menuitem" class="mi" id="m4"><label>fourth</label></div>
</div>
<div id="spillbox"><div id="spill">this text spills well past its parent</div></div>
<div id="widebox"><div id="cutoff"><button>cut off</button></div></div>
<div id="parkbox"><input type="radio" name="p" id="park1"><input type="radio" name="p" id="park2"><span>legend</span></div>
<div id="inkpair"><a href="#" id="ink1">ab</a><a href="#" id="ink2">cd</a></div>
<div id="fitbox"><span id="fits">Today</span></div>
<div id="boxbox"><div id="boxover"></div></div>
<div id="badgebox"><div id="badge"></div></div>
<div id="bleedbox"><div id="bleed"></div></div>
</body></html>
"""
# what the probe must find on the control page (metrics key -> the planted elements' ids)
CONTROL_EXPECT = {"images": ("up",), "broken": ("broken",), "clipped": ("clip", "spill"), "overlaps": ("pair",),
                  "outside": ("far", "cutoff"), "zero_size": ("zero",), "misaligned": ("m2",),
                  "overflowing": ("boxover",)}
# what the probe must NOT report (2026-10-04): radios parked inside an overflow:hidden box, two links whose boxes
# overlap where neither draws anything, and text drawn past its own narrow box but well inside its parent
# a badge placed past its box on purpose (absolute, negative offset) and a full-bleed strip (negative margins)
CONTROL_CLEAN = {"overlaps": ("park1", "ink1"), "clipped": ("fits",), "outside": ("park1",), "overflowing": ("badge", "bleed")}

CHILD_MJS = r"""// GVisualChild.sys.mjs - fieldkit visual runtime probe (throwaway copy only)
import { measure } from "resource://gvisual/GVisualMeasure.sys.mjs";

export class GVisualChild extends JSWindowActorChild {
  async receiveMessage(msg) {
    if (msg.name !== "measure") return null;
    const win = this.contentWindow, doc = this.document;
    const sleep = ms => new Promise(r => win.setTimeout(r, ms));
    const t0 = Date.now();
    while (doc.readyState !== "complete" && Date.now() - t0 < 10000) await sleep(100);
    try { await doc.fonts.ready; } catch (e) {}
    await sleep(600);
    await new Promise(r => win.requestAnimationFrame(() => win.requestAnimationFrame(r)));
    const imgs = [...doc.images].filter(i => !i.complete);
    const t1 = Date.now();
    while (imgs.some(i => !i.complete) && Date.now() - t1 < 3000) await sleep(100);
    const out = { title: doc.title, ready: doc.readyState, dpr: win.devicePixelRatio,
                  viewport: [win.innerWidth, win.innerHeight] };
    if (msg.data && msg.data.list) {
      out.links = [...doc.querySelectorAll("a[href^='about:']")].map(a => a.getAttribute("href"))
        .filter(h => /^about:[a-z0-9-]+$/i.test(h));
    }
    try {
      out.metrics = await measure(win, doc, { chrome: false });
    } catch (e) {
      out.metric_error = String(e && e.stack || e);
    }
    return out;
  }
}
"""

MEASURE_MJS = r"""// GVisualMeasure.sys.mjs - the measurements (fieldkit visual). Every list is capped; `truncated` says so.
const CAP = 200;
const CONTROLS = new Set(["button", "a", "input", "select", "textarea", "toolbarbutton", "menuitem", "menu",
  "moz-button", "moz-toggle", "moz-checkbox", "moz-radio", "toolbaritem", "checkbox", "radio", "menulist"]);
const ROLES = new Set(["button", "menuitem", "link", "tab", "checkbox", "radio", "menuitemcheckbox", "menuitemradio"]);
const VECTOR = /\.svg(\?|#|$)|^data:image\/svg/i;
// the Gorilla artwork and the icon ladder (RT-TINYLOGO, 2026-10-07): measured even when the file is an SVG, because
// about-logo.svg wraps a 1400 px raster
const BRANDING = /^chrome:\/\/branding\/content\/(about-logo|about\.|icon\d+\.|document)/i;

function sel(el) {
  const parts = [];
  let e = el;
  for (let i = 0; e && e.nodeType === 1 && i < 4; i++) {
    let s = e.localName;
    if (e.id) { s += "#" + e.id; parts.unshift(s); break; }
    const cls = (typeof e.className === "string" ? e.className : "").trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) s += "." + cls.join(".");
    parts.unshift(s);
    e = e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
  }
  return parts.join(" > ");
}

function* walk(root) {
  const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
  for (const el of all) {
    yield el;
    let sr = null;
    try { sr = el.openOrClosedShadowRoot; } catch (e) {}
    if (sr) yield* walk(sr);
  }
}

function visible(el) {
  try { return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }); } catch (e) { return true; }
}

function urls(value) {
  if (!value || value === "none") return [];
  const out = [];
  const set = /image-set\(([^)]*\))*[^)]*\)/.exec(value);
  const re = /url\(\s*["']?([^"')]+)["']?\s*\)\s*(\d+(?:\.\d+)?x)?/g;
  let m;
  while ((m = re.exec(value))) out.push({ url: m[1], res: m[2] ? parseFloat(m[2]) : 1, set: !!set });
  return out;
}

function pickSet(list, dpr) {
  if (!list.length || !list[0].set) return list[0];
  const sorted = list.slice().sort((a, b) => a.res - b.res);
  return sorted.find(c => c.res >= dpr) || sorted[sorted.length - 1];
}

async function natural(win, url, cache) {
  if (cache.has(url)) return cache.get(url);
  const p = new Promise(resolve => {
    const img = new win.Image();
    const done = ok => resolve({ w: ok ? img.naturalWidth : 0, h: ok ? img.naturalHeight : 0, ok });
    img.onload = () => done(true);
    img.onerror = () => done(false);
    win.setTimeout(() => done(img.complete && img.naturalWidth > 0), 4000);
    img.src = url;
  });
  cache.set(url, p);
  return p;
}

function painted(cs, rect, nat, res, layer) {
  // the CSS px size the background layer is drawn at
  const sizes = (cs.backgroundSize || "auto").split(",").map(s => s.trim());
  const s = sizes[Math.min(layer, sizes.length - 1)] || "auto";
  const iw = nat.w / res, ih = nat.h / res;
  if (s === "contain" || s === "cover") {
    const f = s === "contain" ? Math.min(rect.width / iw, rect.height / ih) : Math.max(rect.width / iw, rect.height / ih);
    return [iw * f, ih * f];
  }
  const [a, b = "auto"] = s.split(/\s+/);
  const len = (v, box) => v.endsWith("px") ? parseFloat(v) : v.endsWith("%") ? box * parseFloat(v) / 100 : null;
  let w = a === "auto" ? null : len(a, rect.width), h = b === "auto" ? null : len(b, rect.height);
  if (w == null && h == null) return [iw, ih];
  if (w == null) w = ih ? h * iw / ih : h;
  if (h == null) h = iw ? w * ih / iw : w;
  return [w, h];
}

// ---- geometry helpers (2026-10-04): what the user can actually see of a box, and where it really paints
const HTMLNS = "http://www.w3.org/1999/xhtml";
const FORM = new Set(["input", "select", "textarea", "button"]);
const REPLACED = new Set(["img", "svg", "video", "canvas", "iframe", "embed", "object", "image", "picture"]);
const INF = { l: -Infinity, t: -Infinity, r: Infinity, b: Infinity };

// the parent in the flattened tree: a slotted node's slot, else its parent, else (top of a shadow tree) the host
function flatParent(e) {
  return e.assignedSlot || e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
}

function makesFixedBlock(cs) {
  return cs.transform !== "none" || cs.perspective !== "none" || cs.filter !== "none" ||
    (cs.backdropFilter && cs.backdropFilter !== "none") || /paint|layout|strict|content/.test(cs.contain || "");
}

// the ancestors whose overflow can clip `el`: an absolutely positioned box escapes non-positioned ancestors,
// a fixed one escapes everything up to a transformed/contained ancestor; the root and body clip via the viewport
function* clipAncestors(win, el) {
  let pos = win.getComputedStyle(el).position;
  const top = win.document.documentElement, body = win.document.body;
  for (let a = flatParent(el); a && a.nodeType === 1; a = flatParent(a)) {
    if (a === top || a === body) break;
    const cs = win.getComputedStyle(a);
    if (cs.display === "contents") continue;
    const fixedBlock = makesFixedBlock(cs);
    if (pos === "fixed" && !fixedBlock) continue;
    if (pos === "absolute" && cs.position === "static" && !fixedBlock) continue;
    yield [a, cs];
    pos = cs.position;
  }
}

function padBox(a, cs) {
  const r = a.getBoundingClientRect();
  return { l: r.left + (parseFloat(cs.borderLeftWidth) || 0), r: r.right - (parseFloat(cs.borderRightWidth) || 0),
           t: r.top + (parseFloat(cs.borderTopWidth) || 0), b: r.bottom - (parseFloat(cs.borderBottomWidth) || 0) };
}

function contentBox(a, cs) {
  const p = padBox(a, cs);
  return { l: p.l + (parseFloat(cs.paddingLeft) || 0), r: p.r - (parseFloat(cs.paddingRight) || 0),
           t: p.t + (parseFloat(cs.paddingTop) || 0), b: p.b - (parseFloat(cs.paddingBottom) || 0) };
}

const isClip = v => v === "hidden" || v === "clip";

// the region `el` can paint in: the padding box of every ancestor that hides overflow (per axis), then the
// viewport horizontally when vw is given. Not vertically: the document scrolls, a control below the fold is seen.
function clipRegion(win, el, vw) {
  const c = Object.assign({}, INF);
  for (const [a, cs] of clipAncestors(win, el)) {
    const cx = isClip(cs.overflowX), cy = isClip(cs.overflowY);
    if (!cx && !cy) continue;
    const p = padBox(a, cs);
    if (cx) { c.l = Math.max(c.l, p.l); c.r = Math.min(c.r, p.r); }
    if (cy) { c.t = Math.max(c.t, p.t); c.b = Math.min(c.b, p.b); }
  }
  if (vw != null) { c.l = Math.max(c.l, 0); c.r = Math.min(c.r, vw); }
  return c;
}

function isect(a, b) {
  const l = Math.max(a.l != null ? a.l : a.left, b.l != null ? b.l : b.left);
  const r = Math.min(a.r != null ? a.r : a.right, b.r != null ? b.r : b.right);
  const t = Math.max(a.t != null ? a.t : a.top, b.t != null ? b.t : b.top);
  const bt = Math.min(a.b != null ? a.b : a.bottom, b.b != null ? b.b : b.bottom);
  return { l, r, t, b: bt, w: r - l, h: bt - t };
}

const solid = c => c && c !== "transparent" && !/^rgba\(.*,\s*0\)$/.test(c);

// does the box itself paint (a background or a border), or is it a native form control drawn whole?
function paintsBox(el, cs) {
  if (solid(cs.backgroundColor) || (cs.backgroundImage && cs.backgroundImage !== "none")) return true;
  for (const s of ["Top", "Right", "Bottom", "Left"]) {
    if ((parseFloat(cs["border" + s + "Width"]) || 0) > 0 && cs["border" + s + "Style"] !== "none" &&
        cs["border" + s + "Style"] !== "hidden" && solid(cs["border" + s + "Color"])) return true;
  }
  return el.namespaceURI === HTMLNS && FORM.has(el.localName) && cs.appearance !== "none";
}

function rectsOf(list) {
  return [...list].filter(r => r.width > 0 && r.height > 0).map(r => ({ l: r.left, r: r.right, t: r.top, b: r.bottom }));
}

// the rectangles of an element's OWN text nodes (not its descendants'), measured with a Range
function ownTextRects(doc, el) {
  const out = [];
  const range = doc.createRange();
  for (const n of el.childNodes) {
    if (n.nodeType !== 3 || !n.textContent.trim()) continue;
    range.selectNodeContents(n);
    out.push(...rectsOf(range.getClientRects()));
  }
  return out;
}

// painted ink: the own box when it paints, else its text and whatever descendants paint (replaced elements,
// boxes with a background or border, native controls), clipped to the region the element can paint in
function inkRects(win, el, cs, region) {
  const doc = el.ownerDocument;
  let rects;
  if (paintsBox(el, cs)) rects = rectsOf(el.getClientRects());
  else {
    rects = ownTextRects(doc, el);
    const subs = [];
    let sr = null;
    try { sr = el.openOrClosedShadowRoot; } catch (e) {}
    let n = 0;
    for (const d of [...walk(el), ...(sr ? walk(sr) : [])]) {
      if (++n > 400) { rects = rectsOf(el.getClientRects()); subs.length = 0; break; }   // too big to tell: whole box
      if (!visible(d)) continue;
      rects.push(...ownTextRects(doc, d));
      const dcs = win.getComputedStyle(d);
      if (REPLACED.has(d.localName) || paintsBox(d, dcs)) subs.push(...rectsOf(d.getClientRects()));
    }
    rects.push(...subs);
  }
  return rects.map(r => isect(r, region)).filter(r => r.w > 0 && r.h > 0);
}

function boxRects(el, region) {
  return rectsOf(el.getClientRects()).map(r => isect(r, region)).filter(r => r.w > 0 && r.h > 0);
}

export async function measure(win, root, opts) {
  const dpr = win.devicePixelRatio;
  const vw = win.document.documentElement.clientWidth || win.innerWidth;
  const out = { dpr, elements: 0, images: [], images_ok: 0, vector_icons: 0, broken: [], zero_size: [], clipped: [],
                overlaps: [], outside: [], overflowing: [], misaligned: [], page_scrolls_sideways: false, truncated: [], branding: [],
                pictures_seen: [], components_seen: [] };
  const push = (k, v) => { if (out[k].length < CAP) out[k].push(v); else if (!out.truncated.includes(k)) out.truncated.push(k); };
  // what the page actually shows (RT-CONTENT): every picture painted in a visible box, every custom element shown
  const seenPics = new Set(), seenTags = new Set();
  const cache = new Map();
  const groups = new Map();
  const menus = [];
  for (const el of walk(root)) {
    out.elements++;
    if (out.elements > 30000) { out.truncated.push("elements"); break; }
    if (!visible(el)) continue;
    const cs = win.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const tag = el.localName;
    if (tag.includes("-") && r.width > 0 && r.height > 0 && seenTags.size < 400 && !seenTags.has(tag)) { seenTags.add(tag); out.components_seen.push(tag); }
    // ---- pictures
    const pics = [];
    if (tag === "img" && (el.currentSrc || el.src)) pics.push({ kind: "img", url: el.currentSrc || el.src, res: 1, el: true });
    for (const [kind, prop] of [["background", "backgroundImage"], ["list-style", "listStyleImage"], ["content", "content"]]) {
      const v = cs[prop];
      if (!v || v === "none" || v === "normal" || !v.includes("url(")) continue;
      if (kind === "background") {
        const layers = v.split(/,(?![^(]*\))/);
        layers.forEach((layer, i) => { const c = pickSet(urls(layer), dpr); if (c) pics.push(Object.assign({ kind, layer: i }, c)); });
      } else {
        const c = pickSet(urls(v), dpr);
        if (c) pics.push(Object.assign({ kind }, c));
      }
    }
    for (const p of pics) {
      if (r.width > 0 && r.height > 0 && seenPics.size < 400 && !seenPics.has(p.url)) { seenPics.add(p.url); out.pictures_seen.push(p.url.slice(0, 200)); }
      // the root's background paints the canvas, whatever the root's own box is
      if ((r.width === 0 || r.height === 0) && tag !== "html" && tag !== "body") {
        push("zero_size", { sel: sel(el), kind: p.kind, url: p.url.slice(0, 160), rect: [r.width, r.height] });
        continue;
      }
      if (r.width === 0 || r.height === 0) continue;
      const art = BRANDING.test(p.url);
      if (!art && (VECTOR.test(p.url) || cs.MozImageRegion && cs.MozImageRegion !== "auto")) { out.vector_icons++; continue; }
      let nat;
      if (p.kind === "img") nat = { w: el.naturalWidth, h: el.naturalHeight, ok: el.complete && el.naturalWidth > 0 };
      else nat = await natural(win, p.url, cache);
      if (!nat.ok || !nat.w) { push("broken", { sel: sel(el), kind: p.kind, url: p.url.slice(0, 160) }); continue; }
      // a URL without an extension (favicons, thumbnails, moz-icon:) is judged as the raster it is
      let w, h;
      if (p.kind === "background") [w, h] = painted(cs, r, nat, p.res || 1, p.layer || 0);
      else {
        const bl = parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft) + parseFloat(cs.borderRightWidth) + parseFloat(cs.paddingRight);
        const bt = parseFloat(cs.borderTopWidth) + parseFloat(cs.paddingTop) + parseFloat(cs.borderBottomWidth) + parseFloat(cs.paddingBottom);
        w = Math.max(0, r.width - bl); h = Math.max(0, r.height - bt);
        if (cs.objectFit === "contain" || cs.objectFit === "scale-down") {
          const f = Math.min(w / nat.w, h / nat.h); w = nat.w * f; h = nat.h * f;
        }
      }
      const up = Math.max((w * dpr) / nat.w, (h * dpr) / nat.h);
      const rec = { sel: sel(el), kind: p.kind, url: p.url.slice(0, 160), natural: [nat.w, nat.h],
                    painted: [Math.round(w * 10) / 10, Math.round(h * 10) / 10], dpr, upscale: Math.round(up * 100) / 100 };
      if (art) push("branding", rec);
      if (up > 1.05) push("images", rec); else out.images_ok++;
    }
    // ---- text clipped by its own box
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const host = el.getRootNode && el.getRootNode().host;
    const inField = host && ["input", "textarea", "select"].includes(host.localName);   // a text field scrolls by design
    if (hasText && !inField && !["input", "textarea", "select", "option", "script", "style", "title"].includes(tag) && el.clientWidth > 0) {
      const ox = cs.overflowX;
      const scrollable = ox === "auto" || ox === "scroll";
      if (scrollable) {
        // a scroller shows its text by scrolling
      } else if (isClip(ox) || cs.textOverflow === "ellipsis") {
        if (el.scrollWidth > el.clientWidth + 1)
          push("clipped", { sel: sel(el), how: "clipped", text: el.textContent.trim().slice(0, 60), scroll: el.scrollWidth, client: el.clientWidth });
      } else {
        // overflow visible: the text is drawn whole. It is a defect only where it is cut (it leaves the parent's
        // content box or the region its ancestors let it paint in) or runs into a sibling (2026-10-04)
        const tr = ownTextRects(el.ownerDocument, el);
        const pb = padBox(el, cs);
        const out1 = tr.filter(t => t.r > pb.r + 1 || t.l < pb.l - 1);
        if (out1.length) {
          const why = [];
          let par = flatParent(el);
          while (par && par.nodeType === 1 && win.getComputedStyle(par).display === "contents") par = flatParent(par);
          if (par && par.nodeType === 1 && par !== win.document.documentElement) {
            const cb = contentBox(par, win.getComputedStyle(par));
            if (out1.some(t => t.r > cb.r + 1 || t.l < cb.l - 1)) why.push("leaves its parent's content box");
          }
          const reg = clipRegion(win, el, opts.chrome ? null : vw);
          if (out1.some(t => t.r > reg.r + 1 || t.l < reg.l - 1)) why.push("is cut off by an ancestor or the window edge");
          if (par) {
            for (const s of par.children) {
              if (s === el || !visible(s)) continue;
              const sb = s.getBoundingClientRect();
              if (sb.width <= 0 || sb.height <= 0) continue;
              const own = isect(r, sb);
              if (own.w > 1 && own.h > 1) continue;      // a sibling laid over this box on purpose, not hit by spill
              if (out1.some(t => { const o = isect(t, sb); return o.w > 1 && o.h > 1; })) { why.push("runs into " + sel(s)); break; }
            }
          }
          if (why.length) {
            const tl = Math.min(...tr.map(t => t.l)), trr = Math.max(...tr.map(t => t.r));
            push("clipped", { sel: sel(el), how: "spills", text: el.textContent.trim().slice(0, 60),
                              scroll: Math.round(trr - tl), client: el.clientWidth, why: why.join("; ") });
          }
        }
      }
    }
    // ---- horizontally outside the viewport (pages only; popups live in their own windows)
    if (!opts.chrome && r.width > 1 && r.height > 1 && (r.right > vw + 1 || r.left < -1) &&
        (hasText || pics.length || CONTROLS.has(tag))) {
      // only a scroller (auto/scroll) makes off-screen content reachable; an ancestor that hides overflow does
      // not excuse it: judge what is left after its clip against the window (2026-10-04)
      let inScroller = false;
      for (const [, acs] of clipAncestors(win, el)) {
        if (acs.overflowX === "auto" || acs.overflowX === "scroll") { inScroller = true; break; }
      }
      const reg = clipRegion(win, el, null);
      const vis = isect(r, reg);
      const goneByClip = vis.w <= 1 || vis.h <= 1;     // wholly hidden by an ancestor: parked on purpose
      const hidden = cs.position === "absolute" && (r.right < 0 || r.left > vw) && (cs.clipPath !== "none" || r.width <= 1);
      if (!inScroller && !hidden && !goneByClip && (vis.r > vw + 1 || vis.l < -1))
        push("outside", { sel: sel(el), rect: [Math.round(vis.l), Math.round(vis.r)], viewport: vw });
    }
    // ---- a box wider than its parent lets it be (2026-10-04): an in-flow HTML box whose border box leaves its
    // overflow:visible parent's content box sideways. Not judged: boxes placed on purpose (absolute/fixed,
    // relative with an offset, transformed), inline and table-internal boxes, a side a negative margin explains.
    if (!opts.chrome && el.namespaceURI === HTMLNS && r.width > 0 && r.height > 0 && cs.transform === "none" &&
        !["absolute", "fixed"].includes(cs.position) && cs.display !== "inline" && !cs.display.startsWith("table-") &&
        !(cs.position === "relative" && ((parseFloat(cs.left) || 0) !== 0 || (parseFloat(cs.right) || 0) !== 0))) {
      let par = flatParent(el);
      while (par && par.nodeType === 1 && win.getComputedStyle(par).display === "contents") par = flatParent(par);
      if (par && par.nodeType === 1 && par.namespaceURI === HTMLNS && par !== win.document.documentElement) {
        const pcs = win.getComputedStyle(par);
        if (pcs.overflowX === "visible" && pcs.display !== "inline" && !pcs.display.startsWith("table-") &&
            par.getBoundingClientRect().width > 0) {
          const cb = contentBox(par, pcs);
          const ml = parseFloat(cs.marginLeft) || 0, mr = parseFloat(cs.marginRight) || 0;
          const overL = cb.l - r.left, overR = r.right - cb.r;
          const badL = overL > 1 && !(ml < 0 && overL <= -ml + 1);
          const badR = overR > 1 && !(mr < 0 && overR <= -mr + 1);
          if (badL || badR)
            push("overflowing", { sel: sel(el), parent: sel(par), rect: [Math.round(r.left), Math.round(r.right)],
                                  content: [Math.round(cb.l), Math.round(cb.r)], by: Math.round(Math.max(overL, overR)) });
        }
      }
    }
    // ---- sibling controls for the overlap check
    const role = el.getAttribute && el.getAttribute("role");
    // controls parked out of sight (off-screen, or inside an ancestor that hides overflow: the visually-hidden
    // radio pattern) are not laid out against each other: only what is left after the clip joins a group
    if ((CONTROLS.has(tag) || ROLES.has(role)) && r.width > 0 && r.height > 0) {
      const region = clipRegion(win, el, opts.chrome ? null : vw);
      const vis = isect(r, region);
      const parent = el.parentElement || (el.getRootNode && el.getRootNode().host);
      if (parent && vis.w > 1 && vis.h > 1) { if (!groups.has(parent)) groups.set(parent, []); groups.get(parent).push([el, cs, region]); }
    }
    if (tag === "menupopup" || tag === "panelview" || role === "menu" || (opts.chrome && tag === "panel")) menus.push(el);
  }
  if (opts.chrome && root && (root.localName === "menupopup" || root.localName === "panel")) menus.push(root);
  const inkCache = new Map();
  for (const [, kids] of groups) {
    for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
      const [a, csa, rga] = kids[i], [b, csb, rgb] = kids[j];
      if (a.contains(b) || b.contains(a)) continue;
      // per line box (two links in one wrapped paragraph have overlapping bounding boxes but never touch), on the
      // clipped boxes, and only where one control's painted ink meets the other's box (2026-10-04): two transparent
      // boxes overlapping where neither draws anything are not a visible defect
      const boxA = boxRects(a, rga), boxB = boxRects(b, rgb);
      if (!boxA.some(ra => boxB.some(rb => { const o = isect(ra, rb); return o.w > 1 && o.h > 1; }))) continue;
      if (!inkCache.has(a)) inkCache.set(a, inkRects(win, a, csa, rga));
      if (!inkCache.has(b)) inkCache.set(b, inkRects(win, b, csb, rgb));
      const inkA = inkCache.get(a), inkB = inkCache.get(b);
      let best = null;
      for (const [P, Q] of [[inkA, boxB], [boxA, inkB]]) for (const ra of P) for (const rb of Q) {
        const o = isect(ra, rb);
        if (o.w > 1 && o.h > 1 && (!best || o.w * o.h > best[0] * best[1])) best = [Math.round(o.w), Math.round(o.h)];
      }
      if (best) push("overlaps", { a: sel(a), b: sel(b), overlap: best });
    }
  }
  // ---- menu rows: labels (and icons) of a menu's items share one x offset
  const seen = new Set();
  for (const m of new Set(menus)) {
    // one item per row: composite rows (the zoom controls: several buttons in a toolbaritem) are not menu rows
    const cand = [...m.querySelectorAll("menuitem, menu, toolbarbutton.subviewbutton, [role=menuitem], [role=menuitemcheckbox], [role=menuitemradio]")]
      .filter(i => visible(i) && i.getBoundingClientRect().height > 0 && !i.closest("toolbaritem") &&
                   (i.closest("menupopup, panelview, [role=menu]") === m || m.localName === "panel"));
    const rows = new Map();
    for (const i of cand) {
      const top = Math.round(i.getBoundingClientRect().top);
      rows.set(top, (rows.get(top) || []).concat([i]));
    }
    const items = [...rows.values()].filter(r => r.length === 1).map(r => r[0]);
    for (const [what, q] of [["label", ".menu-text, .menu-iconic-text, .toolbarbutton-text, label"], ["icon", ".menu-iconic-icon, .toolbarbutton-icon, .menu-icon"]]) {
      const xs = [];
      for (const it of items) {
        let t = it.querySelector(q);
        if (!t && it.shadowRoot) t = it.shadowRoot.querySelector(q);
        if (!t || !visible(t)) continue;
        const tr = t.getBoundingClientRect();
        if (tr.width === 0) continue;
        xs.push([it, Math.round(tr.left)]);
      }
      if (xs.length < 3) continue;
      const count = new Map();
      for (const [, x] of xs) count.set(x, (count.get(x) || 0) + 1);
      const mode = [...count.entries()].sort((a, b) => b[1] - a[1])[0][0];
      for (const [it, x] of xs) if (Math.abs(x - mode) > 2 && !seen.has(what + sel(it)) && seen.add(what + sel(it))) push("misaligned", { menu: sel(m), item: sel(it), what, x, mode, text: (it.getAttribute("label") || it.textContent || "").trim().slice(0, 40) });
    }
  }
  const de = win.document.documentElement;
  if (!opts.chrome && de && de.scrollWidth > de.clientWidth + 1) out.page_scrolls_sideways = [de.scrollWidth, de.clientWidth];
  return out;
}
"""
