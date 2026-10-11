"""fieldkit/build/repro.py: two builds compared byte for byte, and a difference named where it is and why.

What is held: identical folders are REPRODUCED and nothing else is; a .deb (ar) holding a tar holding a file is
opened down to that file; a time stamp, a build folder and a member order are each named as the cause; a file in
only one build is listed; the command line exits 0 for the same bytes and 3 for different ones.
"""
import io
import json
import subprocess
import sys
import tarfile
import zipfile

from fieldkit.build import repro


def _tar(files, mtime=1789000000, order=None, uid=0):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as tf:
        for name in order or sorted(files):
            data = files[name]
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.uid, ti.mode = len(data), mtime, uid, 0o644
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _ar(members):
    out = b"!<arch>\n"
    for name, data in members:
        out += f"{name:<16}{0:<12}{0:<6}{0:<6}{100644:<8}{len(data):<10}`\n".encode() + data
        if len(data) & 1:
            out += b"\n"
    return out


def _deb(files, **kw):
    return _ar([("debian-binary", b"2.0\n"), ("control.tar.xz", _tar({"./control": b"Package: x\n"})),
                ("data.tar.xz", _tar(files, **kw))])


def test_the_same_bytes_in_two_folders_are_reproduced(tmp_path):
    for side in ("a", "b"):
        (tmp_path / side / "bin").mkdir(parents=True)
        (tmp_path / side / "bin" / "tool").write_bytes(b"\x7fELF same")
    r = repro.compare(tmp_path / "a", tmp_path / "b")
    assert r["identical"] and [s["file"] for s in r["same"]] == ["bin/tool"]
    assert "REPRODUCED: 1 file(s)" in repro.lines(r)[-1]


def test_a_file_inside_a_tar_inside_a_deb_is_named_with_its_time_stamp(tmp_path):
    (tmp_path / "a.deb").write_bytes(_deb({"./usr/bin/x": b"same", "./usr/share/doc/x": b"doc"}))
    (tmp_path / "b.deb").write_bytes(_deb({"./usr/bin/x": b"same", "./usr/share/doc/x": b"doc"}, mtime=1789000099))
    r = repro.compare(tmp_path / "a.deb", tmp_path / "b.deb")
    assert not r["identical"]
    found = r["differ"][0]["findings"]
    assert any(f["where"] == "data.tar.xz!./usr/bin/x" and "mtime" in f["what"] and "SOURCE_DATE_EPOCH" in f["what"]
               for f in found), found


def test_the_build_folder_baked_into_a_binary_is_named_as_the_cause(tmp_path):
    a_root, b_root = tmp_path / "build-one", tmp_path / "somewhere-else"
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "objtool").write_bytes(b"\x7fELF\0\0" + str(a_root).encode() + b"/tools/objtool\0rest")
    (tmp_path / "b" / "objtool").write_bytes(b"\x7fELF\0\0" + str(b_root).encode() + b"/tools/objtool\0rest")
    r = repro.compare(tmp_path / "a", tmp_path / "b", roots=[a_root, b_root])
    f = r["differ"][0]["findings"][0]
    assert any("build folder 1" in c for c in f["causes"]) and f["first_difference_at"] > 0


def test_a_date_written_into_a_file_is_named(tmp_path):
    (tmp_path / "a").write_bytes(b"version 1.0 built Sun Oct 11 12:31:41 UTC 2026\n")
    (tmp_path / "b").write_bytes(b"version 1.0 built Sun Oct 11 12:34:06 UTC 2026\n")
    f = repro.compare(tmp_path / "a", tmp_path / "b")["differ"][0]["findings"][0]
    assert "a date or time is written into the file" in f["causes"]


