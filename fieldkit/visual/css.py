"""Static CSS checks: a rule that paints a raster must not upscale it.

    ASSET-002  the raster's pixels >= 2 x the CSS box it is painted into (doctrine; below 2x the browser
               resamples upward on a HiDPI screen, below 1x on every screen).
    CSS-004    `content: url()/var()` injected into an element with no size clamp (width/max-width or
               height/max-height in the same rule): the 200 px explosion, ERR-UI-004, twice (doctrine section 4).

Where the box comes from, in order:
    1. the rule itself: background-size (or the `/ size` of the background shorthand), else width/max-width and
       height/max-height in px. min()/clamp() with a px term give an UPPER bound on the box (min(80%, 500px) is at
       most 500px): enough to prove a PASS, never a FAIL (below 2x against an upper bound is UNVERIFIABLE).
       calc() of px terms only is evaluated exactly. Both dimensions are judged when both are stated.
    2. an icon rule in the same file whose selector is this rule's compound selector followed by a descendant/child
       icon part (.button-icon, .toolbarbutton-icon, .menu-iconic-icon, image) with px width/height
       (`#whimsy-button > .button-box > .button-icon { width: 16px }`). Every selector of the rule needs one; the
       largest box found is used.
    3. image-set() with no stated box: the 1x candidate's intrinsic size IS the CSS box; every candidate of
       resolution r must be at least r x that box in both dimensions, and a candidate of 2x or more must exist.
    4. background/background-image of one raster file with no background-size: drawn at its intrinsic size (1.00x,
       a FAIL at 200%) - unless another rule in the same file that may match the same element sets a background
       size, in which case each possible size is computed (contain/cover against that rule's px width/height) and
       the verdict is PASS/FAIL only when every possibility agrees, else UNVERIFIABLE with the numbers.
A rule that paints a raster into a box none of these gives is UNVERIFIABLE here (the runtime layer measures the
real box); a URL the jar.mn index cannot resolve is UNVERIFIABLE; nothing is assumed to be fine.
"""
import base64
import io
import re
from pathlib import Path

from . import chrome, rasters

PAINT_PROPS = ("background", "background-image", "content", "list-style-image", "list-style", "border-image",
               "border-image-source")
COMMENT = re.compile(r"/\*.*?\*/", re.S)
BLOCK = re.compile(r"([^{};]+)\{([^{}]*)\}")
DECL = re.compile(r"([\w-]+)\s*:\s*([^;]+)")
URL = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)")
VAR = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,[^)]*)?\)")
PX = re.compile(r"(-?\d+(?:\.\d+)?)px")
RESOLUTION = re.compile(r"(\d+(?:\.\d+)?)(x|dppx)", re.I)
ICON_PARTS = (".button-icon", ".toolbarbutton-icon", ".menu-iconic-icon", "image")
BG_PROPS = ("background", "background-image")


def rules(text):
    """[(selector, {prop: value})] for every innermost block (@media bodies included), comments removed."""
    text = COMMENT.sub("", text)
    out = []
    for m in BLOCK.finditer(text):
        sel = " ".join(m.group(1).split())
        decls = {}
        for d in DECL.finditer(m.group(2)):
            decls[d.group(1).strip().lower()] = d.group(2).replace("!important", "").strip()
        out.append((sel, decls))
    return out


def custom_props(rule_list):
    """--name: value over the whole file (last wins), for var() resolution."""
    props = {}
    for _, decls in rule_list:
        for k, v in decls.items():
            if k.startswith("--"):
                props[k] = v
    return props


def _expand(value, props, depth=0):
    if depth > 5:
        return value
    return VAR.sub(lambda m: _expand(props.get(m.group(1), ""), props, depth + 1), value)


def split_top(s, seps=","):
    """Split on any character of `seps` outside (), [] and quotes; empty pieces dropped."""
    out, cur, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in seps:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


