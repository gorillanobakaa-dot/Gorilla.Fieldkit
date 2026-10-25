"""The tools captured from the Gorilla OpenCode v0.1.152 Windows release (2026-10-25), other than the pipeline:
the release table and `release verify` (fieldkit/build/relfiles.py), the LM Studio log reader (fieldkit/lmstudio.py),
and mutate's refusal of an argument Git Bash rewrote (fieldkit/method/mutate.py).

What is held: a table is only printed from files hashed again and found equal; a page row that differs in size or
hash, a missing row and an extra row are each named; a download is checked against SHA256SUMS and GitHub's digests
and a program outside SHA256SUMS is a problem; verify fails when a linux-install job did not succeed; the log reader
gives each request's prompt tokens, prefill and generation speed, and names a Vulkan device lost with its fix;
mutate refuses a swap that arrived as C:/Program Files/Git/usr/... and runs nothing.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from fieldkit import lmstudio
from fieldkit.build import relfiles
from fieldkit.method import mutate
from tests.test_goapp_windows_release import DEB, GOOD_DICT


exec(DEB)                                       # deb(payload, dict_size): a real .deb with data.tar.xz


def _release(tmp_path, dict_size=GOOD_DICT):
    d = tmp_path / "rel"
    d.mkdir()
    files = {"app.exe": b"MZ program", "app_1.0_amd64.deb": deb(b"ELF app", dict_size)}
    for n, b in files.items():
        (d / n).write_bytes(b)
    sums = "".join(f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items())
    # LF as published: on Windows write_text would write CRLF, which check_download names as a problem (bef462c)
    (d / "SHA256SUMS-v1.0.txt").write_text(sums, encoding="utf-8", newline="\n")
    return d, files


# -- the table -----------------------------------------------------------------------------------------------
def test_the_table_is_measured_and_refuses_a_changed_file(tmp_path):
    d, files = _release(tmp_path)
    t = relfiles.table(d / "SHA256SUMS-v1.0.txt")
    assert t.splitlines()[0] == "| File | Bytes | SHA-256 |"
    assert f"| `app.exe` | 10 | `{hashlib.sha256(files['app.exe']).hexdigest()}` |" in t
    (d / "app.exe").write_bytes(b"MZ changed!")
    with pytest.raises(ValueError, match="does not match"):
        relfiles.table(d / "SHA256SUMS-v1.0.txt")
    (d / "app.exe").unlink()
    with pytest.raises(ValueError, match="not in"):
        relfiles.table(d / "SHA256SUMS-v1.0.txt")


def test_a_page_table_that_differs_is_named_row_by_row(tmp_path):
    d, files = _release(tmp_path)
    sums = relfiles.parse_sums(d / "SHA256SUMS-v1.0.txt")
    sizes = {n: len(b) for n, b in files.items()}
    good = relfiles.table(d / "SHA256SUMS-v1.0.txt")
    assert relfiles.check_table("intro\n\n" + good + "\nmore", sums, sizes) == []
    assert relfiles.check_table(good.replace("| 10 |", "| 12 |"), sums, sizes) == [
        "app.exe: page says 12 bytes, the file has 10"]
    wrong = good.replace(sums["app.exe"], "0" * 64)
    assert any("app.exe: page says 000000000000" in x for x in relfiles.check_table(wrong, sums, sizes))
    one = "\n".join(l for l in good.splitlines() if ".deb" not in l)
    assert relfiles.check_table(one, sums, sizes) == ["app_1.0_amd64.deb: in SHA256SUMS, no row on the page"]
    assert relfiles.check_table("no table here", sums, sizes) == ["no 'File | Bytes | SHA-256' table found"]


def test_sums_lines_are_strict(tmp_path):
    assert relfiles.parse_sums("a" * 64 + "  x\n" + "B" * 64 + " *y\n") == {"x": "a" * 64, "y": "b" * 64}
    with pytest.raises(ValueError, match="line 1"):
        relfiles.parse_sums("not a sum line\n")
    with pytest.raises(ValueError, match="twice"):
        relfiles.parse_sums("a" * 64 + "  x\n" + "b" * 64 + "  x\n")


def test_a_download_is_checked_against_sums_and_github_digests(tmp_path):
    d, files = _release(tmp_path)
    assets = [{"name": n, "digest": "sha256:" + hashlib.sha256(b).hexdigest()} for n, b in files.items()]
    assets += [{"name": "SHA256SUMS-v1.0.txt"}, {"name": "v1.0-release-notes.md"}]
    assert relfiles.check_download(d, assets)["ok"]
    (d / "extra.bin").write_bytes(b"x")
    r = relfiles.check_download(d, assets + [{"name": "extra.bin"}])
    assert r["problems"] == ["extra.bin: published but not in SHA256SUMS-v1.0.txt"]
    assets[0]["digest"] = "sha256:" + "0" * 64
    assert "GitHub's digest" in relfiles.check_download(d, assets)["problems"][0]
    (d / "app.exe").write_bytes(b"other")
    assert any("do not match" in p for p in relfiles.check_download(d, assets)["problems"])


def test_a_checksum_file_with_windows_line_ends_is_a_problem(tmp_path):
    """v0.1.152's SHA256SUMS had CRLF: verify passed it, the Mac's shasum -c refused it."""
    d, files = _release(tmp_path)
    assets = [{"name": n, "digest": "sha256:" + hashlib.sha256(b).hexdigest()} for n, b in files.items()]
    sums = d / "SHA256SUMS-v1.0.txt"
    sums.write_bytes(sums.read_bytes().replace(b"\n", b"\r\n"))
    r = relfiles.check_download(d, assets)
    assert not r["ok"] and any("Windows line ends" in p for p in r["problems"])


def _fake_gh(tmp_path, files, jobs):
    """A gh stand-in: release view/download from `files`, workflow run, run list/view with `jobs`."""
    store = tmp_path / "published"
    store.mkdir()
    for n, b in files.items():
        (store / n).write_bytes(b)
    page = relfiles.table(store / "SHA256SUMS-v1.0.txt") if (store / "SHA256SUMS-v1.0.txt").is_file() else ""
    assets = [{"name": n, "size": len(b), "digest": "sha256:" + hashlib.sha256(b).hexdigest()} for n, b in files.items()]
    script = tmp_path / "gh.py"
    script.write_text(f'''import sys, json, shutil, pathlib
a = sys.argv[1:]
log = pathlib.Path({str(tmp_path / "calls.jsonl")!r})
with log.open("a") as f: f.write(json.dumps(a) + "\\n")
if a[:2] == ["release", "view"]:
    print(json.dumps({{"assets": {assets!r}, "body": {page!r}, "isDraft": False}}))
elif a[:2] == ["release", "download"]:
    for p in pathlib.Path({str(store)!r}).iterdir(): shutil.copy(p, a[a.index("-D") + 1])
elif a[:2] == ["workflow", "run"]:
    pass
elif a[:2] == ["run", "list"]:
    print(json.dumps([{{"databaseId": 77, "createdAt": "2999-01-01T00:00:00Z"}}]))
elif a[:2] == ["run", "view"]:
    print(json.dumps({{"status": "completed", "conclusion": "success", "url": "u",
                      "jobs": [{{"name": k, "conclusion": v}} for k, v in {jobs!r}.items()]}}))
''', encoding="utf-8")
    gh = tmp_path / ("gh.cmd" if sys.platform == "win32" else "gh")
    gh.write_text(f'@"{sys.executable}" "{script}" %*\n' if sys.platform == "win32" else
                  f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    gh.chmod(0o755)
    return str(gh)


def test_verify_downloads_checks_and_reads_the_linux_install_jobs(tmp_path):
    d, files = _release(tmp_path)
    files["SHA256SUMS-v1.0.txt"] = (d / "SHA256SUMS-v1.0.txt").read_bytes()
    gh = _fake_gh(tmp_path, files, {"deb-ubuntu": "success", "rpm-fedora": "success"})
    r = relfiles.verify("v1.0", "o/r", tmp_path / "dl", gh=gh, wait=5)
    assert r["ok"], r["problems"]
    assert r["workflow"]["run_id"] == 77 and r["workflow"]["jobs"]["rpm-fedora"] == "success"
    calls = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert ["workflow", "run", "linux-install.yml", "-R", "o/r", "-f", "tag=v1.0"] in calls
    with pytest.raises(ValueError, match="not empty"):             # downloads only into a new empty folder
        relfiles.verify("v1.0", "o/r", tmp_path / "dl", gh=gh)


def test_verify_fails_when_a_linux_install_job_failed(tmp_path):
    d, files = _release(tmp_path)
    files["SHA256SUMS-v1.0.txt"] = (d / "SHA256SUMS-v1.0.txt").read_bytes()
    gh = _fake_gh(tmp_path, files, {"deb-ubuntu": "success", "rpm-fedora": "failure"})
    r = relfiles.verify("v1.0", "o/r", tmp_path / "dl", gh=gh, wait=5)
    assert not r["ok"] and r["problems"] == ["linux-install.yml job rpm-fedora: failure"]
    r = relfiles.verify("v1.0", "o/r", tmp_path / "dl2", gh=gh, dispatch=False)
    assert r["ok"] and r["workflow"] is None and "not run" in r["problems_not_checked"]


def test_verify_passes_every_published_deb_through_xzcheck(tmp_path):
    """v0.1.152's .deb (dict 8 MiB for 59.7 MiB) is what this catches; here the same at a small scale."""
    d, files = _release(tmp_path, dict_size=8 << 20)                 # 8 MiB for a 10 KiB tar: memory for nothing
    files["SHA256SUMS-v1.0.txt"] = (d / "SHA256SUMS-v1.0.txt").read_bytes()
    gh = _fake_gh(tmp_path, files, {})
    r = relfiles.verify("v1.0", "o/r", tmp_path / "dl", gh=gh, dispatch=False)
    assert not r["ok"] and len(r["problems"]) == 1
    assert r["problems"][0].startswith("xzcheck: app_1.0_amd64.deb:data.tar.xz: dictionary 8 MiB is bigger")
    assert [x["file"] for x in r["xz"]] == ["app_1.0_amd64.deb"]
    assert any("xz FAIL app_1.0_amd64.deb:data.tar.xz: dict 8 MiB" in l for l in relfiles.lines(r))
    good = tmp_path / "good"
    good.mkdir()
    (good / "a.deb").write_bytes(deb(b"ELF app", GOOD_DICT))
    (good / "b.deb").write_bytes(b"!<arch>\nnot a deb")               # unreadable is a problem, never a pass
    res, bad = relfiles.check_xz(good, ["a.deb", "b.deb", "app.exe"])
    assert [x["file"] for x in res] == ["a.deb", "b.deb"] and [x["ok"] for x in res] == [True, False]
    assert bad == ["xzcheck: " + res[1]["problems"][0]] and "not checked" in bad[0]


