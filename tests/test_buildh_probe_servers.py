"""probe_servers: the local pages a probe visits run only while the probe does, on 127.0.0.1, and every request they
receive comes back with the probe's lines (2026-10-04: the Satellite probes needed two scripts started by hand)."""
import http.client
import socket

import pytest

from fieldkit.buildh import probe, probe_servers


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def refused(port):
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_the_satellite_probes_declare_the_pages_they_need():
    assert probe_servers.declared(probe.resolve_js("satellite-mobile").read_text(encoding="utf-8")) == [("echo-ua", 8765)]
    assert probe_servers.declared(probe.resolve_js("satellite-revisit").read_text(encoding="utf-8")) == [("revisit", 8766)]
    assert probe_servers.declared(probe.resolve_js("design-tokens").read_text(encoding="utf-8")) == []


def test_the_echo_page_shows_navigator_and_logs_the_header_it_was_sent():
    page = probe_servers.Page("echo-ua")
    try:
        assert page.httpd.server_address[0] == "127.0.0.1"                    # loopback only, never 0.0.0.0
        r, body = get(page.port, "/level2", {"User-Agent": "Mozilla/5.0 (Android 14; Mobile)"})
        assert r.status == 200 and r.getheader("cache-control") == "no-store"
        assert b"document.title='NAV:'+navigator.userAgent" in body and b"<title>x</title>" in body
        [row] = page.log
        assert row["path"] == "/level2" and row["headers"]["user-agent"] == "Mozilla/5.0 (Android 14; Mobile)"
    finally:
        page.close()
    assert refused(page.port)


def test_the_revisit_page_says_must_revalidate_and_logs_every_request_with_its_cache_headers():
    page = probe_servers.Page("revisit")
    try:
        r, body = get(page.port, "/front-page")
        assert r.getheader("cache-control") == "private, max-age=0, must-revalidate"
        assert b"<title>revisit</title>" in body and len(body) > 20000
        get(page.port, "/front-page", {"Cache-Control": "max-age=0"})            # what Reload sends
        assert [x["path"] for x in page.log] == ["/front-page", "/front-page"]
        assert "cache-control" not in page.log[0]["headers"] and page.log[1]["headers"]["cache-control"] == "max-age=0"
    finally:
        page.close()
    with pytest.raises(ValueError):
        probe_servers.Page("nonesuch")


def test_a_busy_port_moves_the_page_and_the_probe_text_follows():
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        body = f'// gprobe-server: echo-ua {busy}\nconst u = "http://127.0.0.1:{busy}/x";\n'
        said = []
        with probe_servers.serving(body, say=said.append) as (new, pages):
            [page] = pages
            assert page.port != busy and f"127.0.0.1:{page.port}/x" in new and f"127.0.0.1:{busy}/x" not in new
            assert "is busy" in said[0]
            get(page.port, "/x")
        assert refused(page.port)                                                # stopped when the block ended


def test_a_probe_run_has_its_pages_up_while_the_browser_runs_and_reports_their_requests(monkeypatch, tmp_path):
    """run() with a fake copy and a fake browser that visits the declared page: the request is in the timeline, in
    time order with the probe's own lines, and the page is gone afterwards."""
    port = {}

    def fake_prepare(install_dir, js, wait=15, omni=None, files=None, added=None, subs=None, say=print, body=None):
        import re
        port["n"] = int(re.search(r"127\.0\.0\.1:(\d+)/", body).group(1))      # moved if 8765 was busy here
        (tmp_path / "copy").mkdir(exist_ok=True)
        return tmp_path / "copy", tmp_path / "copy" / "app", []

    def fake_launch(app, url="about:blank", timeout=90, headless=True, on_line=None):
        import time
        t0 = time.time()
        get(port["n"], "/level0", {"User-Agent": "UA-0"})
        return {"lines": ["before", "level0 | JS ran"], "times": [t0 - 1, time.time() + 1], "done": True, "seconds": 1.0}
    monkeypatch.setattr(probe, "prepare_copy", fake_prepare)
    monkeypatch.setattr(probe, "launch", fake_launch)
    r = probe.run(tmp_path, "satellite-mobile", say=lambda m: None)
    assert r["done"] and [q["path"] for q in r["requests"]] == ["/level0"]
    assert r["timeline"][0] == "before" and r["timeline"][2] == "level0 | JS ran"
    assert r["timeline"][1].startswith("SERVER [echo-ua 127.0.0.1:") and "user-agent: UA-0" in r["timeline"][1]
    assert refused(port["n"]) and not (tmp_path / "copy").exists()


def test_the_bad_cert_page_speaks_https_with_a_certificate_nobody_trusts():
    """2026-10-08: the certificate error page by its real cause (probe error-pages): HTTPS on 127.0.0.1 with a
    self-signed certificate made for the run, removed when the page stops."""
    import os
    import ssl
    import urllib.error
    import urllib.request
    from fieldkit.buildh import probe_servers as ps
    page = ps.Page("bad-cert")
    tmp = page._tmp
    try:
        try:
            urllib.request.urlopen(f"https://127.0.0.1:{page.port}/", timeout=5)
            raise AssertionError("a self-signed certificate was trusted")
        except urllib.error.URLError as e:
            assert "CERTIFICATE_VERIFY_FAILED" in str(e)
        body = urllib.request.urlopen(f"https://127.0.0.1:{page.port}/x", timeout=5,
                                      context=ssl._create_unverified_context()).read()
        assert b"bad-cert" in body and page.log[-1]["path"] == "/x"
    finally:
        page.close()
    assert not os.path.exists(tmp)
