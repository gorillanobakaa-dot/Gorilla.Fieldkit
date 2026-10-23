# Version: 1.0.0 · updated 26-10-23-22-10
"""D-158-22 (owner 2026-10-23): no Learn more and no link to Mozilla a person can click. The post-install row reads
the mozilla-links probe and fails on any link, any link preference or any page it could not read; the technique's
watch catches a link preference a new release adds, and leaves service endpoints alone."""
import re

from fieldkit.buildh import install, mozlinks, techniques


def _run(lines, done=True):
    return lambda *a, **k: {"lines": lines, "done": done}


def test_zero_links_on_every_page_passes():
    r = mozlinks.rows("/inst", run=_run(["PAGE|about:preferences|0|3", "PAGE|Help > About|0|5", "LINKS|verdict|ok|0"]))
    assert r[0]["ok"] and "0 links to Mozilla on 2 page(s)" in r[0]["evidence"]


def test_a_visible_link_a_pref_or_an_unread_page_fails():
    r = mozlinks.rows("/inst", run=_run(["LINK|about:addons|support link|https://support.mozilla.org/x",
                                          "PAGE|about:addons|1|4", "LINKS|verdict|FAIL|1"]))
    assert not r[0]["ok"] and "about:addons" in r[0]["evidence"]
    r = mozlinks.rows("/inst", run=_run(["PREF|app.support.baseURL|https://support.mozilla.org/", "LINKS|verdict|FAIL|1"]))
    assert not r[0]["ok"] and "app.support.baseURL" in r[0]["evidence"]
    r = mozlinks.rows("/inst", run=_run(["PAGE|about:logins|error x|0", "LINKS|verdict|ok|0"]))
    assert not r[0]["ok"] and "unreadable" in r[0]["evidence"]
    assert not mozlinks.rows("/inst", run=_run([], done=False))[0]["ok"]


def test_the_watch_catches_a_new_link_pref_and_not_an_endpoint():
    t = next(x for x in techniques.TECHNIQUES if x["id"] == "T-158-22-A")
    rx = re.compile(next(s for s in t["signals"] if s["kind"] == "watch")["regex"])
    assert rx.search('pref("app.support.baseURL", "https://support.mozilla.org/1/firefox/");')
    assert rx.search('pref("browser.newfeature.learnMoreUrl", "https://www.mozilla.org/x");')
    assert not rx.search('pref("app.support.baseURL", "", locked);')
    assert not rx.search('pref("identity.fxaccounts.remote.root", "https://accounts.firefox.com/");')
    assert not rx.search('pref("toolkit.telemetry.server", "https://incoming.telemetry.mozilla.org");')


def test_post_install_knows_the_group():
    assert "mozlinks" in install.PROOF_CHECKS
