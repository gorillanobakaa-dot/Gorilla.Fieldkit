"""about-pages (2026-10-08): every about: page read on a copy of the install; the verdict fails a listed page that
shows nothing (about:studies hung on a Normandy wait), real script errors, requests not opened by an allowed
extension, a Gorilla under 64 px and a dead about:glean; Mozilla artwork is listed as open; runs are compared."""
import json

from fieldkit.buildh import aboutpages as ap

LINES = [
    "ABOUT|studies|content|about:studies|about:studies|0|1|0|listed",
    "ABOUT-TEXT|studies|",
    "ABOUT-PIC|studies|chrome://branding/content/about-logo.png|1366x683",
    "ABOUT|telemetry|parent|BLOCKED about:neterror?e=blockedByPolicy|Blocked Gorilla Page|180|1|1|listed",
    "ABOUT-PIC|telemetry|chrome://global/skin/illustrations/security-error.svg|131x129",
    "ABOUT|certificate|content|about:certificate|about:certificate|0|0|533|listed",
    "ABOUT|neterror|content|BLOCKED about:neterror|Problem loading page|583|0|14|hidden",
    "ABOUT|robots|parent|about:robots|Gort!|362|1|1|listed",
    "ABOUT-PIC|robots|chrome://branding/content/about-logo.png|32x32",
    "ABOUT|glean|parent|about:glean|About Glean|582|0|39|listed",
    "GLEAN-MENU|about-glean|shown|496|About Glean ...",
    "GLEAN-MENU|profiler|NOT SHOWN|0|",
    "GLEAN-SUBMIT|Manual Testing ...|0",
    "ABOUT-ERR|neterror|ReferenceError: RPMGetBoolPref is not defined|chrome://global/content/aboutNetErrorHelpers.mjs",
    "ABOUT-ERR|support|AbortError: Actor 'ShieldFrame' destroyed before query 'Shield:GetStudiesEnabled' was resolved|",
    "ABOUT-NET|about|https://ublockorigin.pages.dev/filters/badware.min.txt|extension " + ap.UBLOCK_ID,
    "ABOUT-NET|glean|https://incoming.telemetry.mozilla.org/submit|browser",
    "ABOUT-SUMMARY|6|2|2|2|2",
]


def rows_by_check(p):
    return {r["check"].split(": ", 1)[1]: r for r in ap.verdict(p)}


def test_parse_reads_pages_pictures_errors_requests_and_glean():
    p = ap.parse(LINES)
    assert p["pages"]["telemetry"]["landed"].startswith("BLOCKED")
    assert p["pages"]["robots"]["pics"] == [{"url": "chrome://branding/content/about-logo.png", "w": 32, "h": 32}]
    assert p["pages"]["neterror"]["errors"][0]["msg"].startswith("ReferenceError")
    assert p["orphan_errors"]["support"][0]["msg"].startswith("AbortError")      # about:support was never listed here
    assert p["net"][1] == {"page": "glean", "url": "https://incoming.telemetry.mozilla.org/submit", "who": "browser"}
    assert p["glean"]["submit"]["requests"] == 0 and p["summary"]["pages"] == 6


def test_verdict_fails_what_is_broken_and_lists_artwork_as_open():
    r = rows_by_check(ap.parse(LINES))
    blank = r["every listed page shows its text"]
    assert not blank["ok"] and "about:studies" in blank["evidence"] and "certificate" not in blank["evidence"]
    errs = r["no script errors"]
    assert not errs["ok"] and "ShieldFrame" in errs["evidence"] and "RPMGetBoolPref" not in errs["evidence"]
    net = r["no request opened by a page or the browser"]
    assert not net["ok"] and "incoming.telemetry" in net["evidence"] and "ublockorigin" not in net["evidence"]
    assert not r["no Gorilla under 64 px (D-157-35)"]["ok"]                       # robots draws it at 32 px
    assert not r["about:glean menus and submit"]["ok"]                             # the profiler entry shows nothing
    art = r["Mozilla artwork still drawn (OPEN: artwork sweep)"]
    assert art["ok"] and "security-error.svg" in art["evidence"]


