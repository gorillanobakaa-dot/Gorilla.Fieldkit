"""fieldkit/visual, layer 1 (static): every rule with fakes - fake trees, fake rasters, fake CSS, fake branding.

The 2026-10-02 failure this module exists for: a soft 500 px About logo painted into a 500 px box. Both halves of
that (a soft raster, a CSS rule without 2x headroom) must FAIL, and missing evidence must never PASS.
"""
import random
import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

from fieldkit.visual import allow as allowmod, chrome, css, leftovers, rasters, static


# ------------------------------------------------------------------------------------------- fake pictures
def sharp_master(n=1024, seed=1, palette=((90, 60, 40), (200, 170, 120), (30, 30, 30), (240, 240, 230))):
    """A detailed picture with real edges at every scale, on a transparent background."""
    rnd = random.Random(seed)
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((n * 0.05, n * 0.05, n * 0.95, n * 0.95), fill=palette[0] + (255,))
    for _ in range(900):
        x, y = rnd.uniform(0.15, 0.85) * n, rnd.uniform(0.15, 0.85) * n
        s = rnd.uniform(0.005, 0.04) * n
        d.rectangle((x, y, x + s, y + s * rnd.uniform(0.3, 2)), fill=palette[rnd.randrange(1, len(palette))] + (255,))
    for _ in range(300):
        x, y = rnd.uniform(0.15, 0.85) * n, rnd.uniform(0.15, 0.85) * n
        d.line((x, y, x + rnd.uniform(-60, 60), y + rnd.uniform(-60, 60)), fill=palette[2] + (255,), width=2)
    return im


