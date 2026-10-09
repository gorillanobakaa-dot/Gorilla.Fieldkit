"""removed-pages (2026-10-09, D-157-40): every page the register removes lands on 'address not valid' in the browser
itself, and its own chrome:// file opens nothing."""
import yaml

from fieldkit.buildh import removedpages as rp


def _reg(tmp_path):
    f = tmp_path / "ABOUT-PAGES.yaml"
    f.write_text(yaml.safe_dump({"pages": [{"name": "asrouter", "verdict": "remove"}, {"name": "neterror", "verdict": "keep"},
                                           {"name": "crashparent", "verdict": "remove"}]}), encoding="utf-8")
    return f


def test_removed_reads_the_register(tmp_path):
    assert rp.removed(_reg(tmp_path)) == ["asrouter", "crashparent"]


def test_the_probe_gets_its_addresses():
    js = rp.probe_file(["about:asrouter", "chrome://x/y.html"]).read_text(encoding="utf-8")
    assert 'const ADDRESSES = ["about:asrouter", "chrome://x/y.html"];' in js


def test_judge_passes_only_when_every_address_is_gone():
    names, files = ["asrouter", "crashparent"], ["chrome://browser/content/blockedSite.xhtml"]
    lines = ["GONE|about:asrouter|about:neterror?e=malformedURI&u=about%3Aasrouter|Hmm. That address doesn't look right.",
             "GONE|about:crashparent|about:neterror?e=malformedURI&u=about%3Acrashparent|Hmm.",
             "GONE|chrome://browser/content/blockedSite.xhtml|about:neterror?e=fileNotFound&u=chrome|",
             "GONE-DONE|3"]
    got, done = rp.parse(lines)
    assert all(r["ok"] for r in rp.judge(names, files, got, done))
    lines[0] = "GONE|about:asrouter|about:asrouter|You must enable..."                         # the page came back
    lines[2] = "GONE|chrome://browser/content/blockedSite.xhtml|chrome://browser/content/blockedSite.xhtml|Go back"
    got, done = rp.parse(lines)
    rows = rp.judge(names, files, got, done)
    assert not rows[0]["ok"] and "about:asrouter" in rows[0]["evidence"]
    assert not rows[1]["ok"] and "blockedSite" in rows[1]["evidence"]


def test_a_short_run_fails_closed(tmp_path):
    got, done = rp.parse(["GONE|about:asrouter|about:neterror?e=malformedURI|"])
    assert not rp.judge(["asrouter", "crashparent"], [], got, done)[0]["ok"]
    assert not rp.rows("C:/nowhere", tmp_path / "none.yaml")[0]["ok"]
