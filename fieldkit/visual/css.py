"""Static CSS checks: a rule that paints a raster must not upscale it.

    ASSET-002  the raster's pixel width >= 2 x the CSS box it is painted into (doctrine; below 2x the browser
               resamples upward on a HiDPI screen, below 1x on every screen). An image-set() with a 2x candidate
               is judged by its best candidate.
    CSS-004    `content: url()/var()` injected into an element with no size clamp (width/max-width or
               height/max-height in the same rule): the 200 px explosion, ERR-UI-004, twice (doctrine section 4).

The box comes from the rule itself: background-size in px, else width/height (or max-width/max-height) in px.
A rule that paints a raster into a box it does not state is UNVERIFIABLE here (the runtime layer measures the
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
RES = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)\s*(\d+(?:\.\d+)?)x")


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


def box(decls):
    """-> (width px or None, source) from the rule: background-size, then width, then max-width."""
    bs = decls.get("background-size", "")
    m = PX.findall(bs)
    if m:
        return float(m[0]), f"background-size: {bs}"
    short = decls.get("background", "")
    m = re.search(r"/\s*(\d+(?:\.\d+)?)px", short)
    if m:
        return float(m.group(1)), f"background: ... / {m.group(1)}px"
    for k in ("width", "max-width", "height", "max-height"):
        v = decls.get(k, "")
        m = PX.fullmatch(v.strip()) if v else None
        if m:
            return float(m.group(1)), f"{k}: {v}"
    return None, None


def clamped(decls):
    return any(PX.fullmatch((decls.get(k) or "").strip()) for k in ("width", "max-width", "height", "max-height"))


def _data_raster(url):
    m = re.match(r"data:image/(png|jpeg|gif|webp);base64,(.+)$", url.strip(), re.S)
    if not m:
        return None
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(re.sub(r"\s", "", m.group(2)))))
    im.load()
    return im


def raster_width(path):
    """-> (pixel width, what) for a file a rule paints, or (None, 'vector') for a pure SVG. Raises on unreadable."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".svg":
        embedded = rasters.svg_rasters(p)
        if not embedded:
            return None, "vector"
        im = max((im for _, im in embedded), key=lambda i: i.size[0])
        return im.size[0], f"PNG embedded in {p.name}, {im.size[0]}x{im.size[1]}"
    if ext == ".ico":
        frames = rasters.ico_frames(p)
        return frames[-1][0], f"largest .ico frame {frames[-1][0]} px"
    if ext in rasters.RASTER_EXT:
        im = rasters.open_raster(p)
        return im.size[0], f"{im.size[0]}x{im.size[1]}"
    return None, "not an image"


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
            cands = [(u, float(r)) for _, u, r in RES.findall(value)] if "image-set" in value else []
            cands = cands or [(u, 1.0) for _, u in urls]
            measured = []
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
                        measured.append((None, "vector data: URI", res, "data:"))
                    else:
                        measured.append((im.size[0], f"data: {im.size[0]}x{im.size[1]}", res, "data:"))
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
                    w, what = raster_width(path)
                except Exception as e:  # noqa: BLE001
                    unresolved.append(f"{u}: unreadable ({type(e).__name__}: {e})")
                    continue
                measured.append((w, what, res, Path(path).relative_to(tree).as_posix() if _under(path, tree) else str(path)))
            if missing:
                items.append(_item("ASSET-MISSING", where, "FAIL",
                                   f"{', '.join(missing)}: no file of that name anywhere in the tree; the browser paints "
                                   f"nothing (a broken image)"))
            ras = [m for m in measured if m[0] is not None]
            if prop == "content" and not clamped(decls) and (ras or unresolved):
                items.append(_item("CSS-004", where, "FAIL" if ras else "UNVERIFIABLE",
                                   f"content: {raw[:60]} injects " + (f"a raster ({ras[0][3]}, {ras[0][1]})" if ras else
                                   "an image this check cannot measure") + " with no width/max-width or height/max-height "
                                   "clamp in the same rule (the 200 px explosion, ERR-UI-004)"))
            if unresolved and not measured:
                items.append(_item("ASSET-002", where, "UNVERIFIABLE", "; ".join(unresolved)))
                continue
            if not measured:
                continue
            if not ras:
                items.append(_item("ASSET-002", where, "PASS", "vector: " + ", ".join(m[3] for m in measured)))
                continue
            bw, bsrc = box(decls)
            best = max(ras, key=lambda m: m[0])      # the largest real raster among the candidates
            have2x = any(m[2] >= 2 for m in ras) or len(ras) > 1
            sib = _sibling_2x(best[3], tree)
            if bw is None:
                items.append(_item("ASSET-002", where, "UNVERIFIABLE",
                                   f"paints {best[3]} ({best[1]}) into a box this rule does not state in px "
                                   f"(contain/auto/%): the runtime layer measures it"))
                continue
            ratio = best[0] / bw
            hint = f"; {sib} exists but this rule does not use it (image-set)" if sib and not have2x else ""
            if ratio < 1:
                items.append(_item("ASSET-002", where, "FAIL",
                                   f"UPSCALES {best[3]} ({best[1]}) into {bsrc}: {ratio:.2f}x, blurry on every screen{hint}"))
            elif ratio < rasters.HEADROOM:
                items.append(_item("ASSET-002", where, "FAIL",
                                   f"{best[3]} ({best[1]}) into {bsrc}: {ratio:.2f}x headroom, need {rasters.HEADROOM:.0f}x "
                                   f"(blurry at 200% scaling){hint}"))
            else:
                items.append(_item("ASSET-002", where, "PASS", f"{best[3]} ({best[1]}) into {bsrc}: {ratio:.2f}x"))
            if unresolved:
                items.append(_item("ASSET-002", where + " (other candidates)", "UNVERIFIABLE", "; ".join(unresolved)))
    return items


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
