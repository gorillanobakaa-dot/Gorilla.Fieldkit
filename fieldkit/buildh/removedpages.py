"""The pages the owner's register removes are gone from the browser itself, not only from the source.

    fieldkit build-harness removed-pages <task> [--install-dir D]

Born 2026-10-09 (D-157-40). The register (decisions/ABOUT-PAGES.yaml) and the about-pages run prove a removed page
is not REGISTERED; this proves what a person meets when they type its address: every removed about: page lands on
the network error page with code malformedURI ("Hmm. That address doesn't look right.", the page any unknown about:
address gets), and every removed page's own chrome:// file (CHROME_FILES; its files are not packaged since D-157-40
round 2) does not open. Probe removed-pages, in a throwaway copy, headless, fresh profile; nothing leaves the machine.
Rows join build-verify and post-install ("about").
"""
import json
import re
import tempfile
from pathlib import Path

PROBE = Path(__file__).parent / "probes" / "removed-pages.js"
# the files of removed pages, by their chrome:// address (D-157-40 round 2: not packaged)
CHROME_FILES = [
    "chrome://browser/content/asrouter/asrouter-admin.html",
    "chrome://browser/content/messagepreview/messagepreview.html",
    "chrome://global/content/usercharacteristics/usercharacteristics.html",
    "chrome://global/content/aboutRestricted/aboutRestricted.html",
    "chrome://browser/content/blockedSite.xhtml",
]


def removed(register):
    """Pages the register marks remove -> sorted names."""
    import yaml
    pages = (yaml.safe_load(Path(register).read_text(encoding="utf-8")) or {}).get("pages") or []
    return sorted(e["name"] for e in pages if e.get("verdict") == "remove")


def probe_file(addresses):
    body = PROBE.read_text(encoding="utf-8")
    assert body.count("const ADDRESSES = [];") == 1
    out = Path(tempfile.mkdtemp(prefix="gprobe_gone_")) / "removed-pages.js"
    out.write_text(body.replace("const ADDRESSES = [];", "const ADDRESSES = " + json.dumps(addresses) + ";"),
                   encoding="utf-8")
    return out


def parse(lines):
    """Probe lines -> ({address: {"landed", "text"}}, done count or None)."""
    got, done = {}, None
    for l in lines:
        f = l.strip().split("|")
        if f[0] == "GONE" and len(f) >= 3:
            got[f[1]] = {"landed": f[2], "text": f[3] if len(f) > 3 else ""}
        elif f[0] == "GONE-DONE" and len(f) >= 2 and f[1].isdigit():
            done = int(f[1])
    return got, done


def gone(landed):
    return "e=malformedURI" in landed and landed.startswith("about:neterror")


def file_gone(landed):
    """A chrome:// file that is not packaged: the load fails (error page or an empty about:blank), never the file."""
    return landed.startswith("about:neterror") or landed in ("about:blank", "")


def judge(names, files, got, done):
    """-> rows."""
    rows = []
    want = len(names) + len(files)
    if done != want:
        return [{"check": "removed pages: the probe opened every address", "ok": False,
                 "evidence": f"{done} of {want} reported"}]
    open_pages = [f"about:{n} -> {got.get('about:' + n, {}).get('landed', '(nothing)')[:70]}" for n in names
                  if not gone(got.get("about:" + n, {}).get("landed", ""))]
    rows.append({"check": "removed pages: every removed about: page lands on 'address not valid' (D-157-40)",
                 "ok": not open_pages,
                 "evidence": "; ".join(open_pages[:6]) if open_pages else
                             f"{len(names)} removed page(s), each on about:neterror malformedURI"})
    open_files = [f"{f} -> {got.get(f, {}).get('landed', '(nothing)')[:60]}" for f in files
                  if not file_gone(got.get(f, {}).get("landed", ""))]
    rows.append({"check": "removed pages: their own chrome:// files do not open (D-157-40)", "ok": not open_files,
                 "evidence": "; ".join(open_files[:5]) if open_files else f"{len(files)} file address(es), none opens"})
    return rows


def rows(install_dir, register, say=print, timeout=300, **change):
    """Probe a copy of `install_dir` -> rows; fails closed without a register."""
    if not register or not Path(register).is_file():
        return [{"check": "removed pages: the register is there", "ok": False, "evidence": f"no register at {register}"}]
    from . import probe as pb
    names = removed(register)
    addresses = ["about:" + n for n in names] + CHROME_FILES
    r = pb.run(install_dir, str(probe_file(addresses)), wait=10, timeout=timeout, say=say, **change)
    got, done = parse(r["lines"])
    return judge(names, CHROME_FILES, got, done)
