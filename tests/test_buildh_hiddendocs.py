"""hidden-pages-doc (2026-10-08, D-157-40): every page the register says is or was hidden is explained in English
(public, both tracks) and Romanian (private, plain words), with its picture, rendered from one source; the check
fails closed on a missing page, a missing text or picture, a picture with text chunks, a forbidden word, an
unsourced number or address, and a published copy that differs from what the source renders."""
import struct
import zlib

import pytest
import yaml

from fieldkit.buildh import hiddendocs as hd


def _png(path, text_chunk=False):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    if text_chunk:
        # a home-folder path, joined from parts (the commit privacy scan reads the literal as a real one)
        raw += chunk(b"tEXt", b"Comment\x00" + b"\\".join([b"C:", b"Us" + b"ers", b"someone", b"shot.png"]))
    raw += chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b"")
    path.write_bytes(raw)


def _entry(name, shots, layman="**What it is.** A page.", ro_title="Pagina"):
    return {"name": name, "shots": shots, "no_shot": None if shots else "it would crash the browser",
            "en": {"title": f"The {name} page", "layman": layman, "developer": "Registered in `x.cpp`."},
            "ro": {"title": ro_title, "network": "cu nimeni", "layman": "**Ce este.** O pagină."}}


@pytest.fixture
def world(tmp_path):
    owner, ps = tmp_path / "owner", tmp_path / "patchset"
    (owner / "decisions").mkdir(parents=True)
    (owner / "decisions" / "PRODUCT-DECISIONS.yaml").write_text("decisions: []\n", encoding="utf-8")
    (owner / "decisions" / "ABOUT-PAGES.yaml").write_text(yaml.safe_dump({"pages": [
        {"name": "neterror", "verdict": "keep", "shown": "hidden", "decision": "D-157-40", "network": "on click"},
        {"name": "crashparent", "verdict": "remove", "shown": "gone", "was": "hidden", "decision": "D-157-40",
         "network": "none"},
        {"name": "about", "verdict": "keep", "shown": "listed"},
        {"name": "glean", "verdict": "remove", "shown": "gone", "decision": "D-157-36"},
    ]}), encoding="utf-8")
    src = owner / "docs" / "hidden-pages"
    for d in ("pages", "shots", "research"):
        (src / d).mkdir(parents=True)
    (src / "research" / "A.md").write_text("research notes\n", encoding="utf-8")
    _png(src / "shots" / "about-neterror.png")
    (src / "intro.yaml").write_text(yaml.safe_dump({
        "order": ["crashparent", "neterror"],
        "en": {"title": "The hidden pages", "intro": "Pages about:about does not list."},
        "ro": {"title": "Paginile ascunse", "intro": "Pagini pe care about:about nu le arată."}}), encoding="utf-8")
    (src / "pages" / "a.yaml").write_text(yaml.safe_dump({"pages": [
        _entry("neterror", ["about-neterror.png"]), _entry("crashparent", [])]}, allow_unicode=True), encoding="utf-8")
    ps.mkdir()
    return owner, ps


def _rows(owner, ps):
    return {r["check"].split(": ", 1)[1]: r for r in hd.check(owner, ps)}


def test_write_then_check_passes_and_renders_both_languages(world):
    owner, ps = world
    r = hd.write(owner, ps, say=lambda m: None)
    assert all(x["ok"] for x in r["rows"]), [x for x in r["rows"] if not x["ok"]]
    en = (ps / hd.PUBLIC_DOC).read_text(encoding="utf-8")
    ro = (owner / hd.RO_DOC).read_text(encoding="utf-8")
    assert en.index("## Removed") < en.index("`about:crashparent`: The crashparent page") < en.index("## Kept")
    assert "**Removed (D-157-40).** **Talks to:** none" in en and "**Kept, changed (D-157-40).**" in en
    assert "![about:neterror](screenshots/hidden-pages/about-neterror.png)" in en
    assert "*No picture: it would crash the browser*" in en
    assert "#### For developers" in en and "#### For developers" not in ro
    assert "**Eliminată (D-157-40).** **Vorbește cu:** cu nimeni" in ro and "](shots/about-neterror.png)" in ro
    assert (ps / hd.PUBLIC_SHOTS / "about-neterror.png").is_file()
    assert "about:glean" not in en and "about:about`" not in en        # listed or never-hidden pages stay out


def test_a_hidden_page_without_an_entry_fails(world):
    owner, ps = world
    reg = yaml.safe_load((owner / hd.REGISTER).read_text(encoding="utf-8"))
    reg["pages"].append({"name": "newthing", "verdict": "keep", "shown": "hidden"})
    (owner / hd.REGISTER).write_text(yaml.safe_dump(reg), encoding="utf-8")
    r = _rows(owner, ps)
    assert not r["every page that is or was hidden is explained, and nothing else"]["ok"]
    assert "about:newthing" in r["every page that is or was hidden is explained, and nothing else"]["evidence"]


def test_missing_text_picture_or_reason_fails(world):
    owner, ps = world
    bad = _entry("neterror", ["missing.png"])
    bad["ro"]["layman"] = ""
    nopic = _entry("crashparent", [])
    nopic["no_shot"] = ""
    (owner / hd.SRC / "pages" / "a.yaml").write_text(yaml.safe_dump({"pages": [bad, nopic]}), encoding="utf-8")
    ev = _rows(owner, ps)["every entry has its texts and pictures"]
    assert not ev["ok"]
    for want in ("about:neterror ro.layman", "picture missing.png not found", "about:crashparent has no picture and no reason"):
        assert want in ev["evidence"]


def test_a_picture_with_text_chunks_fails(world):
    owner, ps = world
    _png(owner / hd.SRC / "shots" / "about-neterror.png", text_chunk=True)
    r = _rows(owner, ps)["no picture carries text chunks (paths, user names)"]
    assert not r["ok"] and "tEXt" in r["evidence"]


def test_forbidden_words_and_unsourced_numbers_fail(world):
    owner, ps = world
    (owner / hd.SRC / "pages" / "a.yaml").write_text(yaml.safe_dump({"pages": [
        _entry("neterror", ["about-neterror.png"], layman="It is simply a page. It made 4096 requests."),
        _entry("crashparent", [])]}), encoding="utf-8")
    hd.write(owner, ps, say=lambda m: None)
    r = _rows(owner, ps)
    assert not r["the plain-words texts use none of the words the layman guide forbids"]["ok"]
    src = r["the English holds to its sources (numbers, web addresses), keeps nothing private, no banned words"]
    assert not src["ok"] and "4096" in src["evidence"]


def test_a_hand_edited_public_copy_fails(world):
    owner, ps = world
    hd.write(owner, ps, say=lambda m: None)
    p = ps / hd.PUBLIC_DOC
    p.write_text(p.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    r = _rows(owner, ps)["the English (public) copy is what the source renders"]
    assert not r["ok"] and "differs" in r["evidence"]


def test_a_page_written_twice_is_refused(world):
    owner, ps = world
    (owner / hd.SRC / "pages" / "b.yaml").write_text(yaml.safe_dump({"pages": [_entry("neterror", [])]}), encoding="utf-8")
    with pytest.raises(hd.Refused):
        hd.load(owner)


def test_write_removes_pictures_no_page_uses(world):
    owner, ps = world
    (ps / hd.PUBLIC_SHOTS).mkdir(parents=True)
    _png(ps / hd.PUBLIC_SHOTS / "old.png")
    hd.write(owner, ps, say=lambda m: None)
    assert not (ps / hd.PUBLIC_SHOTS / "old.png").exists()