def soft(im, size):
    """What the About logo was: a small copy blown back up."""
    return im.resize((size // 5, size // 5), Image.LANCZOS).resize((size, size), Image.LANCZOS)


def true_down(im, size):
    return im.resize((size, size), Image.LANCZOS)


@pytest.fixture(scope="module")
def master():
    return sharp_master(1400)


# ------------------------------------------------------------------------------------------- ICON-002
def test_a_true_downsample_of_the_master_passes(master):
    v, ev, nums = rasters.provenance(true_down(master, 256), master)
    assert v == "PASS", ev
    assert nums["separation"] > rasters.SEPARATION


def test_a_soft_raster_fails_like_the_about_logo_did(master):
    v, ev, nums = rasters.provenance(soft(master, 500), master)
    assert v == "FAIL", ev
    assert nums["got"] < rasters.PROVENANCE_RATIO * nums["want"]


def test_a_master_that_does_not_out_resolve_the_raster_is_unverifiable_never_pass(master):
    small = true_down(master, 200)
    v, ev, _ = rasters.provenance(true_down(master, 256), small)
    assert v == "UNVERIFIABLE" and "out-resolve" in ev


def test_a_soft_master_fails_the_self_control_and_is_unverifiable(master):
    # doctrine section 3: icon1024.png was big but no sharper than a small picture, and "validated" a soft asset
    tiny = master.resize((100, 100), Image.LANCZOS)
    soft_master, soft_raster = tiny.resize((1024, 1024), Image.LANCZOS), tiny.resize((512, 512), Image.LANCZOS)
    v, ev, _ = rasters.provenance(soft_raster, soft_master)
    assert v == "UNVERIFIABLE" and "self-control" in ev


def test_different_artwork_is_unverifiable_without_its_own_master(master):
    # installer art: an opaque banner, nothing like the round gorilla badge
    other = Image.new("RGBA", (300, 120), (20, 60, 220, 255))
    d = ImageDraw.Draw(other)
    for x in range(0, 300, 12):
        d.rectangle((x, 0, x + 5, 120), fill=(255, 230, 0, 255))
    v, ev, _ = rasters.provenance(other, master)
    assert v == "UNVERIFIABLE" and "different artwork" in ev


def test_a_tinted_variant_is_judged_on_equalised_detail_and_its_outline(master):
    t = true_down(master, 256)
    blue = Image.new("RGBA", t.size, (40, 80, 230, 255))
    blue.putalpha(t.split()[3])
    tinted = Image.blend(t, blue, 0.4)              # the private-browsing treatment: same shape, recoloured
    v, ev, nums = rasters.provenance(tinted, master)
    assert nums["channel"] == "equalised" and v == "PASS", ev
    v2, ev2, n2 = rasters.provenance(soft(tinted, 256), master)            # tinted AND soft
    assert n2["channel"] == "equalised" and v2 == "FAIL", ev2
    r, g, b, a = tinted.split()
    blurred = Image.merge("RGBA", (r, g, b, a.filter(ImageFilter.GaussianBlur(4))))   # crisp inside, soft outline
    v3, ev3, _ = rasters.provenance(blurred, master)
    assert v3 == "FAIL", ev3


# ------------------------------------------------------------------------------------------- ICO, slots
def test_an_ico_without_the_windows_ladder_fails_and_the_full_ladder_passes(tmp_path, master):
    part = tmp_path / "part.ico"
    master.save(part, sizes=[(16, 16), (32, 32), (48, 48)])
    v, ev, _ = rasters.ico_ladder([s for s, _ in rasters.ico_frames(part)])
    assert v == "FAIL" and "96" in ev
    full = tmp_path / "full.ico"
    master.save(full, sizes=[(s, s) for s in rasters.ICO_REQUIRED])
    v, ev, missing = rasters.ico_ladder([s for s, _ in rasters.ico_frames(full)])
    assert v == "PASS" and 56 in missing          # iconkit's dense sizes are reported, not failed


def test_iconkit_and_this_module_agree_on_the_dense_ladder():
    p = Path.home() / "Documents" / "Scripts" / "IconKit" / "iconkit.py"
    if not p.is_file():
        pytest.skip("IconKit is not on this machine")
    m = re.search(r"DEFAULT_SIZES = \(([^)]*)\)", p.read_text(encoding="utf-8"))
    assert tuple(int(x) for x in m.group(1).split(",") if x.strip()) == rasters.ICO_RECOMMENDED


def test_a_size_slot_must_hold_its_size_and_one_picture_must_not_fill_several_sizes(master):
    big = true_down(master, 128)
    rows = rasters.slot_rows([("c/icon16.png", big), ("c/icon128.png", big), ("default32.png", true_down(master, 32)),
                              ("icon32.png", true_down(master, 32))])
    verdict = {r[0]: r[1] for r in rows}
    assert verdict["c/icon16.png"] == "FAIL" and verdict["c/icon128.png"] == "PASS"
    assert verdict["c/icon16.png#distinct"] == "FAIL" and verdict["c/icon128.png#distinct"] == "FAIL"
    # two NAMES for one size may be the same picture (doctrine 6j)
    assert "default32.png#distinct" not in verdict and verdict["icon32.png"] == "PASS"


def test_nominal_sizes_ignore_msix_scales_and_at2x():
    assert rasters.nominal_size("icon48.png") == 48 and rasters.nominal_size("default256.png") == 256
    assert rasters.nominal_size("about-logo@2x.png") is None  # privacy-scan: allow (a file name or a fake address, not an email)
    assert rasters.nominal_size("LargeTile.scale-200.png") is None and rasters.nominal_size("VisualElements_150.png") is None


# ------------------------------------------------------------------------------------------- the fake tree
def git(tree, *args):
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True,
                   capture_output=True)


def fake_tree(tmp_path, master, logo=None, css_text=None):
    """A Firefox-shaped tree: Mozilla's nightly branding, Gorilla's branding, a jar.mn, one chrome CSS file."""
    tree = tmp_path / "tree"
    nb = tree / "browser" / "branding" / "nightly" / "content"
    nb.mkdir(parents=True)
    (nb / "about-wordmark.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="M10 10 L 20 20 L 30 10 '
                                           'L 40 20 L 50 10 L 60 20 L 70 10 Z" fill="#fff"/></svg>', encoding="utf-8")
    nb.parent.joinpath("jar.mn").write_text("browser.jar:\n% content branding %content/branding/\n"
                                            "  content/branding/about-wordmark.svg (content/about-wordmark.svg)\n", encoding="utf-8")
    gb = tree / "browser" / "branding" / "gorilla"
    (gb / "content").mkdir(parents=True)
    (gb / "locales" / "en-US").mkdir(parents=True)
    (gb / "locales" / "en-US" / "brand.ftl").write_text("# This Source Code Form is subject to the Mozilla Public License\n"
                                                         "-brand-short-name = Gorilla Unleashed\n", encoding="utf-8")
    (logo or true_down(master, 1000)).save(gb / "content" / "about-logo.png")
    true_down(master, 1200 if logo is None else 1000).save(gb / "content" / "about-logo@2x.png")  # privacy-scan: allow (a file name or a fake address, not an email)
    for s in (16, 32, 48, 64, 128, 256):
        true_down(master, s).save(gb / f"default{s}.png")
    true_down(master, 256).save(gb / "firefox.ico", sizes=[(s, s) for s in rasters.ICO_REQUIRED])
    (gb / "jar.mn").write_text(
        "#ifdef XP_MACOSX\nbrowser.jar:\n% content branding %content/branding/wrong/\n#endif\n"
        "browser.jar:\n% content branding %content/branding/ contentaccessible=yes\n"
        "  content/branding/about-logo.png  (content/about-logo.png)\n"
        "  content/branding/about-logo@2x.png  (content/about-logo@2x.png)\n"  # privacy-scan: allow (a file name or a fake address, not an email)
        "  content/branding/icon16.png  (default16.png)\n", encoding="utf-8")
    themes = tree / "browser" / "themes" / "shared"
    themes.mkdir(parents=True)
    (themes / "jar.mn").write_text("browser.jar:\n% skin browser classic/1.0 %skin/classic/browser/\n"
                                   "  skin/classic/browser/icons/ (icons/*.svg)\n", encoding="utf-8")
    (themes / "icons").mkdir()
    (themes / "icons" / "back.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>', encoding="utf-8")
    (themes / "about.css").write_text("/* upstream */\n.a { color: red; }\n", encoding="utf-8")
    tree.joinpath("browser", "base").mkdir(parents=True)
    git(tree.parent, "init", "-q", str(tree))
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "upstream")
    up = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if css_text is not None:
        (themes / "about.css").write_text(css_text, encoding="utf-8")
        git(tree, "commit", "-q", "-am", "hand edit")
    return tree, up


def allow_for(tmp_path, master_path, extra=""):
    p = tmp_path / "ALLOW.yaml"
    p.write_text("masters:\n  - files: \"browser/branding/gorilla/**\"\n    master: \"%s\"\n    why: test master\n"
                 "accept: []\n%s" % (str(master_path).replace("\\", "/"), extra), encoding="utf-8")
    return allowmod.load(p)


@pytest.fixture
def master_file(tmp_path, master):
    p = tmp_path / "master.png"
    master.save(p)
    return p


# ------------------------------------------------------------------------------------------- jar.mn
def test_jar_mn_resolves_chrome_urls_with_ifdef_and_wildcards(tmp_path, master):
    tree, _ = fake_tree(tmp_path, master)
    ix = chrome.Index(tree, "browser/branding/gorilla")
    assert ix.resolve("chrome://branding/content/icon16.png").name == "default16.png"
    assert ix.resolve("chrome://branding/content/about-logo.png").parent.name == "content"
    assert ix.resolve("chrome://browser/skin/icons/back.svg").name == "back.svg"
    assert ix.resolve("chrome://branding/content/nope.png") is None
    # Mozilla's nightly branding registers the same package and must not win
    assert ix.resolve("chrome://branding/content/about-wordmark.svg") is None


def test_include_is_inlined_relative_to_the_including_manifest(tmp_path):
    d = tmp_path / "t" / "toolkit" / "themes" / "windows"
    d.mkdir(parents=True)
    (tmp_path / "t" / "toolkit" / "themes" / "shared").mkdir()
    (tmp_path / "t" / "toolkit" / "themes" / "shared" / "x.png").write_bytes(b"png")
    (tmp_path / "t" / "toolkit" / "themes" / "shared" / "common.inc.mn").write_text(
        "toolkit.jar:\n% skin global classic/1.0 %skin/classic/global/\n  skin/classic/global/x.png (../shared/x.png)\n", encoding="utf-8")
    (d / "jar.mn").write_text("#include ../shared/common.inc.mn\n", encoding="utf-8")
    ix = chrome.Index(tmp_path / "t", manifests=[d / "jar.mn"])
    assert ix.resolve("chrome://global/skin/x.png") == (tmp_path / "t" / "toolkit" / "themes" / "shared" / "x.png").resolve()


# ------------------------------------------------------------------------------------------- ASSET-002, CSS-004
def css_items(tmp_path, master, rule, logo=None):
    tree, _ = fake_tree(tmp_path, master, logo=logo)
    p = tree / "browser" / "base" / "x.css"
    p.write_text(rule, encoding="utf-8")
    return css.check_file(p, "browser/base/x.css", chrome.Index(tree, "browser/branding/gorilla"), tree)


def test_a_raster_painted_into_a_box_its_own_size_fails_asset_002_and_names_the_unused_2x(tmp_path, master):
    items = css_items(tmp_path, master, '#leftBox { background-image: url("chrome://branding/content/about-logo.png");'
                      ' background-size: 500px 500px; width: 500px; }', logo=true_down(master, 500))
    [it] = items
    assert it["verdict"] == "FAIL" and it["rule"] == "ASSET-002" and "1.00x" in it["evidence"]
    assert "about-logo@2x.png exists" in it["evidence"]  # privacy-scan: allow (a file name or a fake address, not an email)


def test_css_that_upscales_a_raster_fails(tmp_path, master):
    [it] = css_items(tmp_path, master, '.x { background: url("chrome://branding/content/about-logo.png") no-repeat / 800px; }',
                     logo=true_down(master, 400))
    assert it["verdict"] == "FAIL" and "UPSCALES" in it["evidence"]


def test_two_x_headroom_passes_and_an_image_set_is_judged_by_its_2x_candidate(tmp_path, master):
    [it] = css_items(tmp_path, master, '.x { background-image: url("chrome://branding/content/about-logo.png"); width: 500px; }')
    assert it["verdict"] == "PASS" and "2.00x" in it["evidence"]
    items = css_items(tmp_path / "b", master, '.x { background-image: image-set(url("chrome://branding/content/about-logo.png") 1x, '
                      'url("chrome://branding/content/about-logo@2x.png") 2x); width: 500px; }', logo=true_down(master, 500))  # privacy-scan: allow (a file name or a fake address, not an email)
    assert [i["verdict"] for i in items] == ["PASS"]


def test_a_box_the_rule_does_not_state_is_unverifiable_not_pass(tmp_path, master):
    [it] = css_items(tmp_path, master, '.x { background-image: url("chrome://branding/content/about-logo.png"); background-size: contain; }')
    assert it["verdict"] == "UNVERIFIABLE"


def test_an_unclamped_raster_injection_fails_css_004_and_a_clamped_one_does_not(tmp_path, master):
    items = css_items(tmp_path, master, ':root { --icon: url("chrome://branding/content/about-logo.png"); }\n'
                      '.illustration { content: var(--icon) !important; }')
    assert any(i["rule"] == "CSS-004" and i["verdict"] == "FAIL" for i in items)
    items = css_items(tmp_path / "b", master, ':root { --icon: url("chrome://branding/content/about-logo.png"); }\n'
                      '.illustration { content: var(--icon); max-width: 200px; }')
    assert not any(i["rule"] == "CSS-004" for i in items)


def test_vector_icons_pass_missing_images_fail_and_unresolvable_urls_are_unverifiable(tmp_path, master):
    items = css_items(tmp_path, master, '.a { list-style-image: url("chrome://browser/skin/icons/back.svg"); }\n'
                      '.b { background-image: url("chrome://browser/skin/icons/gone-forever.svg"); width: 10px; }\n'
                      '.c { background-image: url("resource://somewhere/icon.png"); width: 10px; }')
    by = {i["item"].split(" ")[1]: i for i in items}
    assert by[".a"]["verdict"] == "PASS"
    assert by[".b"]["rule"] == "ASSET-MISSING" and by[".b"]["verdict"] == "FAIL"
    assert by[".c"]["verdict"] == "UNVERIFIABLE"


def test_an_svg_that_embeds_a_png_is_judged_by_the_png(tmp_path, master):
    import base64
    import io
    buf = io.BytesIO()
    true_down(master, 300).save(buf, "PNG")
    tree, _ = fake_tree(tmp_path, master)
    (tree / "browser" / "base" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image width="300" href="data:image/png;base64,%s"/></svg>'
        % base64.b64encode(buf.getvalue()).decode(), encoding="utf-8")
    p = tree / "browser" / "base" / "x.css"
    p.write_text(".x { background: url(logo.svg) fixed center / 450px; }", encoding="utf-8")
    [it] = css.check_file(p, "browser/base/x.css", chrome.Index(tree, "browser/branding/gorilla"), tree)
    assert it["verdict"] == "FAIL" and "0.67x" in it["evidence"]


# ------------------------------------------------------------------------------------------- leftovers
def test_mozilla_wordmarks_art_and_names_are_found_and_comments_are_not(tmp_path, master):
    tree, _ = fake_tree(tmp_path, master)
    gb = tree / "browser" / "branding" / "gorilla"
    src = tree / "browser" / "branding" / "nightly" / "content" / "about-wordmark.svg"
    (gb / "content" / "about-wordmark.svg").write_bytes(src.read_bytes())
    (gb / "content" / "firefox-wordmark.svg").write_text(src.read_text(encoding="utf-8").replace('fill="#fff"', 'fill="#000"'), encoding="utf-8")
    (gb / "pref").mkdir()
    (gb / "pref" / "branding.js").write_text('// was Firefox Nightly, now ours\npref("app.vendorURL", "https://nightly.mozilla.org/");\n', encoding="utf-8")
    items = leftovers.check(gb, tree)
    bad = {(i["rule"], i["item"].split(":")[0]) for i in items if i["verdict"] == "FAIL"}
    assert ("BRAND-001", "browser/branding/gorilla/content/about-wordmark.svg") in bad
    assert ("BRAND-002", "browser/branding/gorilla/content/firefox-wordmark.svg") in bad
    assert ("BRAND-003", "browser/branding/gorilla/pref/branding.js") in bad
    assert not any(i["verdict"] == "FAIL" and "brand.ftl" in i["item"] for i in items)   # MPL header comment
    nightly = [i for i in items if "branding.js" in i["item"] and i["verdict"] == "FAIL"]
    assert len(nightly) == 1 and "nightly.mozilla.org" in nightly[0]["evidence"]       # the comment is not a finding


def test_brand_text_in_a_user_visible_string_fails(tmp_path, master):
    tree, _ = fake_tree(tmp_path, master)
    gb = tree / "browser" / "branding" / "gorilla"
    (gb / "locales" / "en-US" / "brand.ftl").write_text("-brand-short-name = Nightly\n-brand-full-name = Firefox\n", encoding="utf-8")
    items = [i for i in leftovers.check(gb, tree) if "brand.ftl" in i["item"]]
    assert sorted(i["item"].rsplit(":", 1)[1] for i in items if i["verdict"] == "FAIL") == ["Firefox", "Nightly"]


# ------------------------------------------------------------------------------------------- allowlist
def test_an_accepted_exception_needs_a_reason_and_stale_entries_are_reported(tmp_path, master_file):
    a = allow_for(tmp_path, master_file, extra="")
    a["accept"] = [{"rule": "ICON-002", "item": "x/*", "why": "the maintainer said yes on 2026-10-02"},
                   {"rule": "BRAND-003", "item": "nothing/*", "why": "never matches"}]
    items = [{"layer": "static", "rule": "ICON-002", "item": "x/a.png", "verdict": "FAIL", "evidence": "soft"}]
    res = allowmod.summarise(items, a, "static")
    assert res["ok"] and items[0]["accepted"].startswith("the maintainer")
    assert res["stale_allow"] == ["BRAND-003 nothing/*"]
    p = tmp_path / "bad.yaml"
    p.write_text("accept:\n  - rule: ICON-002\n    item: x/*\n", encoding="utf-8")
    bad = allowmod.load(p)
    assert bad["problems"] and not allowmod.summarise(
        [{"layer": "static", "rule": "R", "item": "i", "verdict": "PASS", "evidence": ""}], bad, "static")["ok"]


def test_no_items_is_never_ok_and_a_missing_allowlist_is_a_problem(tmp_path):
    a = allowmod.load(tmp_path / "absent.yaml")
    assert a["problems"]
    assert not allowmod.summarise([], dict(a, problems=[]), "static")["ok"]


def test_the_committed_allowlist_loads_cleanly():
    a = allowmod.load()
    assert not a["problems"], a["problems"]
    assert a["masters"] and all(str(e.get("why")).strip() for e in a["accept"])


# ------------------------------------------------------------------------------------------- the whole layer
def test_the_static_layer_fails_a_soft_logo_and_an_upscaling_rule_and_finds_touched_css(tmp_path, master, master_file):
    rule = ('#leftBox { background-image: url("chrome://branding/content/about-logo.png"); '
            'background-size: 500px 500px; }\n')
    tree, up = fake_tree(tmp_path, master, logo=soft(master, 500), css_text=rule)
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=allow_for(tmp_path, master_file))
    assert not res["ok"]
    assert "browser/themes/shared/about.css" in res["css_files"]
    bad = {(i["rule"], i["item"].split(" ")[0]) for i in res["items"] if i["verdict"] == "FAIL"}
    assert ("ICON-002", "browser/branding/gorilla/content/about-logo.png") in bad
    assert ("ASSET-002", "browser/themes/shared/about.css") in bad
    good = {i["item"] for i in res["items"] if i["verdict"] == "PASS" and i["rule"] == "ICON-002"}
    assert "browser/branding/gorilla/default256.png" in good and "browser/branding/gorilla/firefox.ico#96" in good


