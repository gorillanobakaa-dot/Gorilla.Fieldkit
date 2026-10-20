# Version: 1.0.0 · updated 26-10-18-23-40
"""D-158-13 to the end: no search data of an engine but Google in the source (owner, 2026-10-18: "remove everything
else from the source code as well ... a few more surface attacks")."""
import json

from fieldkit.buildh import searchstrip as ss


def world(tmp_path):
    t = tmp_path
    icons = t / ss.ICONS_DIR
    icons.mkdir(parents=True)
    recs = [{"id": "g1", "engineIdentifiers": ["google"]}, {"id": "p1", "engineIdentifiers": ["perplexity"]},
            {"id": "b1", "engineIdentifiers": ["bing", "bing-*"]}]
    (t / ss.ICONS_JSON).write_text(json.dumps({"data": recs}), encoding="utf-8")
    for r in recs:
        (icons / r["id"]).write_bytes(b"svg")
        (icons / f"{r['id']}.meta.json").write_text("{}")
    (t / ss.DUMPS_MOZBUILD).write_text('X += [\n' + "".join(f'    "search-config-icons/{r["id"]}",\n    "search-config-icons/{r["id"]}.meta.json",\n' for r in recs) + "]\n")
    wd = t / ss.WORDMARKS
    wd.mkdir(parents=True)
    for n in ("Google.svg", "Perplexity.svg", "Bing.svg"):
        (wd / n).write_text("<svg/>")
    (t / ss.JAR).parent.mkdir(parents=True, exist_ok=True)
    (t / ss.JAR).write_text("".join(f"  skin/classic/browser/urlbar/engine-wordmarks/{n} (x)\n" for n in ("Google.svg", "Perplexity.svg", "Bing.svg")))
    (t / ss.URLBAR_CSS).write_text('.a {\n  .searchmode-switcher[wordmark="google"] & {\n    x: 1;\n  }\n'
                                   '  .searchmode-switcher[wordmark="perplexity"] & {\n    x: 2;\n  }\n}\n')
    (t / ss.SWITCHER).parent.mkdir(parents=True, exist_ok=True)
    (t / ss.SWITCHER).write_text('const WORDMARK_ENGINE_FAMILIES = new Set([\n  "bing",\n  "google",\n  "perplexity",\n]);\n')
    return t


def test_only_google_is_left_and_the_lists_follow(tmp_path):
    t = world(tmp_path)
    assert not ss.check(t)["ok"]
    p = ss.apply(t)
    assert sorted(p["icon_records"]) == ["b1", "p1"] and len(p["icon_files"]) == 4 and len(p["wordmarks"]) == 2
    assert ss.check(t) == {"ok": True, "left": []}
    assert [r["id"] for r in json.loads((t / ss.ICONS_JSON).read_text())["data"]] == ["g1"]
    mb = (t / ss.DUMPS_MOZBUILD).read_text()
    assert "g1" in mb and "p1" not in mb and "b1" not in mb
    assert not (t / ss.WORDMARKS / "Perplexity.svg").exists() and (t / ss.WORDMARKS / "Google.svg").exists()
    css = (t / ss.URLBAR_CSS).read_text()
    assert 'wordmark="google"' in css and "perplexity" not in css
    assert '"perplexity"' not in (t / ss.SWITCHER).read_text()


def test_a_returning_engine_fails_the_check(tmp_path):
    t = world(tmp_path)
    ss.apply(t)
    (t / ss.WORDMARKS / "Perplexity.svg").write_text("<svg/>")       # a port brings one back
    c = ss.check(t)
    assert not c["ok"] and any("Perplexity" in x for x in c["left"])