def test_the_same_files_in_another_order_and_another_owner_are_told_apart(tmp_path):
    files = {"./a": b"1", "./b": b"2"}
    (tmp_path / "one.tar.xz").write_bytes(_tar(files, order=["./a", "./b"]))
    (tmp_path / "two.tar.xz").write_bytes(_tar(files, order=["./b", "./a"]))
    found = repro.compare(tmp_path / "one.tar.xz", tmp_path / "two.tar.xz")["differ"][0]["findings"]
    assert any("different order" in f["what"] for f in found)
    (tmp_path / "three.tar.xz").write_bytes(_tar(files, uid=1000))
    found = repro.compare(tmp_path / "one.tar.xz", tmp_path / "three.tar.xz")["differ"][0]["findings"]
    assert any("uid 0 vs 1000" in f["what"] and "builder's user" in f["what"] for f in found)


def test_a_zip_member_time_is_found(tmp_path):
    for name, when in (("a.zip", (2026, 10, 11, 12, 0, 0)), ("b.zip", (2026, 10, 11, 12, 0, 2))):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            z.writestr(zipfile.ZipInfo("app/main.js", when), "same")
    found = repro.compare(tmp_path / "a.zip", tmp_path / "b.zip")["differ"][0]["findings"]
    assert found[0]["where"] == "app/main.js" and "date_time" in found[0]["what"]


def test_files_made_by_only_one_build_are_listed(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "x").write_bytes(b"1")
    (tmp_path / "b" / "x").write_bytes(b"1")
    (tmp_path / "b" / "extra.debug").write_bytes(b"2")
    r = repro.compare(tmp_path / "a", tmp_path / "b")
    assert not r["identical"] and r["only_b"] == ["extra.debug"]


def test_the_command_line_exits_0_when_reproduced_and_3_when_not(tmp_path):
    (tmp_path / "a").write_bytes(b"same")
    (tmp_path / "b").write_bytes(b"same")
    (tmp_path / "c").write_bytes(b"different")
    run = lambda *x: subprocess.run([sys.executable, "-m", "fieldkit", "repro", "compare", *map(str, x)],
                                    capture_output=True, text=True)
    assert run(tmp_path / "a", tmp_path / "b").returncode == 0
    r = run(tmp_path / "a", tmp_path / "c", "--json")
    assert r.returncode == 3 and json.loads(r.stdout)["identical"] is False


def _pe(link_time, guid):
    head = bytearray(0x200)
    head[0:2] = b"MZ"
    head[0x3c:0x40] = (0x80).to_bytes(4, "little")
    head[0x80:0x84] = b"PE\0\0"
    head[0x88:0x8c] = link_time.to_bytes(4, "little")
    return bytes(head) + b"RSDS" + guid + (1).to_bytes(4, "little") + b"firefox.pdb\0"


def test_a_windows_binarys_link_time_and_pdb_signature_are_named(tmp_path):
    (tmp_path / "a.exe").write_bytes(_pe(1789000000, b"A" * 16))
    (tmp_path / "b.exe").write_bytes(_pe(1789000555, b"B" * 16))
    causes = repro.compare(tmp_path / "a.exe", tmp_path / "b.exe")["differ"][0]["findings"][0]["causes"]
    assert any("link time" in c and "/Brepro" in c for c in causes)
    assert any("PDB signature" in c for c in causes)


def test_large_files_are_compared_without_reading_them_byte_by_byte(tmp_path):
    big = bytes(range(256)) * (1 << 16)                     # 16 MB
    (tmp_path / "a").write_bytes(big)
    (tmp_path / "b").write_bytes(big[:-5] + b"XXXXX")
    f = repro.compare(tmp_path / "a", tmp_path / "b")["differ"][0]["findings"][0]
    assert f["first_difference_at"] == len(big) - 5 and f["bytes_differing"] == 5


def test_one_file_a_prefix_of_the_other_is_told_by_its_length(tmp_path):
    (tmp_path / "a").write_bytes(b"linux-image bytes")
    (tmp_path / "b").write_bytes(b"linux-image bytes, built Sun Oct 11 12:31:41 UTC 2026")
    f = repro.compare(tmp_path / "a", tmp_path / "b")["differ"][0]["findings"][0]
    assert f["first_difference_at"] == len(b"linux-image bytes") and f["size"] == [17, 53]