def test_a_clean_tree_passes_and_the_allowlist_can_exclude_vendored_css(tmp_path, master, master_file):
    tree, up = fake_tree(tmp_path, master, css_text='.x { background-image: url("chrome://branding/content/about-logo.png"); width: 400px; }')
    a = allow_for(tmp_path, master_file)
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=a)
    assert res["ok"], [(i["rule"], i["item"], i["evidence"]) for i in res["items"] if i["verdict"] != "PASS"]
    a["css_exclude"] = [{"glob": "browser/themes/**", "why": "vendored"}]
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=a)
    assert "browser/themes/shared/about.css" not in res["css_files"]


def test_missing_evidence_fails_no_branding_dir_no_master(tmp_path, master, master_file):
    tree, up = fake_tree(tmp_path, master)
    res = static.check(tree, "browser/branding/absent", upstream_commit=up, allow=allow_for(tmp_path, master_file))
    assert not res["ok"] and res["items"][0]["verdict"] == "UNVERIFIABLE"
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=allow_for(tmp_path, tmp_path / "no-master.png"))
    assert not res["ok"]
    assert all(i["verdict"] == "UNVERIFIABLE" for i in res["items"] if i["rule"] == "ICON-002")
    unreadable = tree / "browser" / "branding" / "gorilla" / "broken.png"
    unreadable.write_bytes(b"not a png")
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=allow_for(tmp_path, master_file))
    assert any(i["item"].endswith("broken.png") and i["verdict"] == "UNVERIFIABLE" for i in res["items"])