def length(tok):
    """A CSS length -> (px, exact). exact False = an upper bound only. (None, False) when no bound is known."""
    tok = tok.strip()
    if tok == "0":
        return 0.0, True
    m = PX.fullmatch(tok)
    if m:
        return float(m.group(1)), True
    f = re.fullmatch(r"(min|max|clamp|calc)\((.*)\)", tok, re.S | re.I)
    if not f:
        return None, False
    name, inner = f.group(1).lower(), f.group(2)
    args = [length(a) for a in split_top(inner)]
    if name == "min":                       # min() <= each argument: any bounded argument bounds it
        known = [a for a in args if a[0] is not None]
        if not known:
            return None, False
        return min(a[0] for a in known), len(known) == len(args) and all(a[1] for a in args)
    if name == "max":                       # max() is bounded only when every argument is
        if not args or any(a[0] is None for a in args):
            return None, False
        return max(a[0] for a in args), all(a[1] for a in args)
    if name == "clamp":                     # clamp(MIN, VAL, MAX) = max(MIN, min(VAL, MAX)) <= max(MIN, MAX)
        if len(args) != 3 or args[0][0] is None or args[2][0] is None:
            return None, False
        return max(args[0][0], args[2][0]), False
    expr = PX.sub(lambda m: m.group(1), inner)          # calc(): px-only arithmetic is evaluated exactly
    if re.search(r"[^\d.\s+\-*/()]", expr) or "**" in expr or "//" in expr:
        return None, False
    try:
        v = float(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - digits, + - * / ( ) and spaces only
    except Exception:  # noqa: BLE001
        return None, False
    return (v, True) if v > 0 else (None, False)


def _size_value(text):
    """'500px 500px' / 'min(80%, 500px)' / 'auto 16px' -> (w, h, exact) with None for auto; None when no box
    (contain, cover, %, auto auto)."""
    toks = split_top(text, " \t\n")[:2]
    if not toks or toks[0].lower() in ("contain", "cover"):
        return None
    dims = []
    for t in toks:
        if t.lower() == "auto":
            dims.append((None, True))
            continue
        v, exact = length(t)
        if v is None:
            return None
        dims.append((v, exact))
    if len(dims) == 1:
        dims.append((None, True))
    if dims[0][0] is None and dims[1][0] is None:
        return None
    return dims[0][0], dims[1][0], dims[0][1] and dims[1][1]


def _shorthand_size(value):
    """The `/ size` part of a background shorthand, as text, or None."""
    parts = split_top(value, "/")
    if len(parts) < 2:
        return None
    toks = []
    for t in split_top(parts[1], " \t\n"):
        if t.lower() == "auto" or t.lower() in ("contain", "cover") or length(t)[0] is not None or \
                re.fullmatch(r"-?\d+(?:\.\d+)?%", t):
            toks.append(t)
            if len(toks) == 2 or t.lower() in ("contain", "cover"):
                break
        else:
            break
    return " ".join(toks) or None


def box(decls):
    """-> (width px or None, height px or None, source, exact) from the rule itself, or (None, None, None, True).
    exact False: the numbers are upper bounds (min()/clamp())."""
    bs = decls.get("background-size", "")
    if bs:
        got = _size_value(bs)
        if got:
            return got[0], got[1], f"background-size: {bs}", got[2]
    size = _shorthand_size(decls.get("background", ""))
    if size:
        got = _size_value(size)
        if got:
            return got[0], got[1], f"background: ... / {size}", got[2]
    w = h = None
    src = []
    for k in ("width", "max-width"):
        m = PX.fullmatch((decls.get(k) or "").strip())
        if m:
            w = float(m.group(1))
            src.append(f"{k}: {decls[k]}")
            break
    for k in ("height", "max-height"):
        m = PX.fullmatch((decls.get(k) or "").strip())
        if m:
            h = float(m.group(1))
            src.append(f"{k}: {decls[k]}")
            break
    if w is None and h is None:
        return None, None, None, True
    return w, h, "; ".join(src), True


def clamped(decls):
    return any(PX.fullmatch((decls.get(k) or "").strip()) for k in ("width", "max-width", "height", "max-height"))


def ratio(iw, ih, bw, bh):
    """Pixels per CSS px of an iw x ih raster drawn into a bw x bh box (None = auto, aspect kept)."""
    r = []
    if bw:
        r.append(iw / bw)
    if bh:
        r.append(ih / bh)
    return min(r) if r else None


def _data_raster(url):
    m = re.match(r"data:image/(png|jpeg|gif|webp);base64,(.+)$", url.strip(), re.S)
    if not m:
        return None
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(re.sub(r"\s", "", m.group(2)))))
    im.load()
    return im


