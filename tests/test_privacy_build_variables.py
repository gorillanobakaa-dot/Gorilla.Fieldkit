"""A build system's @VAR@ substitution token is not an address (2026-11-16, the Firefox 160 patch set push);
an address or a handle written in capitals still is."""
from fieldkit.core import privacy as p


def test_a_mozilla_manifest_token_is_not_an_email():
    assert p.scan_text("@BINPATH@/@DLL_PREFIX@Microsoft.UI.Windowing.Core@DLL_SUFFIX@", []) == []  # privacy-scan: allow (the installed scanner predates this rule)


def test_capital_addresses_and_handles_still_count():
    for t in ("JOHN@corp.com", "write to @JOHN@mastodon.social", "DLL_PREFIX@corp.com"):  # privacy-scan: allow (fakes)
        assert [h["kind"] for h in p.scan_text(t, [])] == ["email"], t