# ------------------------------------------------------------------------------------------- 2026-10-04: more accurate
def resource_tree(tmp_path, master):
    """fake_tree plus a jar.mn that registers resource://mytheme/ the way toolkit/mozapps/extensions does."""
    tree, _ = fake_tree(tmp_path, master)
    ext = tree / "toolkit" / "mozapps" / "extensions"
    (ext / "mytheme").mkdir(parents=True)
    (ext / "mytheme" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>',
                                             encoding="utf-8")
    true_down(master, 40).save(ext / "mytheme" / "small.png")
    (ext / "jar.mn").write_text("toolkit.jar:\n% resource mytheme %content/mozapps/extensions/mytheme/\n"
                                "  content/mozapps/extensions/mytheme (mytheme/*.svg)\n"
                                "  content/mozapps/extensions/mytheme/small.png (mytheme/small.png)\n"
                                "% resource gre-alias resource://gre/somewhere/\n", encoding="utf-8")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "resource")
    return tree


def test_resource_urls_resolve_through_the_jar_mn_resource_lines(tmp_path, master):
    tree = resource_tree(tmp_path, master)
    ix = chrome.Index(tree, "browser/branding/gorilla")
    ext = tree / "toolkit" / "mozapps" / "extensions" / "mytheme"
    assert ix.resolve_resource("resource://mytheme/icon.svg") == (True, (ext / "icon.svg").resolve())
    assert ix.resolve_resource("resource://mytheme/small.png?x#y") == (True, (ext / "small.png").resolve())
    assert ix.resolve_resource("resource://mytheme/gone.svg") == (True, None)
    assert ix.resolve_resource("resource://nobody-registers-this/icon.svg") == (False, None)
    # an alias onto another resource: URL is not a jar path and is not treated as one
    assert ix.resolve_resource("resource://gre-alias/x.svg") == (False, None)
    assert chrome.resolve("resource://mytheme/icon.svg", tree / "x.css", ix)[1] == "file"
    assert chrome.resolve("resource://mytheme/gone-for-good.svg", tree / "x.css", ix) == (None, "missing")
    assert chrome.resolve("resource://nobody-registers-this/icon.svg", tree / "x.css", ix) == (None, "external")


