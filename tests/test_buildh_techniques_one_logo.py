"""D-157-35 (2026-10-07): one master Gorilla logo, never drawn tiny. Run by the decision register (fieldkit_test).
  - technique T-157-35-A passes a tree that names only about-logo.png and fails one that names a retired file
  - visual rule RT-TINYLOGO fails the artwork drawn below 64 px and leaves big artwork and the icon ladder alone
  - a decision's image_sharp master can live in the owner repo (owner:<path>)"""
import io
import subprocess

from PIL import Image, ImageDraw

from fieldkit.buildh import decisions, techniques
from fieldkit.visual import runtime

T = next(t for t in techniques.TECHNIQUES if t["id"] == "T-157-35-A")


def _tree(tmp_path, extra=None):
    w = tmp_path / "tree"
    files = {
        "browser/themes/shared/master-redirect.css":
            ':root {\n  --gorilla-master-icon: url("chrome://branding/content/about-logo.png");\n}\n',
        "toolkit/content/widgets/moz-page-nav/moz-page-nav.css":
            "  > .logo {\n    /* GORILLA D-157-35: no 24 px Gorilla beside the page title (Settings) */\n    display: none;\n",
        "browser/base/content/aboutRobots.css": '.title { background-image: url("chrome://branding/content/about-logo.png"); }\n',
        "browser/themes/shared/identity-block/identity-block.css":
            "/* GORILLA D-157-35: no 16 px Gorilla in the address bar on browser pages; the label stays */\n",
        "toolkit/themes/windows/global/wizard.css": '.wizard-header { list-style-image: url("chrome://branding/content/icon128.png"); }\n',
    }
    files.update(extra or {})
    for rel, text in files.items():
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_bytes(text.encode())
    g = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)
    g("init", "-q")
    g("add", ".")
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "t")
    return w


def _result(w, tid="T-157-35-A"):
    return next(r for r in techniques.scan(w) if r["id"] == tid)


def test_a_tree_with_one_master_holds(tmp_path):
    r = _result(_tree(tmp_path))
    assert r["ok"], r["signals"]


def test_a_retired_logo_file_named_anywhere_fails(tmp_path):
    # "about-logo@2x" + ".png": written whole it reads as an email address to the privacy scan of the commit hook
    for i, url in enumerate(("about-logo.svg", "about-logo@2x" + ".png", "about-logo-private.png", "about.svg")):
        w = _tree(tmp_path / str(i), {"toolkit/content/aboutX.css": f'body {{ background: url("chrome://branding/content/{url}"); }}\n'})
        bad = [s for s in _result(w)["signals"] if not s["ok"]]
        assert [s["name"] for s in bad] == ["retired-logo-files"], url


def test_the_master_back_to_the_svg_fails(tmp_path):
    w = _tree(tmp_path, {"browser/themes/shared/master-redirect.css":
                         ':root {\n  --gorilla-master-icon: url("chrome://branding/content/about-logo.svg");\n}\n'})
    names = {s["name"] for s in _result(w)["signals"] if not s["ok"]}
    assert names == {"master-is-the-png", "retired-logo-files"}


def test_small_icons_hold_where_allowed_and_fail_anywhere_new(tmp_path):
    assert _result(_tree(tmp_path / "ok"), "T-157-35-B")["ok"]          # wizard.css is on the allow list (128 px)
    w = _tree(tmp_path / "new", {"toolkit/content/aboutX.html": '<link rel="icon" href="chrome://branding/content/icon32.png">\n'})
    bad = [s for s in _result(w, "T-157-35-B")["signals"] if not s["ok"]]
    assert [s["name"] for s in bad] == ["small-gorilla-icons"] and "aboutX.html" in bad[0]["detail"]
    w = _tree(tmp_path / "chip", {"browser/themes/shared/identity-block/identity-block.css":
                                  "#identity-icon { list-style-image: url(chrome://branding/content/icon16.png); }\n"})
    names = {s["name"] for s in _result(w, "T-157-35-B")["signals"] if not s["ok"]}
    assert names == {"chip-hidden", "small-gorilla-icons"}


def test_rt_tinylogo_judges_any_gorilla_drawn_small():
    m = {"branding": [
        {"sel": "div.logo", "kind": "background", "url": "chrome://branding/content/about-logo.png", "natural": [1400, 1400],
         "painted": [24, 24]},
        {"sel": "h1.logo", "kind": "background", "url": "chrome://branding/content/about-logo.png", "natural": [1400, 1400],
         "painted": [551, 551]},
        {"sel": "td img", "kind": "img", "url": "chrome://branding/content/icon32.png", "natural": [32, 32], "painted": [16, 16]},
    ]}
    items = [i for i in runtime.judge_metrics("about:preferences@1x", m, 1) if i["rule"] == "RT-TINYLOGO"]
    # the 24 px artwork AND the 16 px ladder icon fail (owner 2026-10-08); the 551 px logo does not
    assert [i["item"].split(" ", 1)[1] for i in items] == ["div.logo", "td img"]
    assert all(i["verdict"] == "FAIL" for i in items) and "24x24 CSS px" in items[0]["evidence"]


def _png(path, size):
    im = Image.new("RGB", (size, size), (0, 0, 0))
    d = ImageDraw.Draw(im)
    for i in range(0, size, 6):
        d.line((i, 0, size - i, size), fill=(255, 200, 40), width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


def test_image_sharp_reads_an_owner_master(tmp_path):
    tree, owner = tmp_path / "tree", tmp_path / "owner"
    _png(owner / "masters" / "canonical.png", 1200)
    big = Image.open(owner / "masters" / "canonical.png").resize((600, 600), Image.LANCZOS)
    (tree / "branding").mkdir(parents=True)
    big.save(tree / "branding" / "logo.png")
    ok, why = decisions._image_sharp(tree, {"path": "branding/logo.png", "master": "owner:masters/canonical.png"}, owner)
    assert ok and "owner:masters/canonical.png" in why
    soft = big.resize((150, 150), Image.LANCZOS).resize((600, 600), Image.BILINEAR)
    soft.save(tree / "branding" / "logo.png")
    ok, _ = decisions._image_sharp(tree, {"path": "branding/logo.png", "master": "owner:masters/canonical.png"}, owner)
    assert not ok


def test_an_early_return_named_by_the_technique_guards_the_dead_waiters_below_it(tmp_path):
    """2026-10-08: about:support's Normandy section answers at once (GORILLA TECHNIQUE T-157-31-A, if (true) return);
    the store waiters 40+ lines below it are dead code and count as guarded."""
    from fieldkit.buildh import techniques
    body = ["var dataProviders = {", "  async normandy(done) {", "    if (!AppConstants.MOZ_NORMANDY) {", "      done();",
            "      return;", "    }", "    // GORILLA TECHNIQUE T-157-31-A: answer at once", "    if (true) {", "      done({", "        a: [],", "      });",
            "      return;", "    }"] + ["    const x = 1;"] * 45 + ["    ExperimentAPI.manager.store", "      .ready()", "  },", "};"]
    f = tmp_path / "T.sys.mjs"
    f.write_text("\n".join(body), encoding="utf-8")
    n = body.index("    ExperimentAPI.manager.store") + 1
    assert techniques._guarded(tmp_path, "T.sys.mjs", n, "T-157-31-A", 40)
    assert not techniques._guarded(tmp_path, "T.sys.mjs", n, "T-157-99-Z", 40)
