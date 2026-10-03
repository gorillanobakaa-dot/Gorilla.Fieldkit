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
</body></html>
"""
# what the probe must find on the control page (metrics key -> the planted element's id)
CONTROL_EXPECT = {"images": "up", "broken": "broken", "clipped": "clip", "overlaps": "pair", "outside": "far",
                  "zero_size": "zero", "misaligned": "m2"}

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

export async function measure(win, root, opts) {
  const dpr = win.devicePixelRatio;
  const vw = win.document.documentElement.clientWidth || win.innerWidth;
  const out = { dpr, elements: 0, images: [], images_ok: 0, vector_icons: 0, broken: [], zero_size: [], clipped: [],
                overlaps: [], outside: [], misaligned: [], page_scrolls_sideways: false, truncated: [] };
  const push = (k, v) => { if (out[k].length < CAP) out[k].push(v); else if (!out.truncated.includes(k)) out.truncated.push(k); };
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
      // the root's background paints the canvas, whatever the root's own box is
      if ((r.width === 0 || r.height === 0) && tag !== "html" && tag !== "body") {
        push("zero_size", { sel: sel(el), kind: p.kind, url: p.url.slice(0, 160), rect: [r.width, r.height] });
        continue;
      }
      if (r.width === 0 || r.height === 0) continue;
      if (VECTOR.test(p.url) || cs.MozImageRegion && cs.MozImageRegion !== "auto") { out.vector_icons++; continue; }
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
      if (up > 1.05) push("images", rec); else out.images_ok++;
    }
    // ---- text clipped by its own box
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    const host = el.getRootNode && el.getRootNode().host;
    const inField = host && ["input", "textarea", "select"].includes(host.localName);   // a text field scrolls by design
    if (hasText && !inField && !["input", "textarea", "select", "option", "script", "style", "title"].includes(tag) && el.clientWidth > 0) {
      const ox = cs.overflowX;
      const scrollable = ox === "auto" || ox === "scroll";
      if (!scrollable && el.scrollWidth > el.clientWidth + 1) {
        const how = (ox === "hidden" || ox === "clip" || cs.textOverflow === "ellipsis") ? "clipped" : "spills";
        push("clipped", { sel: sel(el), how, text: el.textContent.trim().slice(0, 60), scroll: el.scrollWidth, client: el.clientWidth });
      }
    }
    // ---- horizontally outside the viewport (pages only; popups live in their own windows)
    if (!opts.chrome && r.width > 1 && r.height > 1 && (r.right > vw + 1 || r.left < -1) &&
        (hasText || pics.length || CONTROLS.has(tag))) {
      let a = el.parentElement, inScroller = false;
      while (a) {
        const ax = win.getComputedStyle(a).overflowX;
        if (ax !== "visible") { inScroller = true; break; }
        a = a.parentElement;
      }
      const hidden = cs.position === "absolute" && (r.right < 0 || r.left > vw) && (cs.clipPath !== "none" || r.width <= 1);
      if (!inScroller && !hidden) push("outside", { sel: sel(el), rect: [Math.round(r.left), Math.round(r.right)], viewport: vw });
    }
    // ---- sibling controls for the overlap check
    const role = el.getAttribute && el.getAttribute("role");
    // controls parked off-screen (the visually-hidden radio pattern) are not laid out against each other
    if ((CONTROLS.has(tag) || ROLES.has(role)) && r.width > 0 && r.height > 0 && r.right > 0 && (opts.chrome || r.left < vw)) {
      const parent = el.parentElement || (el.getRootNode && el.getRootNode().host);
      if (parent) { if (!groups.has(parent)) groups.set(parent, []); groups.get(parent).push([el, r]); }
    }
    if (tag === "menupopup" || tag === "panelview" || role === "menu" || (opts.chrome && tag === "panel")) menus.push(el);
  }
  if (opts.chrome && root && (root.localName === "menupopup" || root.localName === "panel")) menus.push(root);
  for (const [, kids] of groups) {
    for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
      const [a] = kids[i], [b] = kids[j];
      if (a.contains(b) || b.contains(a)) continue;
      // per line box: two links in one wrapped paragraph have overlapping bounding boxes but never touch
      let best = null;
      for (const ra of a.getClientRects()) for (const rb of b.getClientRects()) {
        const ow = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const oh = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (ow > 1 && oh > 1 && (!best || ow * oh > best[0] * best[1])) best = [Math.round(ow), Math.round(oh)];
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