def test_a_resource_url_is_measured_and_a_missing_one_fails_instead_of_being_unverifiable(tmp_path, master):
    tree = resource_tree(tmp_path, master)
    p = tree / "browser" / "base" / "x.css"
    p.write_text('.a { background-image: url("resource://mytheme/icon.svg"); width: 40px; }\n'
                 '.b { background-image: url("resource://mytheme/small.png"); width: 40px; }\n'
                 '.c { background-image: url("resource://mytheme/gone-for-good.svg"); width: 40px; }\n'
                 '.d { background-image: url("resource://runtime-only/icon.svg"); width: 40px; }\n', encoding="utf-8")
    items = css.check_file(p, "browser/base/x.css", chrome.Index(tree, "browser/branding/gorilla"), tree)
    by = {i["item"].split(" ")[1]: i for i in items}
    assert by[".a"]["verdict"] == "PASS" and "vector" in by[".a"]["evidence"]
    assert by[".b"]["verdict"] == "FAIL" and "1.00x" in by[".b"]["evidence"]       # measured: 40 px into 40 px
    assert by[".c"]["rule"] == "ASSET-MISSING" and by[".c"]["verdict"] == "FAIL"
    assert by[".d"]["verdict"] == "UNVERIFIABLE"                                    # unregistered: still not a pass