def test_a_clean_run_passes():
    p = ap.parse(["ABOUT|about|parent|about:about|About About|884|0|44|listed",
                  "ABOUT-NET|about|https://ublockorigin.github.io/x.txt|extension " + ap.UBLOCK_ID,
                  "ABOUT-SUMMARY|1|0|0|0|1"])
    assert all(r["ok"] for r in ap.verdict(p))


def test_compare_names_what_changed():
    a = ap.parse(LINES)
    b = ap.parse([l.replace("ABOUT|studies|content|about:studies|about:studies|0|", "ABOUT|studies|content|about:studies|Studies|240|")
                  for l in LINES if not l.startswith(("ABOUT|glean", "GLEAN", "ABOUT-PIC|telemetry"))]
                 + ["ABOUT-PIC|telemetry|chrome://branding/content/about-logo.png|131x129"])
    ch = ap.compare(a, b)
    assert "pages gone: about:glean" in ch
    assert "about:studies shows text again: 0 -> 240 characters" in ch
    assert any("telemetry draws a new picture: chrome://branding/content/about-logo.png" in l for l in ch)
    assert any("telemetry no longer draws: chrome://global/skin/illustrations/security-error.svg" in l for l in ch)


def test_previous_is_the_newest_run_of_another_build(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "STATE", tmp_path)
    for stamp, build in (("20261008-100000", "B27"), ("20261008-110000", "B28"), ("20261008-120000", "B28")):
        (tmp_path / f"about-pages-{stamp}.json").write_text(json.dumps({"build_id": build, "when": stamp, "pages": {}, "net": []}))
    assert ap.previous("B28")["when"] == "20261008-100000"
    assert ap.previous("B29")["when"] == "20261008-120000"
    assert ap.previous("B29", before=tmp_path / "about-pages-20261008-120000.json")["when"] == "20261008-110000"


def test_the_probe_sets_a_dead_proxy_before_reading_pages():
    js = (ap.Path(ap.__file__).parent / "probes" / "about-pages.js").read_text(encoding="utf-8")
    assert js.index('"network.proxy.type", 1') < js.index("for (const name of names)")
    assert 'startsWith("crash")' in js                                             # never the crash pages


def test_a_probe_that_read_nothing_or_broke_fails_closed():
    r = ap.verdict(ap.parse(["error ReferenceError: PromiseDebugging is not defined @gprobe.cfg:75:5"]))
    assert r[0]["check"] == "about-pages: the probe read the pages" and not r[0]["ok"] and "PromiseDebugging" in r[0]["evidence"]
    r = ap.verdict(ap.parse(["ABOUT|about|parent|about:about|About About|884|0|44|listed"]))     # no summary: cut short
    assert not r[0]["ok"] and "MISSING" in r[0]["evidence"]


def test_an_unhandled_rejection_is_an_error_with_its_place():
    p = ap.parse(["ABOUT|preferences|parent|about:preferences|Settings|272|0|9|listed",
                  "ABOUT-REJ|preferences|undefined|chrome://browser/content/preferences/main.js:120 init",
                  "ABOUT-SUMMARY|1|0|0|1|0"])
    errs = {r["check"]: r for r in ap.verdict(p)}["about-pages: no script errors"]
    assert not errs["ok"] and "main.js:120" in errs["evidence"]


def test_rejections_while_a_tab_closes_are_reported_not_failed():
    p = ap.parse(["ABOUT|preferences|parent|about:preferences|Settings|272|0|9|listed",
                  "ABOUT-ERR|preferences (closing)|uncaught exception: undefined|",
                  "ABOUT-REJ|preferences (closing)|undefined|(no stack)",
                  "ABOUT-SUMMARY|1|0|0|1|0"])
    r = {x["check"]: x for x in ap.verdict(p)}
    assert r["about-pages: no script errors"]["ok"]
    assert r["about-pages: promises rejected while a tab closed (no reason, no script stack)"]["evidence"] == "about:preferences x2"
    p2 = ap.parse(["ABOUT|preferences|parent|about:preferences|Settings|272|0|9|listed",
                   "ABOUT-REJ|preferences|undefined|(no stack)", "ABOUT-SUMMARY|1|0|0|1|0"])      # while OPEN: fails
    assert not {x["check"]: x for x in ap.verdict(p2)}["about-pages: no script errors"]["ok"]


