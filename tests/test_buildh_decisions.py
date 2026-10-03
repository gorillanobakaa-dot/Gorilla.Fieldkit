"""The maintainer's decisions are checks: enforced must verify, pending blocks strict, no check = violated."""
import zipfile

import yaml

from fieldkit.buildh import decisions as dec


def _owner(tmp_path, entries):
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "PRODUCT-DECISIONS.yaml").write_text(yaml.safe_dump({"release": "157.0", "decisions": entries}), encoding="utf-8")
    (tmp_path / "mozconfig").write_text("ac_add_options --disable-updater   # no updates\n", encoding="utf-8")
    return tmp_path


def _e(i, status="enforced", verify=None, **kw):
    d = {"id": i, "title": i, "decided": "2026-10-02", "by": "maintainer", "provenance": "test", "why": "test", "status": status}
    if verify is not None:
        d["verify"] = verify
    d.update(kw)
    return d


def _install(tmp_path, prefs):
    inst = tmp_path / "inst"
    (inst / "browser").mkdir(parents=True)
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("greprefs.js", "")
        z.writestr("modules/x.sys.mjs", "")
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("defaults/preferences/firefox.js", prefs)
    return inst


def test_enforced_decision_with_passing_checks(tmp_path):
    o = _owner(tmp_path, [_e("A", verify=[{"mozconfig_has": {"file": "mozconfig", "text": "ac_add_options --disable-updater"}}])])
    r = dec.check(o)
    assert r["ok"] and r["rows"][0]["verdict"] == "ENFORCED"


def test_a_lost_decision_is_violated(tmp_path):
    o = _owner(tmp_path, [_e("A", verify=[{"mozconfig_has": {"file": "mozconfig", "text": "ac_add_options --disable-crashreporter"}}])])
    r = dec.check(o)
    assert not r["ok"] and r["rows"][0]["verdict"] == "VIOLATED"


def test_enforced_without_a_check_is_violated(tmp_path):
    r = dec.check(_owner(tmp_path, [_e("A")]))
    assert not r["ok"] and r["rows"][0]["verdict"] == "VIOLATED"


def test_pending_passes_normally_and_blocks_strict(tmp_path):
    o = _owner(tmp_path, [_e("A", status="pending")])
    assert dec.check(o)["ok"] and not dec.check(o, strict=True)["ok"]


def test_trade_off_is_accepted(tmp_path):
    r = dec.check(_owner(tmp_path, [_e("A", status="trade-off")]), strict=True)
    assert r["ok"] and r["rows"][0]["verdict"] == "ACCEPTED"


def test_pref_value_and_lock_are_checked_on_the_installed_build(tmp_path):
    inst = _install(tmp_path, 'pref("a.b", true, locked);\npref("c.d", false);\n')
    o = _owner(tmp_path, [_e("A", verify=[{"pref": {"name": "a.b", "value": True, "locked": True}},
                                          {"pref": {"name": "c.d", "value": False, "locked": False}}]),
                          _e("B", verify=[{"pref": {"name": "c.d", "value": False, "locked": True}}])])
    r = dec.check(o, install_dir=inst)
    assert [x["verdict"] for x in r["rows"]] == ["ENFORCED", "VIOLATED"]


def test_omni_and_install_absence(tmp_path):
    inst = _install(tmp_path, "")
    (inst / "updater.exe").write_bytes(b"")
    o = _owner(tmp_path, [_e("A", verify=[{"omni_absent": ["modules/x"]}]), _e("B", verify=[{"installed_absent": ["updater.exe"]}])])
    r = dec.check(o, install_dir=inst)
    assert [x["verdict"] for x in r["rows"]] == ["VIOLATED", "VIOLATED"]


def test_no_install_is_uncheckable_never_enforced(tmp_path):
    r = dec.check(_owner(tmp_path, [_e("A", verify=[{"pref": {"name": "a", "value": 1}}])]))
    assert not r["ok"] and r["rows"][0]["verdict"] == "UNCHECKABLE"


def test_register_problems_fail(tmp_path):
    r = dec.check(_owner(tmp_path, [_e("A", status="maybe"), _e("A", status="trade-off")]))
    assert not r["ok"] and r["problems"]


def test_the_real_register_is_well_formed():
    from pathlib import Path
    real = Path.home() / "Documents" / "Gorilla.firefox"
    if not (real / dec.REGISTER).is_file():
        import pytest
        pytest.skip("no maintainer register on this machine")
    assert dec.load(real)["problems"] == []


def test_image_sharp_catches_a_soft_copy(tmp_path):
    from PIL import Image, ImageDraw
    work = tmp_path / "tree"
    work.mkdir()
    master = Image.new("RGBA", (1200, 1200), (0, 0, 0, 255))
    d = ImageDraw.Draw(master)
    for x in range(0, 1200, 12):
        d.line([(x, 0), (x, 1199)], fill=(255, 255, 255, 255), width=3)
    master.save(work / "master.png")
    master.resize((500, 500), Image.LANCZOS).save(work / "good.png")
    master.resize((100, 100), Image.LANCZOS).resize((500, 500), Image.BILINEAR).save(work / "soft.png")
    o = _owner(tmp_path, [_e("G", verify=[{"image_sharp": {"path": "good.png", "master": "master.png"}}]),
                          _e("S", verify=[{"image_sharp": {"path": "soft.png", "master": "master.png"}}])])
    r = dec.check(o, workdir=work)
    assert [x["verdict"] for x in r["rows"]] == ["ENFORCED", "VIOLATED"]