def four_brandings(tmp_path, dirs=("nightly", "official", "aurora", "unofficial")):
    root = tmp_path / "tree" / "browser" / "branding"
    shared = "M10 10 L 20 20 L 30 10 L 40 20 L 50 10 L 60 20 L 70 10 L 80 20 Z"      # the "PDF" letters
    logo = "M5 5 C 10 40 30 40 60 5 C 70 30 90 30 95 5 L 95 95 L 5 95 Z M 1 1 L 2 2"  # one channel's logo
    for d in dirs:
        c = root / d / "content"
        c.mkdir(parents=True)
        extra = '<path d="%s"/>' % logo if d == "nightly" else ""
        (c / "document_pdf.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="%s"/>%s</svg>'
                                            % (shared, extra), encoding="utf-8")
    gb = root / "gorilla" / "content"
    gb.mkdir(parents=True)
    return root, gb, shared, logo


def test_path_data_every_mozilla_channel_carries_is_neutral_but_a_channel_logo_is_still_caught(tmp_path):
    root, gb, shared, logo = four_brandings(tmp_path)
    _, paths = leftovers.mozilla_index(root)
    assert " ".join(shared.split()) not in paths and " ".join(logo.split()) in paths
    (gb / "pdf.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="%s"/><image href="x"/></svg>'
                                % shared, encoding="utf-8")
    (gb / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><path d="%s"/></svg>' % logo,
                                 encoding="utf-8")
    items = {(i["rule"], i["item"].rsplit("/", 1)[-1]): i["verdict"] for i in leftovers.check(gb.parent, tmp_path / "tree")}
    assert items[("BRAND-002", "pdf.svg")] == "PASS"
    assert items[("BRAND-002", "logo.svg")] == "FAIL"


