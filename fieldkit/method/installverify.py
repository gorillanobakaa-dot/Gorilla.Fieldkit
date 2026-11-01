"""fieldkit install: is a program really installed the way its release says, as the desktop will find it?

    fieldkit install verify PRODUCT [--tag vX] [--ref FILE.json] [--proof FILE] [--json]
    fieldkit install self-test [--json]

Owner, 2026-11-01: the Gorilla OpenCode icon must be crisp, big, clearly visible and installed on Debian GNOME
(Wayland), proven by a tool, not by eye. Found on the VAIO: an old Jul-23 icon in ~/.local/share/icons shadowed the
release's in /usr/share/icons, and nothing said so. So the checks read the machine the way GNOME does:

- binary: `which -a` first == the file the .desktop Exec= runs, and its sha256 is the release's SHA256SUMS line.
- .desktop: one effective entry (the user's wins over /usr/share), a second one that is not the release's is stale,
  `desktop-file-validate` clean, Icon= the theme NAME, Terminal= and Categories= present, no other launcher for it.
- icons: for every size the release ships, and 48/64/96 and scalable (the app grid and dash pick 64-128 px, 2x on
  HiDPI: a gap is a blurry icon), the file GNOME resolves FIRST (~/.local/share/icons, ~/.icons, $XDG_DATA_DIRS/icons;
  another theme's copy is reported too) has the release's sha256; PNGs are the stated pixel size, 8-bit RGBA, and not
  a nearest-neighbour upscale (every 2x2 block equal) of a smaller one. A smooth upscale is NOT detected.
- caches: icon-theme.cache newer than the newest icon in each hicolor dir holding it; the desktop database newer than
  the .desktop; whether the running GNOME Shell is older than the icons (a fact, not a failure: Wayland keeps the old
  icon in memory until log out/in).

The reference is the release itself: the .deb is downloaded from the public release URL into a scratch folder and
checked against SHA256SUMS before anything is compared. Linux only; Windows and macOS say `not-this-platform`.
Exit 0 every check passes, 2 bad usage or no reference, 3 a check failed (each with its fix).
"""
import argparse
import configparser
import glob
import hashlib
import json
import os
import shlex
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

PRODUCTS = ("gorilla-opencode",)
REQUIRED_SIZES = (48, 64, 96, 128, 256, 512)       # the release's own sizes are added to these
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


# ---- PNG ------------------------------------------------------------------------------------------------------------
def png_info(data):
    """-> {w, h, depth, ctype, interlace} from the IHDR chunk, or None when this is no PNG."""
    if data[:8] != PNG_SIG or data[12:16] != b"IHDR" or len(data) < 33:
        return None
    w, h, depth, ctype, _comp, _flt, inter = struct.unpack(">IIBBBBB", data[16:29])
    return {"w": w, "h": h, "depth": depth, "ctype": ctype, "interlace": inter}


def _idat(data):
    out, i = [], 8
    while i + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[i:i + 8])
        if kind == b"IDAT":
            out.append(data[i + 8:i + 8 + n])
        i += 12 + n
    return b"".join(out)


