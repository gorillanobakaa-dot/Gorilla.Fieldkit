"""fieldkit web: HTTP first, the browser only for a blocked or empty page; telemetry off; no download path; doctor;
the throwaway profile is gone afterwards. No network, no browser: Selenium and the HTTP reader are faked."""
import os
import sys
import types

import pytest

from fieldkit.web import browser, cli, doctor, rule

GOOD = {"status": 200, "has_text": True, "text_chars": 5000, "challenge": False, "title": "T", "final_url": "https://x/"}


@pytest.fixture(autouse=True)
def scratch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "state"))
    for k in browser.TELEMETRY_ENV:
        monkeypatch.delenv(k, raising=False)
    return tmp_path / "scratch"


PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]       # noqa: E731


class FakeDriver:
    def __init__(self, profile, text="Rendered body " * 30):
        self.profile, self.text, self.quit_called = profile, text, False
        self.current_url, self.title = "https://x/final", "Rendered"
        assert profile.is_dir()

    def set_page_load_timeout(self, s):
        self.timeout = s

    def get(self, url):
        self.got = url

    def execute_script(self, js):
        return self.text

    def quit(self):
        self.quit_called = True


# -- the rule -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("patch,reason", [
    ({}, ""), ({"status": 403}, "HTTP 403"), ({"status": 429}, "HTTP 429"), ({"status": 404}, ""),
    ({"challenge": True}, "bot check page"), ({"has_text": False}, "no text in the answer"),
    ({"text_chars": 50}, "only 50 characters of text")])
def test_rule(patch, reason):
    assert rule.blocked_or_empty(GOOD | patch) == reason


# -- auto: HTTP first -----------------------------------------------------------------------------------------
def test_auto_uses_http_when_page_is_fine():
    calls = []
    r = cli.fetch("https://x/", http=lambda u, t: (GOOD, "plain text"),
                  browse=lambda *a, **k: calls.append(1))
    assert r["via"] == "http" and r["text"] == "plain text" and not calls


def test_auto_goes_to_browser_on_blocked_page():
    seen = {}

    def browse(url, browser, timeout):
        seen["browser"] = browser
        return {"title": "B", "final_url": url, "text": "x" * 500}
    r = cli.fetch("https://x/", http=lambda u, t: (GOOD | {"status": 403}, ""), browse=browse)
    assert r["via"] == "browser" and r["reason"] == "HTTP 403" and seen["browser"] in ("firefox", "safari")


def test_auto_goes_to_browser_on_network_failure():
    def http(u, t):
        raise OSError("reset")
    r = cli.fetch("https://x/", http=http, browse=lambda u, browser, timeout: {"title": "", "final_url": u, "text": "y" * 300})
    assert r["via"] == "browser" and "reset" in r["reason"]


def test_robots_refusal_is_obeyed_not_bypassed():
    from fieldkit.evidence.fetch import Refused

    def http(u, t):
        raise Refused("robots.txt disallows")
    with pytest.raises(Refused):
        cli.fetch("https://x/", http=http, browse=lambda *a, **k: pytest.fail("browser must not run"))


def test_explicit_browser_skips_http():
    r = cli.fetch("https://x/", "firefox", http=lambda *a: pytest.fail("no http"),
                  browse=lambda u, browser, timeout: {"title": "t", "final_url": u, "text": "z"})
    assert r["via"] == "browser" and r["reason"] == ""


# -- the browser: telemetry, no download, throwaway profile ---------------------------------------------------
def test_fetch_sets_telemetry_env_and_removes_profile(scratch_env):
    made = []

    def factory(name, cfg, profile):
        d = FakeDriver(profile)
        made.append(d)
        return d
    got = browser.fetch("https://x/", cfg={}, factory=factory, resolve=PUBLIC)
    assert os.environ["SE_AVOID_STATS"] == "true" and os.environ["SE_OFFLINE"] == "true"
    assert got["title"] == "Rendered" and made[0].quit_called
    assert not made[0].profile.exists()
    assert list(scratch_env.iterdir()) == []                        # the whole job folder is gone


def test_profile_removed_when_the_page_fails(scratch_env):
    class Boom(FakeDriver):
        def get(self, url):
            raise RuntimeError("timeout\nstack")
    with pytest.raises(browser.BrowserError, match="RuntimeError: timeout"):
        browser.fetch("https://x/", cfg={}, factory=lambda n, c, p: Boom(p), resolve=PUBLIC)
    assert list(scratch_env.iterdir()) == []


