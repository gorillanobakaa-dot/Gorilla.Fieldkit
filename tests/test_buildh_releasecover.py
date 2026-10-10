"""release-cover (2026-10-09): the release page names every decision made since the last release, fits GitHub's
125,000-character limit, and carries the hidden pages on the page when D-157-40 is new; the hidden pages' release
edition takes four plain-words sections per page word for word."""
import json

import pytest
import yaml

from fieldkit.buildh import releasecover as rc, hiddendocs as hd
from fieldkit.leakgate.summary import HEADING as LG


def _register(tmp_path):
    f = tmp_path / "decisions" / "PRODUCT-DECISIONS.yaml"
    f.parent.mkdir(parents=True)
    f.write_text(yaml.safe_dump({"decisions": [
        {"id": "D-157-33", "decided": "2026-10-04", "status": "enforced", "title": "satellite"},
        {"id": "D-157-34", "decided": "2026-10-07", "status": "enforced", "title": "brand voice"},
        {"id": "D-157-40", "decided": "2026-10-08", "status": "enforced", "title": "hidden pages"},
        {"id": "D-157-29", "decided": "2026-10-03", "status": "enforced", "title": "older"},
        {"id": "D-157-99", "decided": "2026-10-08", "status": "retired", "title": "dropped"},
    ]}), encoding="utf-8")
    return tmp_path


def test_due_is_what_the_previous_page_does_not_name_since_its_day(tmp_path):
    owner = _register(tmp_path)
    got = rc.due(owner / "decisions" / "PRODUCT-DECISIONS.yaml", "... D-157-33 ...", "2026-10-04")
    assert [i for i, _ in got] == ["D-157-34", "D-157-40"]          # D-157-33 already told; older and retired out


def test_a_page_that_misses_a_decision_or_the_hidden_pages_fails(tmp_path):
    owner = _register(tmp_path)
    reg = owner / "decisions" / "PRODUCT-DECISIONS.yaml"
    rows = {r["check"]: r for r in rc.check("D-157-34 only", reg, "D-157-33", "2026-10-04")}
    assert not rows["release page: every decision since the last release is on it"]["ok"]
    assert "D-157-40" in rows["release page: every decision since the last release is on it"]["evidence"]
    assert not rows["release page: carries the hidden pages, on the page (D-157-40)"]["ok"]
    good = "D-157-34 D-157-40\n" + rc.HIDDEN_HEADING + "\n" + LG + "\n"
    assert all(r["ok"] for r in rc.check(good, reg, "D-157-33", "2026-10-04"))
    big = good + "x" * rc.LIMIT
    assert not {r["check"]: r for r in rc.check(big, reg, "D-157-33", "2026-10-04")}["release page: fits GitHub's limit"]["ok"]


def test_run_reads_the_previous_release_with_gh_and_fails_closed(tmp_path):
    owner = _register(tmp_path)
    page = tmp_path / "PAGE.md"
    page.write_text("D-157-34 D-157-40\n" + rc.HIDDEN_HEADING + "\n" + LG, encoding="utf-8")

    class R:
        returncode, stderr = 0, ""
        stdout = json.dumps({"tagName": "v157.0-win64", "publishedAt": "2026-10-04T22:48:03Z", "body": "D-157-33"})
    rows = rc.run(owner, page, run_cmd=lambda *a, **k: R())
    assert all(r["ok"] for r in rows) and "v157.0-win64 (2026-10-04)" in rows[0]["evidence"]
    assert not rc.run(owner, tmp_path / "missing.md")[0]["ok"]

    class Bad:
        returncode, stdout, stderr = 1, "", "not logged in"
    assert not rc.run(owner, page, run_cmd=lambda *a, **k: Bad())[0]["ok"]


def test_the_release_edition_keeps_four_sections_word_for_word():
    layman = ("**What it is.** A page.\n\n**When you would meet it.** Never.\n\n**What you see.** Black.\n\n"
              "**Does it talk to anyone?** No one.\n\n**What we did, and why.** Removed.\n\n"
              "**What it costs you.** Nothing.\n\n**The lesson.** Look.")
    secs = hd.sections(layman)
    assert secs["What it is"] == "**What it is.** A page." and secs["The lesson"] == "**The lesson.** Look."
    entries = {"x": {"name": "x", "shots": ["about-x.png"], "en": {"title": "The x page", "layman": layman, "developer": "d"}}}
    reg = {"x": {"verdict": "remove", "decision": "D-157-40", "network": "none"}}
    text = hd.render_release({"order": ["x"], "en": {"release_intro": "Intro."}}, entries, reg)
    assert text.startswith("## What's new: the hidden pages") and "Intro." in text
    assert "**Does it talk to anyone?** No one." in text and "**What it costs you.** Nothing." in text
    assert "Never." not in text and "Look." not in text                     # the other sections stay in the full text
    assert f"{hd.RAW_URL}/docs/screenshots/hidden-pages/about-x.png" in text
    assert f"{hd.REPO_URL}/blob/master/docs/HIDDEN-PAGES.md#about-x" in text
    del entries["x"]["en"]["layman"]
    entries["x"]["en"]["layman"] = "**What it is.** A page."
    with pytest.raises(hd.Refused):
        hd.render_release({"order": ["x"]}, entries, reg)
