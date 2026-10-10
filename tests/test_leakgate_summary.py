"""The leak gate scene by scene (2026-10-10): built from the run's own log, every scene explained in plain words, and
on every release page."""
from fieldkit.leakgate import summary as s

LOG = ("leakgate: build 1 (157.0), packets ON, repeat 3\n"
       "  startup-idle [direct] run 1/3: 300 s + 60 s after shutdown\n"
       "  startup-idle [proxied] run 1/3: 300 s + 60 s after shutdown\n"
       "  newtab [direct] run 1/3: 60 s + 10 s after shutdown\n")


def test_the_table_comes_from_the_log(tmp_path):
    log = tmp_path / "run.log"
    log.write_bytes(LOG.encode("utf-16"))                       # the launcher writes UTF-16 (Tee-Object)
    res = tmp_path / "run.result.json"
    res.write_text('{"exit_code": 0, "minutes": 239.9, "started": "2026-10-06T10:08:14", "ended": "2026-10-06T14:08:07"}',
                   encoding="utf-8-sig")
    r = s.run(log, res, "20261004224302")
    assert list(r["scenes"]) == ["startup-idle", "newtab"] and r["scenes"]["startup-idle"]["runs"] == 2
    md = r["markdown"]
    assert md.startswith(s.HEADING) and "| `startup-idle` |" in md and "**3**" in md and "PASS" in md
    assert "10:08 to 14:08" in md and not r["missing"]


def test_an_unexplained_scene_is_named():
    md, missing = s.render(s.scenes("  brand-new-scene [direct] run 1/3: 30 s + 5 s after shutdown\n"))
    assert missing == ["brand-new-scene"] and "(not explained)" in md


def test_the_release_page_must_carry_it(tmp_path):
    from fieldkit.buildh import releasecover as rc
    reg = tmp_path / "reg.yaml"
    reg.write_text("decisions: []\n", encoding="utf-8")
    rows = rc.check("no table here", reg, "", "2026-10-04")
    assert any(r["check"].endswith("scene by scene") and not r["ok"] for r in rows)
    rows = rc.check(s.HEADING + "\n...", reg, "", "2026-10-04")
    assert all(r["ok"] for r in rows if r["check"].endswith("scene by scene"))