# -- the LM Studio log -----------------------------------------------------------------------------------------
LOG = """[2026-10-25 00:21:25][INFO][google/gemma-4-12b-qat] Running chat completion on conversation with 2 messages.
[2026-10-25 00:21:31][DEBUG] 200.00.743.732 I slot launch_slot_: id  2 | task 2554 | processing task, is_child = 0
[2026-10-25 00:21:44][DEBUG] 200.13.331.523 I slot print_timing: id  2 | task 2554 | prompt processing, n_tokens =   2048, progress = 0.16, t =  12.59 s / 162.70 tokens per second
[2026-10-25 00:27:22][DEBUG] 205.51.849.420 I slot print_timing: id  2 | task 2554 | prompt eval time =  263029.97 ms / 12990 tokens (   20.25 ms per token,    49.39 tokens per second)
205.51.849.446 I slot print_timing: id  2 | task 2554 |        eval time =   88075.23 ms /   326 tokens (  271.00 ms per token,     3.69 tokens per second)
205.51.849.449 I slot print_timing: id  2 | task 2554 |       total time =  351105.20 ms / 13316 tokens
[2026-10-25 00:27:22][DEBUG] 205.51.850.457 I slot      release: id  2 | task 2554 | stop processing: n_tokens = 13315, truncated = 0
[2026-10-25 00:46:42][INFO][google/gemma-4-12b-qat] Running chat completion on conversation with 2 messages.
[2026-10-25 00:46:43][DEBUG] 1.0 I slot launch_slot_: id  2 | task 3419 | processing task, is_child = 0
[2026-10-25 00:50:00][DEBUG] 2.0 I slot print_timing: id  2 | task 3419 | prompt processing, n_tokens =  12288, progress = 0.94, t =  200.00 s / 61.44 tokens per second
[2026-10-25 00:50:34][DEBUG] 229.03.417.568 E ggml_vulkan: device lost on Vulkan0
[2026-10-25 00:50:34][DEBUG] 229.03.423.433 E slot      iterate: id  2 | task 3419 | got exception: vk::Device::getFenceStatus: ErrorDeviceLost
229.03.423.473 I slot      release: id  2 | task 3419 | stop processing: n_tokens = 13063, truncated = 0
[2026-10-25 00:51:35][DEBUG] 0.11.041.417 I srv  llama_server: model loaded
[2026-10-25 00:52:00][DEBUG] 0.2 I slot launch_slot_: id  0 | task 2554 | processing task, is_child = 0
[2026-10-25 00:52:10][DEBUG] 0.3 I slot print_timing: id  0 | task 2554 | prompt eval time =    1187.58 ms /     5 tokens (  237.52 ms per token,     4.21 tokens per second)
"""


