"""fieldkit accept: the judges read the disk, a false claim of success is a MISS, a missing program is SKIP.
Mutation plan: tests/accept_mutations.yaml."""
import subprocess

from fieldkit import accept


def job(i):
    return next(j for j in accept.jobs() if j["id"] == i)


def doer(fn):
    return lambda prompt, folder, model, timeout: fn(prompt, folder) or "Done, all finished."


def test_suite_has_the_everyday_jobs():
    ids = {j["id"] for j in accept.jobs()}
    assert {"see-file", "delete-file", "move-file", "tail-file", "find-file", "grep-text", "unzip", "json-read",
            "json-edit", "download", "image-convert", "ocr", "probe-video"} <= ids


def test_delete_passes_only_when_the_file_is_gone():
    ok = accept.run_job(job("delete-file"), "m", agent=doer(lambda p, f: (f / "junk.tmp").unlink()))
    assert ok["result"] == "PASS"


def test_false_claim_of_delete_is_a_miss():
    r = accept.run_job(job("delete-file"), "m", agent=doer(lambda p, f: None))
    assert r["result"] == "MISS" and "still exists" in r["reason"]


def test_delete_that_also_removes_the_other_file_is_a_miss():
    def both(p, f):
        (f / "junk.tmp").unlink()
        (f / "keep.txt").unlink()
    r = accept.run_job(job("delete-file"), "m", agent=doer(both))
    assert r["result"] == "MISS" and "keep.txt" in r["reason"]


def test_move_that_copies_is_a_miss():
    def copy(p, f):
        (f / "archive").mkdir()
        (f / "archive" / "report.txt").write_text("r\n")
    r = accept.run_job(job("move-file"), "m", agent=doer(copy))
    assert r["result"] == "MISS" and "copied" in r["reason"]


def test_move_passes():
    def mv(p, f):
        (f / "archive").mkdir()
        (f / "report.txt").rename(f / "archive" / "report.txt")
    assert accept.run_job(job("move-file"), "m", agent=doer(mv))["result"] == "PASS"


def test_answer_judge_reads_the_reply():
    assert accept.run_job(job("see-file"), "m", agent=lambda *a: "it says marmalade")["result"] == "PASS"
    assert accept.run_job(job("see-file"), "m", agent=lambda *a: "I could not open it")["result"] == "MISS"


def test_json_edit_must_keep_other_keys():
    import json

    def edit(keep):
        def f(p, folder):
            (folder / "data.json").write_text(json.dumps({"count": 7, **({"name": "x"} if keep else {})}))
        return f
    assert accept.run_job(job("json-edit"), "m", agent=doer(edit(True)))["result"] == "PASS"
    assert accept.run_job(job("json-edit"), "m", agent=doer(edit(False)))["result"] == "MISS"


def test_unzip_setup_makes_a_real_zip_and_judge_reads_the_content():
    def un(p, f):
        import zipfile
        zipfile.ZipFile(f / "bundle.zip").extractall(f / "out")
    assert accept.run_job(job("unzip"), "m", agent=doer(un))["result"] == "PASS"


def test_download_job_serves_a_local_file_and_fills_the_url():
    import urllib.request

    def get(prompt, f):
        url = prompt.split()[1]
        assert url.startswith("http://127.0.0.1:")
        (f / "hello.txt").write_bytes(urllib.request.urlopen(url).read())
    assert accept.run_job(job("download"), "m", agent=doer(get))["result"] == "PASS"


def test_missing_program_is_skip_not_pass():
    r = accept.run_job(job("ocr"), "m", agent=lambda *a: "HELLO42", which=lambda n: None)
    assert r["result"] == "SKIP" and "tesseract" in r["reason"]


def test_any_of_needs():
    assert accept.missing_needs(job("image-convert"), which=lambda n: n == "convert") == []
    assert accept.missing_needs(job("image-convert"), which=lambda n: None) == ["magick", "convert"]


def test_timeout_is_a_miss():
    def slow(*a):
        raise subprocess.TimeoutExpired("x", 1)
    assert accept.run_job(job("see-file"), "m", agent=slow)["result"] == "MISS"


def test_report_counts_per_model_and_os():
    rows = [{"model": "a", "os": "linux", "result": "PASS"}, {"model": "a", "os": "linux", "result": "MISS"},
            {"model": "a", "os": "windows", "result": "SKIP"}]
    t = accept.report(rows)
    assert t[("a", "linux")] == {"pass": 1, "miss": 1, "skip": 0, "error": 0}
    assert t[("a", "windows")]["skip"] == 1


def test_temp_folders_are_removed():
    seen = []
    accept.run_job(job("see-file"), "m", agent=lambda p, f, m, t: seen.append(f) or "MARMALADE")
    assert seen and not seen[0].exists()


def test_miss_row_keeps_what_the_model_ran():
    reply = "I did it.\n--- what actually ran (recorded) ---\nbash: del junk.tmp -> error"
    row = accept.run_job(job("see-file"), "m", agent=lambda p, f, m, t: reply)
    assert row["result"] == "MISS" and "bash: del junk.tmp" in row["ran"]
    assert accept.ran_record("plain reply") == ""


def test_cloud_model_carries_only_its_own_provider(tmp_path, monkeypatch):
    import json
    home = tmp_path / "home"
    d = home / ".config" / "gorilla-opencode"
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"providers": {"cloudA": {"apiKey": "k1"}, "cloudB": {"apiKey": "k2"}}}))
    (d / "cloudA-oauth.json").write_text("{}")
    monkeypatch.setattr(accept.Path, "home", lambda: home)
    accept.isolated_env(tmp_path / "e", "cloudA.some-model")
    out = tmp_path / "e" / "config" / "gorilla-opencode"
    assert json.loads((out / "config.json").read_text())["providers"] == {"cloudA": {"apiKey": "k1"}}
    assert (out / "cloudA-oauth.json").exists()
    accept.isolated_env(tmp_path / "f", "local.some-model")
    assert "providers" not in json.loads((tmp_path / "f" / "config" / "gorilla-opencode" / "config.json").read_text())


def test_setup_png_is_a_valid_png(tmp_path):
    import struct
    import zlib
    accept._make(tmp_path / "a.png", "png:")
    data = (tmp_path / "a.png").read_bytes()
    pos = 8
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        body = data[pos + 4:pos + 8 + n]
        assert struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(body)
        pos += 12 + n