def test_a_missing_fluent_message_fails_and_is_named():
    p = ap.parse(["ABOUT|preferences|parent|about:preferences|Settings|272|0|9|listed",
                  "ABOUT-L10N|preferences|settings-keyboard-shortcuts-group|moz-fieldset",
                  "ABOUT-SUMMARY|1|0|0|0|0"])
    r = {x["check"]: x for x in ap.verdict(p)}["about-pages: every Fluent message a page names exists (else the element shows nothing)"]
    assert not r["ok"] and "settings-keyboard-shortcuts-group (moz-fieldset)" in r["evidence"]


def test_walk_rewrites_the_probe_constants():
    src = ap.probe_file(only=("studies",), walk=True, dwell=6000).read_text(encoding="utf-8")
    assert 'const ONLY = ["studies"];' in src and "const WALK = true;" in src and "const DWELL = 6000;" in src
    assert ap.probe_file() == ap.Path(ap.__file__).parent / "probes" / "about-pages.js"   # untouched by default


def test_live_lines_say_what_matters():
    assert ap.live("ABOUT-NOW|studies") == "\n>> about:studies"
    assert "Mozilla artwork" in ap.live("ABOUT-PIC|telemetry|chrome://global/skin/illustrations/security-error.svg|131x129")
    assert ap.live("ABOUT-PIC|addons|chrome://global/skin/icons/settings.svg|16x16") is None        # plain icons: quiet
    assert "OPENED, NOT ALLOWED" in ap.live("ABOUT-NET|glean|https://incoming.telemetry.mozilla.org/x|browser")
    assert "allowed" in ap.live("ABOUT-NET|about|https://ublockorigin.github.io/x|extension " + ap.UBLOCK_ID)
    assert ap.live("ABOUT-ERR|certerror|ReferenceError: RPMGetBoolPref is not defined|x.mjs").startswith("   error (known:")
    assert "MISSING TEXT" in ap.live("ABOUT-L10N|preferences|x-id|moz-button")


def test_prebuild_reads_a_copy_with_the_trees_changes_and_is_never_a_comparison_base(monkeypatch, tmp_path):
    from fieldkit.buildh import compile as cg, install as inst, probe
    rec = tmp_path / "build-record.json"
    rec.write_text('{"head": "abc1234567"}', encoding="utf-8")
    monkeypatch.setattr(cg, "_record_path", lambda tid: rec)
    monkeypatch.setattr(inst, "find_install", lambda: None)
    assert ap.prebuild({"workdir": "w"}, "t", say=lambda m: None) is None                 # nothing to read against
    monkeypatch.setattr(inst, "find_install", lambda: tmp_path / "inst")
    monkeypatch.setattr(inst, "installed", lambda d: {"build_id": "20261008103725"})
    monkeypatch.setattr(probe, "tree_since", lambda w, since, target, out: ({"omni.ja:x.js": tmp_path / "x.js"}, [("a.cpp", "compiled")]))
    seen = {}
    def run(target, bid, say=print, partial=False, **kw):
        seen.update(partial=partial, omni=kw.get("omni"), bid=bid)
        return {"rows": [{"check": "c", "ok": True, "evidence": ""}]}
    monkeypatch.setattr(ap, "run", run)
    r = ap.prebuild({"workdir": "w"}, "t", say=lambda m: None)
    assert r["ok"] and seen["partial"] and seen["omni"] == {"omni.ja:x.js": str(tmp_path / "x.js")}
    assert any("a.cpp" in n for n in r["notes"])