def test_the_log_gives_each_requests_speeds_and_names_a_lost_device(tmp_path):
    d = lmstudio.parse(LOG.splitlines())
    first, lost, after = d["requests"]
    assert (first["prompt_tokens"], first["prefill_tps"], first["gen_tokens"], first["gen_tps"]) == (12990, 49.39, 326, 3.69)
    assert first["n_tokens"] == 13315 and first["messages"] == 2 and round(first["prefill_s"]) == 263
    assert first["model"] == "google/gemma-4-12b-qat" and not first["device_lost"]
    assert lost["device_lost"] and lost["prompt_tokens"] is None and lost["prefill_progress"]["tps"] == 61.44
    # task ids restart after a reload: the same id is a new request, never merged into the old one
    assert after["task"] == 2554 and after["prompt_tokens"] == 5 and after["start"] == "2026-10-25 00:52:00"
    assert len(d["lost"]) == 1 and d["lost"][0]["task"] == 3419 and "--gpu off" in d["lost"][0]["fix"]
    text = "\n".join(lmstudio.lines(d))
    assert "prefill 49.4 tok/s (263 s)" in text and "gen 326 tok 3.69 tok/s" in text and "DEVICE LOST" in text
    assert "longer reading the prompt than writing" in text and "FIX:" in text
    p = tmp_path / "2026-10-25.1.log"
    p.write_text(LOG, encoding="utf-8")
    assert lmstudio.main([str(p)]) == 3                                  # a lost device is not a clean log
    p.write_text(LOG.split("[2026-10-25 00:46:42]")[0], encoding="utf-8")
    assert lmstudio.main([str(p)]) == 0
    assert lmstudio.main([str(tmp_path / "missing.log")]) == 2


