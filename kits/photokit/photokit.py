"""photokit - review, de-duplicate and sort a folder of photos (offline, Pillow only).

Built 2026-10-12 for comparing a tenant's own photos with a letting agent's inventory
report (InventoryBase). The procedure that uses it is the fieldkit-property-inventory skill.

  photokit.py doctor [--install]
      Checks Python and Pillow (installs Pillow from PyPI with --install) and reports whether
      Chrome is present for the upload step. Run it first on any new machine.
  photokit.py sheet SRC OUT [--cols 4] [--per 12] [--size 480] [--strip PREFIX]
      Labelled contact sheets (thumbnail grids) so a model or a person can look at
      many photos at once. Label = file name without extension (and without PREFIX).
  photokit.py dupes SRC [--threshold 6] [--window 0] [--json]
      Near-duplicate groups by perceptual difference hash (dHash, 64 bit). Report
      only: it never moves or deletes. --window N only compares photos whose sorted
      positions are at most N apart (burst shots); 0 compares every pair.
      The hash only proposes: on plain walls unrelated photos hash alike, and the
      same defect shot from two angles hashes apart. Decide by looking (compare).
  photokit.py compare SRC OUT A:B [A:B ...] [--prefix P]
      Side-by-side sheets of chosen pairs (with their hash distance) for a visual call.
  photokit.py check SRC MAPPING
      Every photo in SRC must appear exactly once in MAPPING (or its 'skip' line).
      Exit 1 on missing, unknown or repeated names.
  photokit.py sort SRC MAPPING OUT [--edge 2000] [--quality 85]
      Copies into OUT/<ref>/<ref>_<stem>.jpg, EXIF-rotated and shrunk for upload.
      Originals are never touched. Lines whose ref is 'skip' are left out.
  photokit.py fetch LIST OUT
      Downloads 'label|url' lines to OUT/<label>.jpg (the report's own photos).
  photokit.py serve ROOT [--port 8765] [--idle 600]
      Read-only GET server on 127.0.0.1 for ROOT only, with CORS, so a page in the
      in-app browser can fetch the sorted photos into its own upload input. Stops by
      itself after --idle seconds without a request; stop it earlier by PID.

MAPPING format, one line per report item, '#' starts a comment:
  <ref> <stem> <stem> ...        e.g.  5.12 IMG_20261009_192808 IMG_20261009_192824
  skip <stem> ...                photos deliberately not uploaded (duplicates)
A stem may be given without a common prefix if --prefix is passed to check/sort.

Exit codes: 0 ok, 1 findings (check: mapping faults), 2 usage error.
"""
import argparse
import json
import pathlib
import sys
import urllib.request

IMAGE_EXT = (".jpg", ".jpeg", ".png")


def _images(src):
    return sorted(p for p in pathlib.Path(src).iterdir() if p.suffix.lower() in IMAGE_EXT)


def _open(p):
    from PIL import Image, ImageOps
    return ImageOps.exif_transpose(Image.open(p)).convert("RGB")