def pixel_rows(data, info):
    """Rows of 8-bit RGBA pixels as bytes (unfiltered), or None for any other PNG layout."""
    if info["depth"] != 8 or info["ctype"] != 6 or info["interlace"]:
        return None
    w, h, bpp = info["w"], info["h"], 4
    raw = zlib.decompress(_idat(data))
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        if f == 1:
            for i in range(bpp, stride):
                cur[i] = (cur[i] + cur[i - bpp]) & 255
        elif f == 2:
            for i in range(stride):
                cur[i] = (cur[i] + prev[i]) & 255
        elif f == 3:
            for i in range(stride):
                cur[i] = (cur[i] + (((cur[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 255
        elif f == 4:
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                cur[i] = (cur[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        elif f != 0:
            return None
        rows.append(bytes(cur))
        prev = cur
    return rows


def blocky_upscale(data, info):
    """True when every 2x2 block of pixels is one colour: the picture was enlarged by pixel repetition.
    None when the layout is not read (not 8-bit RGBA, over 512 px)."""
    if info["w"] > 512 or info["h"] > 512 or info["w"] % 2 or info["h"] % 2:
        return None
    rows = pixel_rows(data, info)
    if rows is None:
        return None
    for y in range(0, info["h"], 2):
        if rows[y] != rows[y + 1]:
            return False
        v = memoryview(rows[y]).cast("I")
        if v[0::2].tolist() != v[1::2].tolist():
            return False
    return True


# ---- where things are ------------------------------------------------------------------------------------------------
def login_path(run=_run):
    """The PATH a login gets (~/.profile adds ~/.local/bin): the desktop session's, not this tool's own shell's."""
    rc, out, _ = run([os.environ.get("SHELL") or "bash", "-lc", 'printf %s "$PATH"'])
    return out.strip() if rc == 0 and out.strip() else None


def environment(home=None, env=None):
    live = env is None
    env = dict(os.environ) if live else dict(env)
    if live:
        env["PATH"] = login_path() or env.get("PATH", "")
    home = Path(home or Path.home())
    dh = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    dd = [Path(x) for x in (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if x]
    return {"home": home, "data_home": dh, "data_dirs": dd,
            "path": [x for x in (env.get("PATH") or "").split(os.pathsep) if x],
            "pixmaps": [Path("/usr/share/pixmaps")]}


def icon_bases(e):
    """GNOME's icon lookup order for the hicolor and other themes."""
    return [e["data_home"] / "icons", e["home"] / ".icons", *[d / "icons" for d in e["data_dirs"]]]


def app_dirs(e):
    return [e["data_home"] / "applications", *[d / "applications" for d in e["data_dirs"]]]


def which_all(name, path):
    out = []
    for d in path:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK) and p not in out:
            out.append(p)
    return out


def tilde(p, e):
    s = str(p)
    h = str(e["home"])
    return "~" + s[len(h):] if s.startswith(h) else s


def row(check, ok, line, fix=""):
    return {"check": check, "ok": ok, "line": line, "fix": fix if not ok else ""}


# ---- the checks -----------------------------------------------------------------------------------------------------
def desktop_fields(path):
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(Path(path).read_text(encoding="utf-8", errors="replace"))
    return dict(cp["Desktop Entry"]) if cp.has_section("Desktop Entry") else {}


def exec_target(fields, path):
    try:
        tok = shlex.split(fields.get("Exec", ""))[0]
    except (IndexError, ValueError):
        return None
    if "/" in tok:
        return tok
    found = which_all(tok, path)
    return found[0] if found else None


def check_binary(product, ref, e, desktop_path, fields):
    rows = []
    allw = which_all(product, e["path"])
    target = exec_target(fields, e["path"]) if fields else None
    if not allw:
        return [row("binary.path", False, f"`which -a {product}` finds nothing on PATH",
                    f"put {tilde(e['home'] / '.local' / 'bin', e)} on PATH (fieldkit parity align --apply installs it there)")]
    first = allw[0]
    same = target is not None and os.path.exists(target) and os.path.samefile(first, target)
    rows.append(row("binary.path", same,
                    f"`which -a` first = {tilde(first, e)}; {tilde(desktop_path, e) if desktop_path else 'no .desktop'} "
                    f"Exec= runs {tilde(target, e) if target else 'nothing'}"
                    + (f"; also on PATH: {', '.join(tilde(x, e) for x in allw[1:])}" if len(allw) > 1 else ""),
                    "make the first copy on PATH and the .desktop Exec= the same file: put ~/.local/bin first on PATH, or "
                    "remove the older copy (sudo apt remove it) / rerun `fieldkit parity align --apply`"))
    want = ref.get("binary_sha")
    for p in dict.fromkeys([first, target] if target and target != first else [first]):
        got = sha256(p) if os.path.exists(p) else None
        rows.append(row("binary.sha256", got is not None and got == want,
                        f"{tilde(p, e)} sha256 {got} {'==' if got == want else '!='} the release's {want}",
                        "reinstall the release's binary: fieldkit parity align --apply"))
    return rows


def check_desktop(product, ref, e, run):
    """-> (rows, effective path or None, its fields)."""
    name = f"{product}.desktop"
    found = [d / name for d in app_dirs(e) if (d / name).is_file()]
    rows = []
    if not found:
        return [row("desktop.entry", False, f"no {name} in {', '.join(tilde(d, e) for d in app_dirs(e))}",
                    f"run `{product} install` (per user)")], None, {}
    eff = found[0]
    bad = [p for p in found[1:] if ref.get("desktop_sha") and sha256(p) != ref["desktop_sha"]]
    rows.append(row("desktop.entry", not bad,
                    f"effective {tilde(eff, e)}" + (f"; a second, older entry {', '.join(tilde(p, e) for p in bad)}" if bad
                    else (f"; {len(found) - 1} shadowed copy equals the release's" if len(found) > 1 else "; no second entry")),
                    "recycle the stale entry (`fieldkit size recycle PATH`; under /usr/share it needs sudo)"))
    fields = desktop_fields(eff)
    others = []
    for d in app_dirs(e):
        for p in sorted(glob.glob(str(d / "*.desktop"))):
            if Path(p).name == name:
                continue
            try:
                tok = shlex.split(desktop_fields(p).get("Exec", ""))[0]
            except (IndexError, ValueError, configparser.Error):
                continue
            if os.path.basename(tok) == product:
                others.append(p)
    rows.append(row("desktop.other-launcher", not others,
                    "no other launcher runs " + product if not others else "other launchers run it: " + ", ".join(map(str, others)),
                    "recycle them (`fieldkit size recycle PATH`)"))
    rc, out, err = run(["desktop-file-validate", str(eff)])
    if rc == 127:
        rows.append(row("desktop.valid", False, "desktop-file-validate is not installed", "sudo apt install desktop-file-utils"))
    else:
        rows.append(row("desktop.valid", rc == 0 and not out.strip(),
                        f"desktop-file-validate {tilde(eff, e)}: " + ((out + err).strip()[:200] or "clean"),
                        "fix the lines it names (rerun `" + product + " install` rewrites the entry)"))
    icon = fields.get("Icon", "")
    rows.append(row("desktop.icon-name", icon == product,
                    f"Icon={icon!r} (the theme name {product!r} is wanted, not a path)", f"set Icon={product}"))
    rows.append(row("desktop.terminal-categories", "Terminal" in fields and bool(fields.get("Categories")),
                    f"Terminal={fields.get('Terminal')!r} Categories={fields.get('Categories')!r}", "set both in the entry"))
    return rows, eff, fields


def _icon_file(base, theme, size, product):
    ext = "svg" if size == "scalable" else "png"
    return base / theme / size / "apps" / f"{product}.{ext}"


def check_icons(product, ref, e):
    """-> (rows, resolved {size: (path, sha)})"""
    rows, resolved = [], {}
    sizes = [f"{s}x{s}" for s in REQUIRED_SIZES]
    sizes += [s for s in ref.get("icons", {}) if s not in sizes and s != "scalable"]
    sizes = sorted(sizes, key=lambda s: int(s.split("x")[0])) + ["scalable"]
    bases = icon_bases(e)
    for size in sizes:
        want = ref.get("icons", {}).get(size)
        chain = [p for p in (_icon_file(b, "hicolor", size, product) for b in bases) if p.is_file()]
        if want is None:
            rows.append(row(f"icon.{size}", False,
                            f"the release ships no {size} icon" + (f" (the machine has {tilde(chain[0], e)})" if chain else ""),
                            "the release lacks this size: add it to the Gorilla.Opencode packaging (GNOME scales another size, blurry)"))
            continue
        if not chain:
            rows.append(row(f"icon.{size}", False, f"{size}: no file in {', '.join(tilde(b, e) for b in bases)}",
                            f"run `{product} install` (per user) or reinstall the package"))
            continue
        first = chain[0]
        got = sha256(first)
        resolved[size] = (str(first), got)
        if got != want:
            good = [p for p in chain[1:] if sha256(p) == want]
            rows.append(row(f"icon.{size}", False,
                            f"{size}: GNOME resolves {tilde(first, e)} sha256 {got[:12]} but the release's is {want[:12]}"
                            + (f"; the release's copy {tilde(good[0], e)} is shadowed by it" if good else ""),
                            f"recycle the stale file (`fieldkit size recycle {first}`) or rerun `{product} install`"))
            continue
        why = ""
        if size == "scalable":
            if b"<svg" not in first.read_bytes()[:4096]:
                why = "not an SVG"
        else:
            px = int(size.split("x")[0])
            data = first.read_bytes()
            info = png_info(data)
            if not info:
                why = "not a PNG"
            elif (info["w"], info["h"]) != (px, px):
                why = f"the PNG is {info['w']}x{info['h']}, not {px}x{px}"
            elif (info["depth"], info["ctype"]) != (8, 6):
                why = f"the PNG is depth {info['depth']} colour type {info['ctype']}, not 8-bit RGBA"
            elif blocky_upscale(data, info):
                why = "the PNG is a pixel-repeated upscale of a smaller picture"
        rows.append(row(f"icon.{size}", not why,
                        f"{size}: {tilde(first, e)} sha256 {got[:12]} == the release's" + (f" BUT {why}" if why else ""),
                        "the release's own file is wrong for this size: fix it in Gorilla.Opencode packaging"))
    # another theme's copy can be picked before hicolor when it is the current theme
    other = [p for b in bases for p in glob.glob(str(b / "*" / "*" / "apps" / f"{product}.*"))
             if Path(p).relative_to(b).parts[0] != "hicolor"]
    rows.append(row("icon.other-theme", not other, "no other theme holds the icon" if not other else "other themes hold it: " + ", ".join(other),
                    "recycle them (`fieldkit size recycle PATH`)"))
    return rows, resolved


def _newest(paths):
    return max((os.path.getmtime(p) for p in paths), default=0)


def check_caches(product, e, eff, session_start=None):
    rows = []
    for b in icon_bases(e):
        hi = b / "hicolor"
        files = glob.glob(str(hi / "*" / "apps" / f"{product}.*"))
        if not files:
            continue
        cache = hi / "icon-theme.cache"
        ok = cache.is_file() and os.path.getmtime(cache) >= _newest(files)
        rows.append(row(f"cache.icons:{tilde(hi, e)}", ok,
                        f"{tilde(cache, e)} " + ("is newer than its newest icon" if ok else "is missing or older than an icon in it"),
                        f"gtk-update-icon-cache -f -t {hi}" + ("" if os.access(hi, os.W_OK) else " (needs sudo)")))
    db = e["data_home"] / "applications" / "mimeinfo.cache"
    if eff and Path(eff).parent == e["data_home"] / "applications":
        ok = db.is_file() and os.path.getmtime(db) >= os.path.getmtime(eff)
        rows.append(row("cache.desktop-db", ok, f"{tilde(db, e)} " + ("is newer than the .desktop" if ok else "is missing or older than the .desktop"),
                        f"update-desktop-database {db.parent}"))
    newest = max([_newest(glob.glob(str(b / "hicolor" / "*" / "apps" / f"{product}.*"))) for b in icon_bases(e)] + [0])
    if session_start is not None and newest:
        old = session_start < newest
        rows.append({"check": "session", "ok": True, "info": True, "fix": "",
                     "line": "the running GNOME session started before the icon change: the running GNOME session keeps the old icon "
                             "in memory until log out/in (Wayland cannot reload the shell in place)" if old else
                             "the running GNOME session started after the newest icon change"})
    return rows


def shell_start(run=_run, now=None):
    rc, out, _ = run(["ps", "-C", "gnome-shell", "-o", "etimes="])
    try:
        return (now or time.time()) - int(out.split()[0])
    except (IndexError, ValueError):
        return None


def verify(product, ref, e=None, run=_run, session_start=None):
    e = e or environment()
    drows, eff, fields = check_desktop(product, ref, e, run)
    irows, resolved = check_icons(product, ref, e)
    rows = check_binary(product, ref, e, eff, fields) + drows + irows + check_caches(product, e, eff, session_start)
    return rows, resolved


# ---- the reference: the release itself ------------------------------------------------------------------------------
def reference_from_release(product, tag, repo, folder, run=_run, fetch_fn=None):
    """Download the release's .deb (checked against SHA256SUMS) into a new folder and read what a correct install holds."""
    from .. import parity
    from ..build import relfiles
    fetch_fn = fetch_fn or parity.fetch_public
    ver = tag.lstrip("v")
    deb, _ = fetch_fn(tag, repo, f"{product}_{ver}_amd64.deb", Path(folder) / "dl")
    sums = relfiles.parse_sums(next(Path(folder, "dl").glob("SHA256SUMS*")))
    raw = f"{product}-{tag}-linux-amd64"
    if raw not in sums:
        raise RuntimeError(f"SHA256SUMS does not list {raw}")
    x = Path(folder) / "x"
    rc, out, err = run(["dpkg-deb", "-x", str(deb), str(x)])
    if rc != 0:
        raise RuntimeError(f"dpkg-deb -x failed: {(err or out)[:200]}")
    icons = {}
    for p in glob.glob(str(x / "usr" / "share" / "icons" / "hicolor" / "*" / "apps" / f"{product}.*")):
        icons[Path(p).parts[-3]] = sha256(p)
    dsk = x / "usr" / "share" / "applications" / f"{product}.desktop"
    return {"tag": tag, "repo": repo, "binary_sha": sums[raw], "icons": icons, "desktop_sha": sha256(dsk) if dsk.is_file() else None}


# ---- self-test: a fake HOME with every break seeded ------------------------------------------------------------------
def _png(size, seed, rgba=True, repeat=1):
    """A valid PNG of size x size whose pixels are noise (so it is no blocky upscale), or repeated `repeat` times."""
    import random
    rnd = random.Random(seed)
    n = size // repeat
    px = [[bytes(rnd.randrange(256) for _ in range(4)) for _ in range(n)] for _ in range(n)]
    raw = b""
    for y in range(size):
        r = px[y // repeat]
        line = b"".join(r[x // repeat] for x in range(size))
        if not rgba:
            line = b"".join(line[i:i + 3] for i in range(0, len(line), 4))
        raw += b"\x00" + line

    def chunk(k, d):
        return struct.pack(">I", len(d)) + k + d + struct.pack(">I", zlib.crc32(k + d))
    return (PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6 if rgba else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


DESKTOP = ("[Desktop Entry]\nType=Application\nName=Fake Product\nExec={exec} launch\nIcon={icon}\nTerminal=true\n"
           "Categories=Development;IDE;\n")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64"/></svg>'


class Fake:
    """product 'fakeprod' in a fake HOME and a fake /usr/share; self.ref is the matching 'release'."""
    product = "fakeprod"

    def __init__(self, root):
        self.root = Path(root)
        self.home = self.root / "home"
        self.sysshare = self.root / "usr" / "share"
        self.bin = self.home / ".local" / "bin"
        self.release_png = {s: _png(s, s) for s in REQUIRED_SIZES}
        self.binary = b"#!/bin/sh\necho release\n"
        self.desktop = DESKTOP.format(exec=self.bin / self.product, icon=self.product)
        for base in (self.home / ".local" / "share" / "icons", ):
            for s, d in self.release_png.items():
                self.put(base / "hicolor" / f"{s}x{s}" / "apps" / f"{self.product}.png", d)
            self.put(base / "hicolor" / "scalable" / "apps" / f"{self.product}.svg", SVG)
        self.put(self.bin / self.product, self.binary, 0o755)
        self.put(self.home / ".local" / "share" / "applications" / f"{self.product}.desktop", self.desktop.encode())
        self.touch_caches()

    def put(self, p, data, mode=0o644):
        p = Path(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(mode)

    def touch_caches(self, when=None):
        when = when or time.time() + 10
        for p in [self.home / ".local" / "share" / "icons" / "hicolor" / "icon-theme.cache",
                  self.home / ".local" / "share" / "applications" / "mimeinfo.cache"]:
            p.write_bytes(b"c")
            os.utime(p, (when, when))

    @property
    def ref(self):
        icons = {f"{s}x{s}": hashlib.sha256(d).hexdigest() for s, d in self.release_png.items()}
        icons["scalable"] = hashlib.sha256(SVG).hexdigest()
        return {"binary_sha": hashlib.sha256(self.binary).hexdigest(), "icons": icons,
                "desktop_sha": hashlib.sha256(self.desktop.encode()).hexdigest()}

    @property
    def env(self):
        return environment(self.home, {"XDG_DATA_DIRS": str(self.sysshare), "PATH": f"{self.bin}:{self.root / 'usr' / 'bin'}"})

    def icon(self, base, size, ext="png"):
        return base / "hicolor" / (f"{size}x{size}" if size != "scalable" else size) / "apps" / f"{self.product}.{ext}"

    @property
    def uicons(self):
        return self.home / ".local" / "share" / "icons"

    @property
    def sicons(self):
        return self.sysshare / "icons"


def _seed_old_icon(f):
    f.put(f.icon(f.sicons, 256), f.release_png[256])           # the release's copy, shadowed
    f.put(f.icon(f.uicons, 256), _png(256, 999))                # an old one first


def _seed_missing_size(f):
    f.icon(f.uicons, 64).unlink()


def _seed_wrong_pixels(f):
    f.release_png[96] = _png(64, 5)                              # the "release" file is 64 px under the 96 name
    f.put(f.icon(f.uicons, 96), f.release_png[96])


def _seed_upscaled(f):
    f.release_png[128] = _png(128, 7, repeat=2)
    f.put(f.icon(f.uicons, 128), f.release_png[128])


def _seed_not_rgba(f):
    f.release_png[48] = _png(48, 8, rgba=False)
    f.put(f.icon(f.uicons, 48), f.release_png[48])


def _seed_no_scalable(f):
    f.icon(f.uicons, "scalable", "svg").unlink()


def _seed_stale_cache(f):
    f.touch_caches(time.time() - 3600)


def _seed_stale_desktop_db(f):
    p = f.home / ".local" / "share" / "applications" / "mimeinfo.cache"
    os.utime(p, (time.time() - 3600,) * 2)


def _seed_second_desktop(f):
    f.put(f.sysshare / "applications" / f"{f.product}.desktop", DESKTOP.format(exec="/usr/bin/old", icon=f.product).encode())


def _seed_other_launcher(f):
    f.put(f.home / ".local" / "share" / "applications" / "old-name.desktop",
          DESKTOP.format(exec=f.bin / f.product, icon=f.product).encode())


def _seed_exec_elsewhere(f):
    f.put(f.root / "elsewhere" / f.product, f.binary, 0o755)
    f.put(f.home / ".local" / "share" / "applications" / f"{f.product}.desktop",
          DESKTOP.format(exec=f.root / "elsewhere" / f.product, icon=f.product).encode())


def _seed_binary_hash(f):
    f.put(f.bin / f.product, b"#!/bin/sh\necho OLD\n", 0o755)


def _seed_path_shadow(f):
    f.put(f.root / "usr" / "bin" / f.product, f.binary, 0o755)
    f.root.joinpath("usr", "bin", f.product).chmod(0o755)
    p = f.home / ".local" / "share" / "applications" / f"{f.product}.desktop"
    f.put(p, DESKTOP.format(exec=f.root / "usr" / "bin" / f.product, icon=f.product).encode())   # desktop runs /usr/bin copy
    f.bin.joinpath(f.product).write_bytes(f.binary)                                         # first on PATH is another file


def _seed_icon_path(f):
    p = f.home / ".local" / "share" / "applications" / f"{f.product}.desktop"
    f.put(p, DESKTOP.format(exec=f.bin / f.product, icon="../old.png").encode())


def _seed_no_categories(f):
    p = f.home / ".local" / "share" / "applications" / f"{f.product}.desktop"
    f.put(p, f.desktop.replace("Categories=Development;IDE;\n", "").encode())


def _seed_invalid_desktop(f):
    p = f.home / ".local" / "share" / "applications" / f"{f.product}.desktop"
    f.put(p, (f.desktop + "Bogus-Key-Without-Meaning=1\nType=Link\n").encode())


def _seed_other_theme(f):
    f.put(f.uicons / "Adwaita" / "256x256" / "apps" / f"{f.product}.png", f.release_png[256])


#: (what is broken, seed, the check that must fail)
BREAKS = [
    ("old icon shadowing the release's", _seed_old_icon, "icon.256x256"),
    ("a size missing (64)", _seed_missing_size, "icon.64x64"),
    ("PNG with the wrong pixel size", _seed_wrong_pixels, "icon.96x96"),
    ("PNG that is a pixel-repeated upscale", _seed_upscaled, "icon.128x128"),
    ("PNG that is not RGBA", _seed_not_rgba, "icon.48x48"),
    ("scalable SVG missing", _seed_no_scalable, "icon.scalable"),
    ("another theme holding the icon", _seed_other_theme, "icon.other-theme"),
    ("icon cache older than the icons", _seed_stale_cache, "cache.icons"),
    ("desktop database older than the .desktop", _seed_stale_desktop_db, "cache.desktop-db"),
    ("a second, older .desktop", _seed_second_desktop, "desktop.entry"),
    ("another launcher for the program", _seed_other_launcher, "desktop.other-launcher"),
    ("Exec= runs another binary", _seed_exec_elsewhere, "binary.path"),
    ("first binary on PATH is not the Exec= one", _seed_path_shadow, "binary.path"),
    ("binary hash differs from the release's", _seed_binary_hash, "binary.sha256"),
    ("Icon= is a path", _seed_icon_path, "desktop.icon-name"),
    ("Categories= missing", _seed_no_categories, "desktop.terminal-categories"),
    ("desktop-file-validate errors", _seed_invalid_desktop, "desktop.valid"),
]


def self_test(run=_run):
    results = []
    with tempfile.TemporaryDirectory(prefix="fk-install-selftest-") as t:
        f = Fake(Path(t) / "base")
        rows, _ = verify(f.product, f.ref, f.env, run)
        base_bad = [r["check"] for r in rows if not r["ok"]]
        results.append({"case": "a correct install passes", "ok": not base_bad, "detail": base_bad})
        for i, (name, seed, check) in enumerate(BREAKS):
            f = Fake(Path(t) / f"case{i}")
            seed(f)
            rows, _ = verify(f.product, f.ref, f.env, run)
            failed = [r["check"] for r in rows if not r["ok"]]
            caught = any(c.startswith(check) for c in failed)
            results.append({"case": f"caught: {name}", "ok": caught, "detail": [] if caught else failed})
    return results


# ---- command line -----------------------------------------------------------------------------------------------------
def _say(rows):
    for r in rows:
        tag = "INFO" if r.get("info") else "OK  " if r["ok"] else "FAIL"
        print(f"{tag} {r['check']}: {r['line']}")
        if not r["ok"]:
            print(f"     FIX: {r['fix']}")


def main(argv=None, prog="fieldkit install"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["verify", "self-test"])
    ap.add_argument("product", nargs="?")
    ap.add_argument("--tag", help="the release to compare with (default: the parity pin)")
    ap.add_argument("--ref", help="a reference JSON (from --save-ref) instead of downloading the release")
    ap.add_argument("--save-ref", help="write the reference read from the release to this file")
    ap.add_argument("--proof", help="write the resolved icon paths and sha256 per size to this JSON file")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "self-test":
        res = self_test()
        ok = all(r["ok"] for r in res)
        if a.json:
            print(json.dumps({"ok": ok, "cases": res}, indent=1))
        else:
            for r in res:
                print(f"{'OK  ' if r['ok'] else 'FAIL'} {r['case']}" + ("" if r["ok"] else f" (failed checks: {r['detail']})"))
            print(f"{sum(r['ok'] for r in res)}/{len(res)} cases\nNEXT: " + ("none" if ok else "a check does not catch its break: fix it in fieldkit/method/installverify.py"))
        return 0 if ok else 3
    if a.product not in PRODUCTS:
        print(f"REFUSED: PRODUCT is one of {', '.join(PRODUCTS)}")
        return 2
    if not sys.platform.startswith("linux"):
        out = {"status": "not-this-platform", "ok": None, "platform": sys.platform}
        print(json.dumps(out) if a.json else f"not-this-platform: install verify reads Linux only; nothing was verified on {sys.platform}")
        return 0
    try:
        if a.ref:
            ref = json.loads(Path(a.ref).read_text(encoding="utf-8"))
        else:
            from .. import parity
            p = parity.pin()[a.product]
            tag = a.tag or p["version"]
            with tempfile.TemporaryDirectory(prefix="fk-install-ref-") as t:
                ref = reference_from_release(a.product, tag, p["repo"], Path(t) / "r")
    except (RuntimeError, OSError, KeyError, ValueError) as ex:
        print(f"REFUSED: no reference: {ex}")
        return 2
    if a.save_ref:
        Path(a.save_ref).write_text(json.dumps(ref, indent=1), encoding="utf-8")
    e = environment()
    rows, resolved = verify(a.product, ref, e, session_start=shell_start())
    bad = [r for r in rows if not r["ok"]]
    if a.proof:
        Path(a.proof).write_text(json.dumps({"product": a.product, "tag": ref.get("tag"), "release_sha256": ref["icons"],
                                             "resolved": {k: {"path": v[0], "sha256": v[1]} for k, v in resolved.items()}}, indent=1),
                                 encoding="utf-8")
    if a.json:
        print(json.dumps({"ok": not bad, "rows": rows, "resolved": resolved}, indent=1))
    else:
        _say(rows)
        print(f"{len(rows) - len(bad)}/{len(rows)} checks pass\nNEXT: " + ("none" if not bad else bad[0]["fix"]))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
