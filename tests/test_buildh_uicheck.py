"""UI checks (fieldkit/buildh/uicheck.py): the build-23 Gorilla.Satellite menu defects must never pass again."""
import subprocess

from fieldkit.buildh import uicheck

GIT = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def _repo(tmp_path, files_before, files_after):
    w = tmp_path / "tree"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    for rel, text in files_before.items():
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), *GIT, "commit", "-q", "-m", "pristine"], check=True)
    for rel, text in files_after.items():
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_text(text, encoding="utf-8")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), *GIT, "commit", "-q", "-m", "gorilla"], check=True)
    return w


PRISTINE = {"dom/webidl/Node.webidl": "interface Node { readonly attribute Document? ownerDocument; };\n",
            "browser/themes/shared/toolbarbuttons.css": "#a {}\n"}


def test_build_23_menu_code_fails_both_rules(tmp_path):
    w = _repo(tmp_path, PRISTINE, {"browser/modules/GorillaLinkMode.sys.mjs":
        'const popup = doc.createXULElement("menupopup");\nconst win = doc.ownerGlobal;\n'})
    rows = {r["rule"]: r for r in uicheck.lint(w)}
    assert not rows["UI-API"]["ok"] and "GorillaLinkMode.sys.mjs:2" in rows["UI-API"]["evidence"]
    assert not rows["UI-MENU"]["ok"] and "system grey" in rows["UI-MENU"]["evidence"]


def test_build_24_menu_code_passes(tmp_path):
    w = _repo(tmp_path, PRISTINE, {
        "browser/modules/GorillaLinkMode.sys.mjs":
            'const popup = doc.createXULElement("menupopup");\n'
            '// not ownerGlobal: comments may name it\nconst win = () => button.ownerDocument.defaultView;\n',
        "browser/themes/shared/toolbarbuttons.css":
            "#a {}\n#gorilla-satellite-button {\n  > menupopup {\n    appearance: none !important;\n"
            "    background-color: #000000 !important;\n  }\n}\n"})
    assert all(r["ok"] for r in uicheck.lint(w)), uicheck.lint(w)


def test_an_api_the_tree_defines_is_not_flagged(tmp_path):
    before = dict(PRISTINE, **{"dom/webidl/Node.webidl": "interface Node { readonly attribute Window ownerGlobal; };\n"})
    w = _repo(tmp_path, before, {"browser/modules/X.sys.mjs": "const w = el.ownerGlobal;\n"})
    assert {r["rule"] for r in uicheck.lint(w)} == {"UI-MENU"}


def test_unchanged_upstream_code_is_not_gorillas_business(tmp_path):
    before = dict(PRISTINE, **{"toolkit/x.js": "const w = el.ownerGlobal;\nconst p = createXULElement(\"menupopup\");\n"})
    w = _repo(tmp_path, before, {"toolkit/y.js": "const ok = 1;\n"})
    assert all(r["ok"] for r in uicheck.lint(w))


BUILD_23 = [
    "UI|menu|gorilla-satellite-button|Off: a normal connection|1.25|rgb(0,255,255)|rgb(255,255,255)",
    "UI|surface|menu gorilla-satellite-button|8|0",
    'UI|error|menu gorilla-satellite-button|TypeError: can\'t access property "gBrowser", win is undefined @GorillaLinkMode.sys.mjs:427',
]
BUILD_24 = [
    "UI|menu|gorilla-satellite-button|Off: a normal connection|16.75|rgb(0,255,255)|rgb(0,0,0)",
    "UI|settings|gorillaLinkMode|Gorilla.Satellite mode|18.59|rgb(242,240,248)|rgb(0,0,0)",
    "UI|surface|menu gorilla-satellite-button|5|3",
    "UI|surface|settings|6|0",
]


def test_build_23_probe_output_fails_contrast_and_errors():
    rows = {r["check"].split(" (")[0]: r for r in uicheck.judge(uicheck.parse(BUILD_23))}
    assert not rows["UI: every menu item and Gorilla Settings control is readable"]["ok"]
    assert "1.25:1" in rows["UI: every menu item and Gorilla Settings control is readable"]["evidence"]
    assert not rows["UI: no script error while menus and Settings open"]["ok"]


def test_build_24_probe_output_passes():
    assert all(r["ok"] for r in uicheck.judge(uicheck.parse(BUILD_24)))


def test_no_measurement_is_not_a_pass():
    rows = uicheck.judge(uicheck.parse(["noise", "UI|surface|settings|0|0"]))
    assert not rows[0]["ok"] and "no item measured" in rows[0]["evidence"]


def test_a_menu_that_opens_with_everything_hidden_fails():
    rows = uicheck.judge(uicheck.parse(BUILD_24[:2] + ["UI|surface|menu PanelUI-menu-button|0|12"]))
    assert not [r for r in rows if r["check"] == "UI: no menu opens empty"][0]["ok"]