def test_with_a_mozilla_channel_absent_nothing_is_excluded(tmp_path):
    root, _, shared, _ = four_brandings(tmp_path, dirs=("nightly", "official", "aurora"))
    _, paths = leftovers.mozilla_index(root)
    assert " ".join(shared.split()) in paths          # "all four" is unproven with three: stay strict


def strip(master, size=(150, 57), region=(97, 5, 144, 52), bg=(255, 255, 255), logo=None, flip=False):
    """An installer strip like wizHeader.bmp: an opaque field with the logo pasted into `region`."""
    side = region[2] - region[0]
    c = Image.new("RGB", size, bg)
    lg = logo if logo is not None else rasters.squarify(master).resize((side, side), Image.LANCZOS)
    c.paste(lg, region[:2], lg)
    return c.transpose(Image.FLIP_LEFT_RIGHT) if flip else c


def test_a_region_measures_the_logo_inset_in_installer_art(tmp_path, master):
    sq = rasters.squarify(master)
    im = strip(master)
    v, ev, _ = rasters.provenance(im, sq, master_squared=True)
    assert v != "PASS"                                      # the whole strip is not the master's artwork
    v, ev, nums = rasters.provenance(im, sq, master_squared=True, region=(97, 5, 144, 52))
    assert v == "PASS", ev
    assert nums["region"] == [97, 5, 144, 52] and nums["background"] == [255, 255, 255]
    softlogo = soft(sq, 47).convert("RGBA")
    v, ev, _ = rasters.provenance(strip(master, logo=softlogo), sq, master_squared=True, region=(97, 5, 144, 52))
    assert v == "FAIL", ev                                  # a region is no excuse for a blurred logo


