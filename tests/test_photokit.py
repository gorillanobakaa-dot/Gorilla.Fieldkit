"""PhotoKit (kits/photokit/photokit.py), run as a user would run it: subprocess, real files.

Every command is shown failing as well as passing.
"""
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "kits" / "photokit" / "photokit.py"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="kits/photokit missing")
PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402


def run(*args):
    r = subprocess.run([sys.executable, "-I", str(TOOL), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout + r.stderr


def picture(path, seed, size=(640, 480)):
    im = Image.new("RGB", size, (200, 200, 200))
    d = ImageDraw.Draw(im)
    for k in range(6):
        x = (seed * 97 + k * 131) % size[0]
        y = (seed * 53 + k * 71) % size[1]
        d.rectangle([x, y, x + 120, y + 90], fill=((seed * 40 + k * 30) % 255, (k * 60) % 255, (seed * 90) % 255))
    im.save(path, quality=90)
    return path


@pytest.fixture
def photos(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    picture(src / "IMG_1.jpg", 1)
    Image.open(src / "IMG_1.jpg").resize((600, 450)).save(src / "IMG_2.jpg", quality=70)  # same shot, re-saved
    picture(src / "IMG_3.jpg", 7)
    picture(src / "IMG_4.jpg", 13, size=(480, 640))
    return src


def test_sheet_makes_labelled_grids_and_fails_on_empty(photos, tmp_path):
    rc, out = run("sheet", photos, tmp_path / "sheets", "--per", "3", "--strip", "IMG_")
    assert rc == 0
    sheets = sorted((tmp_path / "sheets").glob("sheet_*.jpg"))
    assert [s.name for s in sheets] == ["sheet_01.jpg", "sheet_02.jpg"]
    assert Image.open(sheets[0]).size == (4 * 480, 1 * (480 + 36))
    (tmp_path / "empty").mkdir()
    assert run("sheet", tmp_path / "empty", tmp_path / "x")[0] == 1


def test_dupes_finds_the_resaved_shot_only(photos, tmp_path):
    rc, out = run("dupes", photos, "--threshold", "6", "--sheet", tmp_path / "pairs")
    assert rc == 1
    assert "IMG_1.jpg  IMG_2.jpg" in out and "IMG_3" not in out.split("group(s)")[0]
    assert (tmp_path / "pairs" / "pairs_01.jpg").is_file()
    (photos / "IMG_2.jpg").unlink()
    rc, out = run("dupes", photos, "--threshold", "6")
    assert rc == 0 and "0 group(s)" in out


def test_compare_draws_chosen_pairs_and_rejects_unknown(photos, tmp_path):
    rc, out = run("compare", photos, tmp_path / "cmp", "1:3", "--prefix", "IMG_")
    assert rc == 0 and (tmp_path / "cmp" / "pairs_01.jpg").is_file()
    assert run("compare", photos, tmp_path / "cmp", "1:99", "--prefix", "IMG_")[0] == 2


def test_check_catches_missing_unknown_and_repeated(photos, tmp_path):
    good = tmp_path / "map.txt"
    good.write_text("5.4 1 3  # walls\n5.12 4\nskip 2\n", encoding="utf-8")
    rc, out = run("check", photos, good, "--prefix", "IMG_")
    assert rc == 0 and "5.4:2" in out and "skip:1" in out and "OK" in out
    bad = tmp_path / "bad.txt"
    bad.write_text("5.4 1 3 3\n5.12 9\n", encoding="utf-8")
    rc, out = run("check", photos, bad, "--prefix", "IMG_")
    assert rc == 1
    assert "IMG_3 already mapped" in out and "IMG_9 is not in" in out and "IMG_2 is not mapped" in out


def test_sort_writes_item_folders_skips_dupes_and_refuses_a_bad_mapping(photos, tmp_path):
    m = tmp_path / "map.txt"
    m.write_text("5.4 1 3\n5.12 4\nskip 2\n", encoding="utf-8")
    out = tmp_path / "sorted"
    rc, text = run("sort", photos, m, out, "--prefix", "IMG_", "--edge", "300")
    assert rc == 0 and "sorted 3" in text
    assert sorted(p.name for p in (out / "5.4").iterdir()) == ["5.4_1.jpg", "5.4_3.jpg"]
    assert not (out / "skip").exists()
    assert max(Image.open(out / "5.12" / "5.12_4.jpg").size) == 300
    assert (photos / "IMG_1.jpg").is_file()  # originals untouched
    m.write_text("5.4 1\n", encoding="utf-8")
    rc, text = run("sort", photos, m, tmp_path / "sorted2", "--prefix", "IMG_")
    assert rc == 1 and "mapping has faults" in text and not (tmp_path / "sorted2").exists()


def test_serve_gives_files_in_root_only_with_cors(photos, tmp_path):
    import importlib.util
    import threading
    import urllib.error
    import urllib.request
    spec = importlib.util.spec_from_file_location("photokit", TOOL)
    pk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pk)
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    srv = pk.make_server(photos, 0, idle=60)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    try:
        r = urllib.request.urlopen(base + "IMG_1.jpg", timeout=10)
        assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "*"
        assert r.headers["Access-Control-Allow-Private-Network"] == "true"
        for bad in ("", "../secret.txt", "%2e%2e/secret.txt", "nope.jpg"):
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(base + bad, timeout=10)
            assert e.value.code == 404, bad
    finally:
        srv.shutdown()


def test_doctor_passes_here():
    rc, out = run("doctor")
    assert rc == 0 and "pillow" in out and "list_connected_browsers" in out


def test_fetch_refuses_non_https(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text("5.1_a|http://example.invalid/x.jpg\n", encoding="utf-8")
    rc, out = run("fetch", lst, tmp_path / "got")
    assert rc == 0 and "refused (not https)" in out and "fetched 0" in out