def test_no_selenium_manager_path(tmp_path, monkeypatch):
    """Selenium starts its Manager (download + stats) only when not given the driver and the binary. Prove both are
    always passed, and that a missing path stops before Selenium is even imported."""
    ff, gd = tmp_path / "firefox.exe", tmp_path / "geckodriver.exe"
    ff.write_text("x")
    gd.write_text("x")
    rec = {}

    class Opts:
        def __init__(self):
            self.args, self.prefs = [], {}
        add_argument = lambda self, a: self.args.append(a)                      # noqa: E731
        set_preference = lambda self, k, v: self.prefs.__setitem__(k, v)        # noqa: E731

    class Service:
        def __init__(self, executable_path=None, **kw):
            rec["driver"] = executable_path

    def Firefox(service, options):
        rec["binary"], rec["options"] = options.binary_location, options
        return "driver"
    wd = types.SimpleNamespace(FirefoxOptions=Opts, Firefox=Firefox)
    sel = types.ModuleType("selenium")
    sel.webdriver = wd
    mods = {"selenium": sel, "selenium.webdriver": wd, "selenium.webdriver.firefox": types.ModuleType("f"),
            "selenium.webdriver.firefox.service": types.SimpleNamespace(Service=Service)}
    monkeypatch.setattr(sys, "modules", sys.modules | mods)
    drv = browser._selenium_driver("firefox", {"firefox": str(ff), "geckodriver": str(gd)}, tmp_path / "p")
    assert drv == "driver" and rec["driver"] == str(gd) and rec["binary"] == str(ff)
    assert "-headless" in rec["options"].args and str(tmp_path / "p") in rec["options"].args
    assert rec["options"].prefs["toolkit.telemetry.enabled"] is False
    with pytest.raises(browser.BrowserError, match="geckodriver"):
        browser.driver_paths({"firefox": str(ff)})
    with pytest.raises(browser.BrowserError, match="not a file"):
        browser.driver_paths({"firefox": str(ff), "geckodriver": str(tmp_path / "gone")})


def test_missing_config_fails_before_anything_is_made(scratch_env):
    with pytest.raises(browser.BrowserError, match="browser_fetch"):
        browser.fetch("https://x/", cfg={}, resolve=PUBLIC)
    assert not scratch_env.exists() or list(scratch_env.iterdir()) == []


def test_private_address_refused():
    local = lambda h, p: [(2, 1, 6, "", ("127.0.0.1", p))]                      # noqa: E731
    with pytest.raises(browser.BrowserError, match="not a public address"):
        browser.fetch("http://localhost/", cfg={}, factory=lambda *a: None, resolve=local)
    with pytest.raises(browser.BrowserError, match="http"):
        browser.check_public("file:///etc/passwd")


# -- doctor ---------------------------------------------------------------------------------------------------
def test_doctor_lists_missing_with_exact_commands():
    rows = doctor.check(cfg={}, has_module=lambda n: False, key="windows-x64")
    byname = {r["item"]: r for r in rows}
    assert not any(r["ok"] for r in rows)
    assert f"selenium=={doctor.PINS['selenium']}" in byname["python package selenium"]["fix"]
    gd = byname["geckodriver"]["fix"]
    assert "geckodriver-v0.37.1-win64.zip" in gd and doctor.PINS["geckodriver"]["assets"]["windows-x64"]["sha256"] in gd


def test_doctor_install_refuses_without_a_person():
    rows = doctor.check(cfg={}, has_module=lambda n: False, key="linux-x64")
    done, why = doctor.install(rows, isatty=False, download=lambda *a: pytest.fail("no download"),
                               run=lambda *a, **k: pytest.fail("no pip"))
    assert done == [] and why.startswith("REFUSED") and "geckodriver" in why


def test_doctor_install_asks_and_declining_installs_nothing():
    rows = doctor.check(cfg={}, has_module=lambda n: True, key="linux-x64")
    done, why = doctor.install(rows, isatty=True, ask=lambda p: "n", say=lambda *a: None,
                               download=lambda *a: pytest.fail("declined"))
    assert done == [] and why == ""


def test_install_checks_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "tools_dir", lambda: tmp_path / "tools")
    with pytest.raises(browser.BrowserError, match="sha256 mismatch"):
        doctor.install_geckodriver("linux-x64", download=lambda url, dest: dest.write_bytes(b"not it"),
                                   say=lambda *a: None)
    assert not (tmp_path / "tools").exists()


def test_pins_cover_every_asset():
    for key, a in doctor.PINS["geckodriver"]["assets"].items():
        assert len(a["sha256"]) == 64 and a["file"].startswith("geckodriver-v0.37.1-"), key
