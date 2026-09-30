"""lifecycle: a clean app passes, a leaky uninstaller is caught, a broken install is never uninstalled blind."""
import sys

import pytest
import yaml

from fieldkit.build import lifecycle

PY = sys.executable


def _spec(tmp_path, install, uninstall, allowed=None):
    app = tmp_path / "App"
    data = tmp_path / "AppData"
    s = {"name": "fake", "watch": {"paths": [str(tmp_path)]},
         "install": {"cmd": [PY, "-c", install.format(app=app, data=data)],
                     "verify": [{"files_exist": [str(app / "app.exe")]}, {"output_contains": "installed"}]},
         "uninstall": {"cmd": [PY, "-c", uninstall.format(app=app, data=data)],
                       "verify": [{"files_absent": [str(app / "app.exe")]}]},
         "allowed_leftovers": allowed or []}
    p = tmp_path.parent / f"{tmp_path.name}-spec.yaml"
    p.write_text(yaml.safe_dump(s))
    return p


INSTALL = ("import pathlib; a=pathlib.Path(r'{app}'); a.mkdir(); (a/'app.exe').write_text('x'); "
           "d=pathlib.Path(r'{data}'); d.mkdir(); (d/'settings.ini').write_text('y'); print('installed')")
CLEAN_UNINSTALL = "import shutil; shutil.rmtree(r'{app}'); shutil.rmtree(r'{data}')"
LEAKY_UNINSTALL = "import shutil; shutil.rmtree(r'{app}')"                 # forgets the settings folder


def test_needs_the_owners_approval(tmp_path):
    with pytest.raises(PermissionError):
        lifecycle.run(_spec(tmp_path, INSTALL, CLEAN_UNINSTALL))


def test_clean_lifecycle(tmp_path):
    r = lifecycle.run(_spec(tmp_path, INSTALL, CLEAN_UNINSTALL), approve=True, state_dir=tmp_path.parent / "st1")
    assert r["clean"], lifecycle.lines(r)
    assert [s["step"] for s in r["steps"]] == ["install", "uninstall"]


def test_leftovers_are_reported(tmp_path):
    r = lifecycle.run(_spec(tmp_path, INSTALL, LEAKY_UNINSTALL), approve=True, state_dir=tmp_path.parent / "st2")
    assert not r["clean"]
    left = r["leftovers"]["files"]["added"]
    assert any(p.endswith("settings.ini") for p in left)
    assert any("LEFTOVERS in files" in l for l in lifecycle.lines(r))


def test_allowed_leftovers_are_not_counted(tmp_path):
    r = lifecycle.run(_spec(tmp_path, INSTALL, LEAKY_UNINSTALL, allowed=["settings.ini"]), approve=True,
                      state_dir=tmp_path.parent / "st3")
    assert r["clean"], lifecycle.lines(r)


def test_failed_install_is_not_followed_by_uninstall(tmp_path):
    r = lifecycle.run(_spec(tmp_path, "import sys; sys.exit(3)", CLEAN_UNINSTALL), approve=True,
                      state_dir=tmp_path.parent / "st4")
    assert not r["clean"] and [s["step"] for s in r["steps"]] == ["install"]