# -- sheet -----------------------------------------------------------------------
def cmd_sheet(a):
    from PIL import Image, ImageDraw, ImageFont
    files = _images(a.src)
    if not files:
        print("no images", file=sys.stderr)
        return 1
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    s = a.size
    for start in range(0, len(files), a.per):
        chunk = files[start:start + a.per]
        rows = (len(chunk) + a.cols - 1) // a.cols
        sheet = Image.new("RGB", (a.cols * s, rows * (s + 36)), "white")
        d = ImageDraw.Draw(sheet)
        for i, p in enumerate(chunk):
            im = _open(p)
            im.thumbnail((s, s))
            x, y = (i % a.cols) * s, (i // a.cols) * (s + 36)
            sheet.paste(im, (x + (s - im.width) // 2, y + 36 + (s - im.height) // 2))
            label = p.stem[len(a.strip):] if a.strip and p.stem.startswith(a.strip) else p.stem
            d.text((x + 4, y + 2), label, fill="black", font=font)
        name = out / f"sheet_{start // a.per + 1:02d}.jpg"
        sheet.save(name, quality=80)
        print(name)
    return 0


# -- dupes -----------------------------------------------------------------------
def dhash(p, size=8):
    from PIL import Image
    im = _open(p).convert("L").resize((size + 1, size), Image.LANCZOS)
    px = im.tobytes()
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (px[r * (size + 1) + c] > px[r * (size + 1) + c + 1])
    return bits


def find_dupes(files, threshold, window=0):
    hashes = [dhash(p) for p in files]
    parent = list(range(len(files)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs = []
    for i in range(len(files)):
        last = len(files) if window <= 0 else min(len(files), i + window + 1)
        for j in range(i + 1, last):
            dist = bin(hashes[i] ^ hashes[j]).count("1")
            if dist <= threshold:
                pairs.append((files[i].name, files[j].name, dist))
                parent[root(j)] = root(i)
    groups = {}
    for i in range(len(files)):
        groups.setdefault(root(i), []).append(files[i].name)
    return [g for g in groups.values() if len(g) > 1], pairs


def pair_sheets(src, pairs, out, size=420, per=6):
    """Side-by-side sheets of candidate pairs; the decision is made by looking, not by the hash."""
    from PIL import Image, ImageDraw, ImageFont
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    src = pathlib.Path(src)
    names = []
    for start in range(0, len(pairs), per):
        chunk = pairs[start:start + per]
        sheet = Image.new("RGB", (2 * size, len(chunk) * (size + 32)), "white")
        d = ImageDraw.Draw(sheet)
        for r, (x, y, dist) in enumerate(chunk):
            for c, name in enumerate((x, y)):
                im = _open(src / name)
                im.thumbnail((size, size))
                sheet.paste(im, (c * size + (size - im.width) // 2, r * (size + 32) + 32))
                d.text((c * size + 4, r * (size + 32) + 4), f"{pathlib.Path(name).stem}  d={dist}", fill="black", font=font)
        name = out / f"pairs_{start // per + 1:02d}.jpg"
        sheet.save(name, quality=80)
        names.append(name)
    return names


def cmd_compare(a):
    src = pathlib.Path(a.src)
    by_stem = {p.stem: p.name for p in _images(src)}
    pairs = []
    for spec in a.pairs:
        x, y = (a.prefix + s if a.prefix + s in by_stem else s for s in spec.split(":"))
        if x not in by_stem or y not in by_stem:
            print(f"unknown photo in {spec}", file=sys.stderr)
            return 2
        dist = bin(dhash(src / by_stem[x]) ^ dhash(src / by_stem[y])).count("1")
        pairs.append((by_stem[x], by_stem[y], dist))
    for n in pair_sheets(src, pairs, a.out):
        print(n)
    return 0


def cmd_dupes(a):
    files = _images(a.src)
    groups, pairs = find_dupes(files, a.threshold, a.window)
    if a.sheet:
        for n in pair_sheets(a.src, pairs, a.sheet):
            print(n)
    if a.json:
        print(json.dumps({"groups": groups, "pairs": pairs}, indent=1))
    else:
        for g in groups:
            print("  ".join(g))
        print(f"{len(groups)} group(s) of near-duplicates among {len(files)} photos (threshold {a.threshold})")
    return 1 if groups else 0


# -- mapping ---------------------------------------------------------------------
def read_mapping(path, prefix=""):
    entries = []
    for n, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].split()
        if not line:
            continue
        ref, stems = line[0], line[1:]
        entries += [(ref, s if s.startswith(prefix) else prefix + s, n) for s in stems]
    return entries


def mapping_faults(src, mapping, prefix=""):
    have = {p.stem for p in _images(src)}
    entries = read_mapping(mapping, prefix)
    seen, faults = {}, []
    for ref, stem, n in entries:
        if stem not in have:
            faults.append(f"line {n}: {stem} is not in {src}")
        if stem in seen:
            faults.append(f"line {n}: {stem} already mapped on line {seen[stem]}")
        seen.setdefault(stem, n)
    faults += [f"{stem} is not mapped" for stem in sorted(have - set(seen))]
    return faults, entries


def cmd_check(a):
    faults, entries = mapping_faults(a.src, a.mapping, a.prefix)
    for f in faults:
        print(f)
    refs = {}
    for ref, _, _ in entries:
        refs[ref] = refs.get(ref, 0) + 1
    print("  ".join(f"{r}:{c}" for r, c in refs.items()))
    print("OK" if not faults else f"{len(faults)} fault(s)")
    return 1 if faults else 0


def cmd_sort(a):
    faults, entries = mapping_faults(a.src, a.mapping, a.prefix)
    if faults:
        for f in faults:
            print(f, file=sys.stderr)
        print("mapping has faults: run 'check' and fix them first", file=sys.stderr)
        return 1
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    by_stem = {p.stem: p for p in _images(src)}
    n = 0
    for ref, stem, _ in entries:
        if ref == "skip":
            continue
        d = out / ref
        d.mkdir(parents=True, exist_ok=True)
        im = _open(by_stem[stem])
        im.thumbnail((a.edge, a.edge))
        short = stem[len(a.prefix):] if a.prefix else stem
        im.save(d / f"{ref}_{short}.jpg", quality=a.quality)
        n += 1
    print(f"sorted {n} photo(s) into {out}")
    return 0


# -- fetch -----------------------------------------------------------------------
def cmd_fetch(a):
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in pathlib.Path(a.list).read_text(encoding="utf-8").splitlines():
        if "|" not in line:
            continue
        label, url = (x.strip() for x in line.split("|", 1))
        if not url.startswith("https://"):
            print(f"refused (not https): {url}", file=sys.stderr)
            continue
        with urllib.request.urlopen(url, timeout=60) as r:
            (out / f"{label}.jpg").write_bytes(r.read())
        n += 1
    print(f"fetched {n}")
    return 0


# -- doctor ----------------------------------------------------------------------
def doctor_checks():
    """(name, ok, detail, how) for what PhotoKit and the inventory procedure need here."""
    import importlib.util
    import os
    import platform
    import shutil
    out = [("python>=3.9", sys.version_info >= (3, 9), platform.python_version(), "install Python 3.9+"),
           ("pillow", importlib.util.find_spec("PIL") is not None, "", f"{sys.executable} -m pip install pillow")]
    chrome = None
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            p = pathlib.Path(base, "Google", "Chrome", "Application", "chrome.exe")
            if base and p.is_file():
                chrome = str(p)
                break
    else:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    out.append(("chrome (for uploads, optional)", bool(chrome), chrome or "",
                "winget install --id Google.Chrome -e  (or any Chromium browser); then the OWNER installs the "
                "Claude in Chrome extension and signs in - an agent cannot"))
    return out


def cmd_doctor(a):
    checks = doctor_checks()
    for name, ok, detail, how in checks:
        print(f"{'ok ' if ok else 'MISSING'}  {name:<32} {detail}" + ("" if ok else f"\n         -> {how}"))
    hard = [c for c in checks if not c[1] and "optional" not in c[0]]
    if hard and a.install and any(c[0] == "pillow" for c in hard):
        print("installing: pillow (from PyPI)")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "pillow"], check=False)
        hard = [c for c in doctor_checks() if not c[1] and "optional" not in c[0]]
    print("Claude in Chrome connection is checked by the agent: list_connected_browsers.")
    return 1 if hard else 0


# -- serve -----------------------------------------------------------------------
def make_server(root, port=0, idle=600):
    """Read-only GET server on 127.0.0.1 for ONE folder, so a web page open in the in-app
    browser can fetch() photos into its own file input (the in-app browser cannot attach
    local files). CORS + Private-Network-Access headers allow exactly that. It lists nothing,
    refuses paths outside ROOT and stops after IDLE seconds without a request."""
    import functools
    import http.server
    import threading
    import time
    root = pathlib.Path(root).resolve()
    state = {"last": time.time()}

    class H(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_OPTIONS(self):
            state["last"] = time.time()
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            state["last"] = time.time()
            p = pathlib.Path(self.translate_path(self.path)).resolve()
            if root not in p.parents or not p.is_file():
                self.send_error(404)
                return
            super().do_GET()

        do_HEAD = do_GET

        def log_message(self, fmt, *args):
            print(fmt % args, file=sys.stderr)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", int(port)), functools.partial(H, directory=str(root)))

    def watchdog():
        while True:
            time.sleep(5)
            if time.time() - state["last"] > idle:
                srv.shutdown()
                return
    threading.Thread(target=watchdog, daemon=True).start()
    return srv


def cmd_serve(a):
    srv = make_server(a.root, a.port, a.idle)
    print(f"serving {pathlib.Path(a.root).resolve()} on http://127.0.0.1:{srv.server_address[1]}/ "
          f"(read-only, stops after {a.idle}s idle)", flush=True)
    srv.serve_forever()
    print("stopped (idle)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor")
    p.add_argument("--install", action="store_true", help="pip install what is missing")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("sheet")
    p.add_argument("src")
    p.add_argument("out")
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--per", type=int, default=12)
    p.add_argument("--size", type=int, default=480)
    p.add_argument("--strip", default="")
    p.set_defaults(fn=cmd_sheet)
    p = sub.add_parser("dupes")
    p.add_argument("src")
    p.add_argument("--threshold", type=int, default=6)
    p.add_argument("--window", type=int, default=0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--sheet", default="", help="also draw side-by-side sheets of each candidate pair here")
    p.set_defaults(fn=cmd_dupes)
    p = sub.add_parser("compare")
    p.add_argument("src")
    p.add_argument("out")
    p.add_argument("pairs", nargs="+", help="STEM_A:STEM_B")
    p.add_argument("--prefix", default="")
    p.set_defaults(fn=cmd_compare)
    for name, fn in (("check", cmd_check), ("sort", cmd_sort)):
        p = sub.add_parser(name)
        p.add_argument("src")
        p.add_argument("mapping")
        if name == "sort":
            p.add_argument("out")
            p.add_argument("--edge", type=int, default=2000)
            p.add_argument("--quality", type=int, default=85)
        p.add_argument("--prefix", default="")
        p.set_defaults(fn=fn)
    p = sub.add_parser("fetch")
    p.add_argument("list")
    p.add_argument("out")
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("serve")
    p.add_argument("root")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--idle", type=int, default=600)
    p.set_defaults(fn=cmd_serve)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