def raster_size(path):
    """-> (w, h, what, intrinsic) for a file a rule paints; w None for a pure SVG. intrinsic True when the file's
    pixel size is also its CSS intrinsic size (a plain raster file). Raises on unreadable."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".svg":
        embedded = rasters.svg_rasters(p)
        if not embedded:
            return None, None, "vector", False
        im = max((im for _, im in embedded), key=lambda i: i.size[0])
        return im.size[0], im.size[1], f"PNG embedded in {p.name}, {im.size[0]}x{im.size[1]}", False
    if ext == ".ico":
        frames = rasters.ico_frames(p)
        return frames[-1][0], frames[-1][0], f"largest .ico frame {frames[-1][0]} px", False
    if ext in rasters.RASTER_EXT:
        im = rasters.open_raster(p)
        return im.size[0], im.size[1], f"{im.size[0]}x{im.size[1]}", True
    return None, None, "not an image", False


def raster_width(path):
    """-> (pixel width, what) for a file a rule paints, or (None, 'vector') for a pure SVG. Raises on unreadable."""
    w, _, what, _ = raster_size(path)
    return w, what


def image_set(value):
    """-> [(url, resolution)] for every candidate of an image-set() in the value (1x when none is given), or None."""
    m = re.search(r"(?:-webkit-)?image-set\(", value, re.I)
    if not m:
        return None
    depth, i = 1, m.end()
    while i < len(value) and depth:
        depth += {"(": 1, ")": -1}.get(value[i], 0)
        i += 1
    out = []
    for arg in split_top(value[m.end():i - 1]):
        u = URL.search(arg)
        if u:
            url, rest = u.group(2), arg[u.end():]
        else:
            q = re.match(r"\s*(['\"])(.*?)\1", arg)
            if not q:
                continue
            url, rest = q.group(2), arg[q.end():]
        r = RESOLUTION.search(rest)
        res = float(r.group(1)) if r else 1.0  # 1x == 1dppx
        out.append((url, res))
    return out


def _subject(part):
    toks = split_top(part, " >+~\t\n")
    return toks[-1] if toks else ""


def _compatible(a, b):
    """Could compound selectors a and b match the same element? False only when provably not."""
    ta, tb = re.match(r"[a-zA-Z][\w-]*", a), re.match(r"[a-zA-Z][\w-]*", b)
    if ta and tb and ta.group(0).lower() != tb.group(0).lower():
        return False
    ia, ib = re.findall(r"#([\w-]+)", a), re.findall(r"#([\w-]+)", b)
    return not (ia and ib and set(ia) != set(ib))


def icon_box(sel, rule_list):
    """Rule 2 of the module docstring -> (w, h, source) or None."""
    found = []
    for part in split_top(sel):
        best = None
        for sel2, decls2 in rule_list:
            for q in split_top(sel2):
                if not (q.startswith(part) and len(q) > len(part) and q[len(part)] in " >"):
                    continue
                last = _subject(q)
                if not any(last == ip or (last.startswith(ip) and not re.match(r"[\w-]", last[len(ip)]))
                           for ip in ICON_PARTS):
                    continue
                w = PX.fullmatch((decls2.get("width") or "").strip())
                h = PX.fullmatch((decls2.get("height") or "").strip())
                if not (w or h):
                    continue
                cand = (float(w.group(1)) if w else None, float(h.group(1)) if h else None, q, decls2)
                if best is None or (cand[0] or 0) * (cand[1] or 0) + (cand[0] or 0) + (cand[1] or 0) > \
                        (best[0] or 0) * (best[1] or 0) + (best[0] or 0) + (best[1] or 0):
                    best = cand
        if best is None:
            return None
        found.append(best)
    if not found:
        return None
    ws = [f[0] for f in found if f[0]]
    hs = [f[1] for f in found if f[1]]
    f = found[0]
    src = f"{f[2]} {{" + "; ".join(f"{k}: {f[3][k]}" for k in ("width", "height") if f[3].get(k)) + "}"
    return (max(ws) if ws else None), (max(hs) if hs else None), src


def _companion_sizes(sel, decls, rule_list, iw, ih):
    """Rule 4: other rules in the file that may match the same element and set a background size.
    -> [(description, ratio or None)]."""
    subjects = [_subject(p) for p in split_top(sel)]
    out = []
    for sel2, decls2 in rule_list:
        if decls2 is decls:
            continue
        size = decls2.get("background-size") or _shorthand_size(decls2.get("background", ""))
        if not size or size.strip().lower() in ("auto", "auto auto"):
            continue
        if not any(_compatible(s, _subject(q)) for s in subjects for q in split_top(sel2)):
            continue
        got = _size_value(size)
        r = None
        if got:
            r = ratio(iw, ih, got[0], got[1]) if got[2] else None
        elif size.strip().lower() in ("contain", "cover"):
            w = PX.fullmatch((decls2.get("width") or "").strip())
            h = PX.fullmatch((decls2.get("height") or "").strip())
            if w and h:
                sx, sy = float(w.group(1)) / iw, float(h.group(1)) / ih
                scale = min(sx, sy) if size.strip().lower() == "contain" else max(sx, sy)
                r = 1 / scale
        dims = "; ".join(f"{k}: {decls2[k]}" for k in ("width", "height") if decls2.get(k))
        same = any(_subject(q) == s for s in subjects for q in split_top(sel2))
        out.append((f"{sel2[:70]} {{background-size: {size}{'; ' + dims if dims else ''}}}", r, same))
    out.sort(key=lambda c: (not c[2], c[1] is None))     # the same subject (e.g. `button`) first, then measured
    return [(d, r) for d, r, _ in out]


def check_file(css_path, rel, index, tree):
    """-> [item dict] for every declaration in one CSS file that paints an image."""
    items = []
    try:
        text = Path(css_path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [_item("CSS-READ", rel, "UNVERIFIABLE", f"cannot read: {e}")]
    rl = rules(text)
    props = custom_props(rl)
    for sel, decls in rl:
        for prop, raw in decls.items():
            if prop not in PAINT_PROPS:
                continue
            value = _expand(raw, props)
            urls = URL.findall(value)
            if not urls:
                continue
            where = f"{rel} {sel[:80]} {{{prop}}}"
            iset = image_set(value)
            cands = iset or [(u, 1.0) for _, u in urls]
            measured = []      # (w, h, what, res, rel, intrinsic)
            unresolved = []
            missing = []
            for u, res in cands:
                if u.startswith("data:"):
                    try:
                        im = _data_raster(u)
                    except Exception as e:  # noqa: BLE001 - a broken data: URI is a finding, not a crash
                        unresolved.append(f"data: URI unreadable ({type(e).__name__})")
                        continue
                    if im is None:
                        measured.append((None, None, "vector data: URI", res, "data:", False))
                    else:
                        measured.append((im.size[0], im.size[1], f"data: {im.size[0]}x{im.size[1]}", res, "data:", True))
                    continue
                path, kind = chrome.resolve(u, css_path, index)
                if kind == "external":
                    unresolved.append(f"{u} is not a tree file (resource:/moz-icon:/http:)")
                    continue
                if kind == "missing":
                    missing.append(u)
                    continue
                if path is None:
                    unresolved.append(f"{u} does not resolve to one file in the tree (jar.mn, then by file name)")
                    continue
                try:
                    w, h, what, intrinsic = raster_size(path)
                except Exception as e:  # noqa: BLE001
                    unresolved.append(f"{u}: unreadable ({type(e).__name__}: {e})")
                    continue
                measured.append((w, h, what, res, Path(path).relative_to(tree).as_posix() if _under(path, tree)
                                 else str(path), intrinsic))
            if missing:
                items.append(_item("ASSET-MISSING", where, "FAIL",
                                   f"{', '.join(missing)}: no file of that name anywhere in the tree; the browser paints "
                                   f"nothing (a broken image)"))
            ras = [m for m in measured if m[0] is not None]
            if prop == "content" and not clamped(decls) and (ras or unresolved):
                items.append(_item("CSS-004", where, "FAIL" if ras else "UNVERIFIABLE",
                                   f"content: {raw[:60]} injects " + (f"a raster ({ras[0][4]}, {ras[0][2]})" if ras else
                                   "an image this check cannot measure") + " with no width/max-width or height/max-height "
                                   "clamp in the same rule (the 200 px explosion, ERR-UI-004)"))
            if unresolved and not measured:
                items.append(_item("ASSET-002", where, "UNVERIFIABLE", "; ".join(unresolved)))
                continue
            if not measured:
                continue
            if not ras:
                items.append(_item("ASSET-002", where, "PASS", "vector: " + ", ".join(m[4] for m in measured)))
                continue
            best = max(ras, key=lambda m: m[0])      # the largest real raster among the candidates
            have2x = any(m[3] >= 2 for m in ras) or len(ras) > 1
            sib = _sibling_2x(best[4], tree)
            hint = f"; {sib} exists but this rule does not use it (image-set)" if sib and not have2x else ""
            bw, bh, bsrc, exact = box(decls)
            if bw is None and bh is None:
                ib = icon_box(sel, rl)
                if ib:
                    bw, bh, bsrc, exact = ib[0], ib[1], ib[2], True
            if bw is None and bh is None:
                verdict = _unboxed(sel, prop, decls, rl, iset, ras, unresolved, missing, hint)
                if verdict:
                    items.append(_item("ASSET-002", where, *verdict))
                else:
                    items.append(_item("ASSET-002", where, "UNVERIFIABLE",
                                       f"paints {best[4]} ({best[2]}) into a box this rule does not state in px "
                                       f"(contain/auto/%): the runtime layer measures it"))
                if unresolved:
                    items.append(_item("ASSET-002", where + " (other candidates)", "UNVERIFIABLE", "; ".join(unresolved)))
                continue
            r = ratio(best[0], best[1], bw, bh)
            if not exact:
                if r >= rasters.HEADROOM:
                    items.append(_item("ASSET-002", where, "PASS",
                                       f"{best[4]} ({best[2]}) into {bsrc} (at most {_px(bw, bh)}): {r:.2f}x or more"))
                else:
                    items.append(_item("ASSET-002", where, "UNVERIFIABLE",
                                       f"{best[4]} ({best[2]}) into {bsrc}: the box is only bounded (at most "
                                       f"{_px(bw, bh)}, {r:.2f}x at that bound, need {rasters.HEADROOM:.0f}x); the "
                                       f"runtime layer measures the real box"))
            elif r < 1:
                items.append(_item("ASSET-002", where, "FAIL",
                                   f"UPSCALES {best[4]} ({best[2]}) into {bsrc}: {r:.2f}x, blurry on every screen{hint}"))
            elif r < rasters.HEADROOM:
                items.append(_item("ASSET-002", where, "FAIL",
                                   f"{best[4]} ({best[2]}) into {bsrc}: {r:.2f}x headroom, need {rasters.HEADROOM:.0f}x "
                                   f"(blurry at 200% scaling){hint}"))
            else:
                items.append(_item("ASSET-002", where, "PASS", f"{best[4]} ({best[2]}) into {bsrc}: {r:.2f}x"))
            if unresolved:
                items.append(_item("ASSET-002", where + " (other candidates)", "UNVERIFIABLE", "; ".join(unresolved)))
    return items


def _px(w, h):
    return " x ".join(f"{v:g}px" for v in (w, h) if v) or "?"


def _unboxed(sel, prop, decls, rl, iset, ras, unresolved, missing, hint):
    """Rules 3 and 4 of the module docstring -> (verdict, evidence) or None (stays UNVERIFIABLE)."""
    if iset:
        ones = [m for m in ras if m[3] == 1.0 and m[5]]
        if not ones or unresolved or missing:
            return None
        one = ones[0]
        bw, bh = one[0], one[1]
        short = [m for m in ras if m[0] < m[3] * bw or m[1] < m[3] * bh]
        src = f"the 1x candidate's intrinsic box {one[4]} {bw}x{bh}"
        if short:
            return "FAIL", "; ".join(f"{m[4]} ({m[2]}) is the {m[3]:g}x candidate but holds only "
                                     f"{ratio(m[0], m[1], bw, bh):.2f}x of {src}" for m in short)
        hi = [m for m in ras if m[3] >= rasters.HEADROOM]
        if not hi:
            return "FAIL", (f"image-set() has no {rasters.HEADROOM:g}x candidate: drawn at {src}, 1.00x, blurry at 200% "
                            f"scaling{hint}")
        top = max(hi, key=lambda m: m[0])
        return "PASS", f"{top[4]} ({top[2]}) is the {top[3]:g}x candidate of {src}: {ratio(top[0], top[1], bw, bh):.2f}x"
    if prop not in BG_PROPS or len(ras) != 1 or not ras[0][5] or unresolved:
        return None
    if (decls.get("background-size") or "auto").strip().lower() not in ("auto", "auto auto") or \
            _shorthand_size(decls.get("background", "")):
        return None
    m = ras[0]
    intrinsic = f"{m[4]} ({m[2]}) drawn at its intrinsic size (background-size: auto): 1.00x"
    comps = _companion_sizes(sel, decls, rl, m[0], m[1])
    if not comps:
        return "FAIL", f"{intrinsic}, blurry at 200% scaling; no rule in this file sizes it{hint}"
    listed = "; ".join(f"{d} -> " + (f"{r:.2f}x" if r is not None else "not measurable") for d, r in comps[:4])
    more = f" (+{len(comps) - 4} more)" if len(comps) > 4 else ""
    if all(r is not None and r < rasters.HEADROOM for _, r in comps):
        return "FAIL", f"{intrinsic} if no other rule applies; every rule here that may size it is also below " \
                       f"{rasters.HEADROOM:g}x: {listed}{more}"
    return "UNVERIFIABLE", (f"{intrinsic} if no other rule applies, but {len(comps)} rule(s) in this file may match the "
                            f"same element and size it (which one applies needs the DOM): {listed}{more}")


def _under(p, tree):
    try:
        Path(p).resolve().relative_to(Path(tree).resolve())
        return True
    except ValueError:
        return False


def _sibling_2x(rel, tree):
    if not rel or rel.startswith("data:"):
        return None
    p = Path(tree) / rel
    cand = p.with_name(p.stem + "@2x" + p.suffix)
    return cand.relative_to(tree).as_posix() if cand.is_file() else None


def _item(rule, item, verdict, evidence):
    return {"layer": "static", "rule": rule, "item": item, "verdict": verdict, "evidence": evidence}