def test_the_newest_log_is_the_default(tmp_path):
    import os
    for i, name in enumerate(["2026-10/2026-10-24.1.log", "2026-10/2026-10-25.1.log"]):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))
    assert [x.name for x in lmstudio.default_logs(root=tmp_path)] == ["2026-10-25.1.log"]
    assert [x.name for x in lmstudio.default_logs("2026-10-24", root=tmp_path)] == ["2026-10-24.1.log"]


# -- mutate and Git Bash --------------------------------------------------------------------------------------
ROOTS = ["C:/Program Files/Git"]


def test_a_swap_git_bash_rewrote_is_refused_with_the_fix(tmp_path):
    f = tmp_path / "pkg.py"
    f.write_text('MODE = ("/usr/share/x/setup.sh", 0o755)\n', encoding="utf-8")
    text = f.read_text()
    old = '"C:/Program Files/Git/usr/share/x/setup.sh", 0o755'
    assert mutate.msys_rewritten(old, text, ROOTS) == '"/usr/share/x/setup.sh", 0o755'
    assert mutate.msys_rewritten('"/usr/share/x/setup.sh", 0o755', text, ROOTS) is None      # typed right
    assert mutate.msys_rewritten('"D:/Tools/Git/usr/share/x/setup.sh", 0o755', text, []) == '"/usr/share/x/setup.sh", 0o755'
    marker = tmp_path / "ran"
    test = [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"]
    r = mutate.mutate([{"file": str(f), "swap": [old, '"/usr/share/x/setup.sh", 0o644'], "from_argv": True}],
                      test, roots=ROOTS)
    assert not r["ok"] and r["refused"] == 1 and "MSYS_NO_PATHCONV=1" in r["results"][0]["detail"]
    assert not marker.exists() and f.read_text() == text                  # nothing ran, nothing changed
    # the NEW text rewritten while the OLD is fine: also refused (the mutation would insert a path nobody typed)
    r = mutate.mutate([{"file": str(f), "swap": ["0o755", "0o755  # C:/Program Files/Git/tmp"], "from_argv": True}],
                      test, roots=ROOTS)
    assert r["refused"] == 1 and "NEW" in r["results"][0]["detail"]
    # a --plan file is read by Python, never rewritten: no refusal for its text
    r = mutate.mutate([{"file": str(f), "swap": ["0o755", "0o644"]}], test, roots=ROOTS)
    assert r["refused"] == 0 and marker.exists()
