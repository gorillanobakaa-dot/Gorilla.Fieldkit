"""`fieldkit appcapture` (fieldkit/appcapture): proxy environment, CA bundle, redaction, PID-safe stop, a live capture."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from fieldkit import appcapture as ac
from fieldkit.appcapture import redact

TOK = "ya29.a0AfH6SMBxxxxxxxxxxxxxxxxxxxx"
REFRESH = "1//0gAbCdEfGhIjKlMnOpQrStUv"


def test_redaction_removes_every_token_shape():
    s = f'{TOK} {REFRESH} Bearer abcdefghij123 AIzaSyA-123456789012345678901 "access_token": "zzz" refresh_token=qqq'
    out = redact.text(s)
    for secret in (TOK, REFRESH, "abcdefghij123", "AIzaSyA-1234", '"zzz"', "qqq"):
        assert secret not in out
    assert "<redacted>" in out


def test_secret_headers_are_blanked_and_others_scrubbed():
    h = redact.headers({"Authorization": "Bearer x", "Cookie": "a=b", "X-Other": TOK, "User-Agent": "antigravity/hub/2.31.2"})
    assert h["Authorization"] == "<redacted>" and h["Cookie"] == "<redacted>"
    assert TOK not in h["X-Other"] and h["User-Agent"] == "antigravity/hub/2.31.2"


def test_clean_flow_never_carries_a_token():
    rec = {"method": "POST", "host": "h", "path": f"/x?access_token={REFRESH}", "req_headers": {"Authorization": "Bearer abcdefghijk"},
           "resp_headers": {}, "req_body": json.dumps({"t": TOK}), "resp_body": f"ok {TOK}", "status": 200, "req_len": 3}
    assert TOK not in json.dumps(ac.clean(rec)) and REFRESH not in json.dumps(ac.clean(rec))


def test_proxy_env_covers_go_node_python_and_electron():
    e = ac.proxy_env(1234, "/b.pem")
    assert e["HTTPS_PROXY"] == "http://127.0.0.1:1234" and e["SSL_CERT_FILE"] == "/b.pem"
    assert e["NODE_EXTRA_CA_CERTS"] == "/b.pem" and e["REQUESTS_CA_BUNDLE"] == "/b.pem"
    assert "--proxy-server=http://127.0.0.1:1234" in ac.app_args("electron", 1234)
    assert ac.app_args("go", 1234) == []


def test_bundle_is_system_cas_plus_mitm_ca(tmp_path):
    (tmp_path / "sys.pem").write_text("SYSTEM-CA\n")
    (tmp_path / "mitm.pem").write_text("MITM-CA\n")
    out = ac.build_bundle(tmp_path / "mitm.pem", tmp_path / "b.pem", system=tmp_path / "sys.pem").read_text()
    assert "SYSTEM-CA" in out and "MITM-CA" in out


def test_endpoints_and_select():
    recs = [{"method": "POST", "host": "a.example", "path": "/v1:fetchAvailableModels", "status": 200},
            {"method": "POST", "host": "a.example", "path": "/v1:fetchAvailableModels?x=1", "status": 200},
            {"method": "GET", "host": "b.example", "path": "/z", "status": 404}]
    assert ac.endpoints(recs)[0] == ("POST", "a.example", "/v1:fetchAvailableModels", [200], 2)
    assert len(ac.select(recs, host="b.example")) == 1 and len(ac.select(recs, path="fetchAvailable")) == 2


def test_stop_pid_refuses_a_reused_pid():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert ac.stop_pid(p.pid, psutil.Process(p.pid).create_time() + 500) == []       # wrong creation time: not ours
        assert p.poll() is None
        assert ac.stop_pid(p.pid, psutil.Process(p.pid).create_time()) == [p.pid]
        p.wait(timeout=5)
    finally:
        p.kill()


def test_main_process_is_the_launcher():
    found = [(5, 10.0, ["a"], 1), (6, 11.0, ["a", "--type=zygote"], 5), (7, 12.0, ["ls"], 5)]
    assert ac.main_process(found)[0] == 5 and ac.main_process([]) is None


def test_start_without_executable_or_mitm_says_so(tmp_path):
    assert "no executable" in ac.start("nope", root=tmp_path)["error"]
    assert ac.stop("nope", root=tmp_path)["ok"] is False


@pytest.mark.skipif(not shutil.which("mitmdump") or os.environ.get("CI") and sys.platform == "win32", reason="needs mitmdump")
def test_live_capture_of_a_fake_app_decrypts_and_redacts(tmp_path):
    app = tmp_path / "app" / ("fakeapp.cmd" if sys.platform == "win32" else "fakeapp")
    app.parent.mkdir()
    code = ("import urllib.request,time\n"
            "try:\n"
            f"  urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:1/x', data=b'{{\"project\":\"p\",\"t\":\"{TOK}\"}}'), timeout=3)\n"
            "except Exception: pass\n"
            "time.sleep(30)\n")
    if sys.platform == "win32":
        pytest.skip("shell shim not written for Windows CI")
    app.write_text(f"#!{sys.executable}\n{code}")
    app.chmod(0o755)
    res = ac.start("fake", exe=str(app), root=tmp_path / "scr", say=lambda m: None)
    try:
        assert res["ok"], res
        assert Path(res["bundle"]).read_text().count("BEGIN CERTIFICATE") > 1
        assert ac._same(res["mitm"]["pid"], res["mitm"]["ctime"])
        time.sleep(4)
    finally:
        done = ac.stop("fake", root=tmp_path / "scr")
    assert done["ok"] and not ac._same(res["mitm"]["pid"], res["mitm"]["ctime"])
    assert not ac._same(res["app_proc"]["pid"], res["app_proc"]["ctime"])
