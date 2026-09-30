"""searchfox_tools against a local stand-in for searchfox.org (no network needed).

The stand-in answers in searchfox's nested JSON shape, so the flattening, the
Linux-only noise filter, the cache, the SVG verdict and the blast radius are
all exercised for real. One opt-in test (FIELDKIT_NETWORK=1) asks the real site.
"""
import http.server
import importlib.util
import json
import os
import threading
import urllib.parse
from pathlib import Path

import pytest

from fieldkit.core import settings

TOOL = Path(settings.expand("${FIELDKIT}")) / "toolbox" / "searchfox-tools" / "searchfox_tools.py"
pytestmark = pytest.mark.skipif(not TOOL.is_file(), reason="searchfox-tools not gathered")

INDEX = {   # query -> the files that mention it
    "PDMFactory": [("dom/media/platforms/PDMFactory.cpp", 12, "PDMFactory::PDMFactory()"),
                   ("dom/media/platforms/PDMFactory.h", 40, "class PDMFactory"),
                   ("widget/windows/nsWindow.cpp", 7, "PDMFactory* f"),                       # Windows noise
                   ("widget/android/Foo.cpp", 3, "PDMFactory")],                                # Android noise
    "symbol:PDMFactory": [("dom/media/platforms/PDMFactory.cpp", 12, "PDMFactory::PDMFactory()"),
                          ("dom/media/platforms/PDMFactory.cpp", 12, "PDMFactory::PDMFactory()")],  # duplicate
    "arrow-left.svg": [("browser/themes/shared/toolbarbuttons.css", 88, "url(arrow-left.svg)")],
}


@pytest.fixture
def sf(tmp_path, monkeypatch):
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["q"][0]
            hits.append(q)
            if q == "not-json":
                data, ctype = b"<html>maintenance</html>", "text/html"
            else:
                rows = INDEX.get(q, [])
                body = {"normal": {"Textual Occurrences": [
                    {"path": p, "lines": [{"lno": n, "line": line}]} for p, n, line in rows]}}
                data, ctype = json.dumps(body).encode(), "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location("searchfox_tools_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "BASE", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(mod, "MIN_INTERVAL", 0)
    mod.requests_seen = hits
    yield mod
    srv.shutdown()


def test_search_flattens_filters_platform_noise_and_dedups(sf):
    got = sf.search("PDMFactory")
    assert [p for p, _, _ in got] == ["dom/media/platforms/PDMFactory.cpp", "dom/media/platforms/PDMFactory.h"]
    assert len(sf.search("PDMFactory", linux_only=False)) == 4
    assert len(sf.search("symbol:PDMFactory")) == 1


def test_second_identical_search_is_served_from_the_cache(sf):
    sf.search("PDMFactory")
    sf.search("PDMFactory")
    assert sf.requests_seen.count("PDMFactory") == 1
    sf.search("PDMFactory", use_cache=False)
    assert sf.requests_seen.count("PDMFactory") == 2


def test_svg_keeplist_verdicts(sf):
    verdict, files = sf.svg_keeplist("chrome/icons/arrow-left.svg")
    assert verdict.startswith("KEEP") and files == ["browser/themes/shared/toolbarbuttons.css"]
    verdict, files = sf.svg_keeplist("never-used.svg")
    assert verdict.startswith("SAFE TO SHIM") and files == []


def test_blast_radius_counts_per_file(sf):
    r = sf.blast_radius("PDMFactory")
    assert list(r.items())[0] == ("dom/media/platforms/PDMFactory.cpp", 2)
    assert "widget/android/Foo.cpp" not in r


def test_a_non_json_answer_is_a_clear_error(sf):
    with pytest.raises(RuntimeError, match="did not return JSON"):
        sf.search("not-json")


@pytest.mark.skipif(os.environ.get("FIELDKIT_NETWORK") != "1", reason="opt-in: asks the real searchfox.org")
def test_live_searchfox_finds_a_well_known_symbol(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("searchfox_live", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "cache")
    assert any(p.endswith("nsIContentPolicy.idl") for p, _, _ in mod.search("nsIContentPolicy", limit=500))