def test_an_opaque_region_is_compared_against_the_master_on_its_own_background(tmp_path, master):
    sq = rasters.squarify(master)
    region = (23, 62, 141, 180)
    im = strip(master, size=(164, 314), region=region, bg=(12, 12, 14))
    v, ev, nums = rasters.provenance(im, sq, master_squared=True, region=region)
    assert v == "PASS", ev
    assert nums["background"] == [12, 12, 14]
    # the same crop judged against the master on white would see a different picture: the flattening matters
    flat = Image.new("RGBA", sq.size, (12, 12, 14, 255))
    flat.alpha_composite(sq)
    crop = im.crop(region)
    assert rasters.colour_distance(rasters.squarify(crop), flat) < rasters.colour_distance(rasters.squarify(crop), sq)


def test_flip_mirrors_a_right_to_left_strip_back_and_a_bad_region_is_unverifiable(tmp_path, master):
    sq = rasters.squarify(master)
    im = strip(master, flip=True)                           # logo now at x 6..53
    v, ev, nums = rasters.provenance(im, sq, master_squared=True, region=(6, 5, 53, 52), flip=True)
    assert v == "PASS", ev
    assert nums["flip"] is True
    v, ev, _ = rasters.provenance(im, sq, master_squared=True, region=(100, 5, 160, 52))
    assert v == "UNVERIFIABLE" and "not inside" in ev


def test_region_and_flip_in_the_allowlist_are_validated_and_reach_the_static_layer(tmp_path, master, master_file):
    p = tmp_path / "geo.yaml"
    m = str(master_file).replace("\\", "/")
    p.write_text('masters:\n  - files: "browser/branding/gorilla/wizHeader.bmp"\n    master: "%s"\n'
                 '    region: [97, 5, 144, 52]\n    why: header strip\n'
                 '  - files: "browser/branding/gorilla/wizHeaderRTL.bmp"\n    master: "%s"\n'
                 '    region: [6, 5, 53, 52]\n    flip: true\n    why: mirrored strip\n'
                 '  - files: "browser/branding/gorilla/**"\n    master: "%s"\n    why: catch-all\naccept: []\n' % (m, m, m),
                 encoding="utf-8")
    a = allowmod.load(p)
    assert not a["problems"]
    assert allowmod.geometry(allowmod.master_for("browser/branding/gorilla/wizHeaderRTL.bmp", a)[1]) == ((6, 5, 53, 52), True)
    assert allowmod.geometry(allowmod.master_for("browser/branding/gorilla/icon.png", a)[1]) == (None, False)
    tree, up = fake_tree(tmp_path, master)
    gb = tree / "browser" / "branding" / "gorilla"
    strip(master).save(gb / "wizHeader.bmp")
    strip(master, flip=True).save(gb / "wizHeaderRTL.bmp")
    res = static.check(tree, "browser/branding/gorilla", upstream_commit=up, allow=a)
    got = {i["item"]: i for i in res["items"] if i["rule"] == "ICON-002" and "wizHeader" in i["item"]}
    assert got["browser/branding/gorilla/wizHeader.bmp"]["verdict"] == "PASS"
    assert got["browser/branding/gorilla/wizHeaderRTL.bmp"]["verdict"] == "PASS"
    assert "mirrored" in got["browser/branding/gorilla/wizHeaderRTL.bmp"]["evidence"]
    for bad in ("[97, 5, 44, 52]", "[1, 2, 3]", "[0.5, 1, 4, 4]", "[-1, 0, 4, 4]"):
        q = tmp_path / "bad.yaml"
        q.write_text('masters:\n  - files: "x"\n    master: "y"\n    region: %s\n    why: w\n' % bad, encoding="utf-8")
        assert allowmod.load(q)["problems"], bad
    q.write_text('masters:\n  - files: "x"\n    master: "y"\n    flip: "yes"\n    why: w\n', encoding="utf-8")
    assert allowmod.load(q)["problems"]
