"""Source-audit blind spots and the vendor host inventory (2026-10-02), on small fake source trees and fake builds."""
import io
import zipfile

from fieldkit.leakgate import audit, gate


def _tree(tmp_path, files):
    for rel, text in files.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return tmp_path


# each blind spot the independent audit found, reduced to the pattern that hid it
BLIND_SPOTS = {
    # MITM priming: an XHR opened with HEAD, in a directory no component covered
    "toolkit/actors/NetErrorParent.sys.mjs":
        'primeMitm(b) { let request = new XMLHttpRequest({ mozAnon: true }); request.open("HEAD", url); request.send(null); }',
    # application reputation: C++ channel through the IO service, then AsyncOpen
    "toolkit/components/reputationservice/ApplicationReputation.cpp":
        "rv = ios->NewChannel(serviceUrl, nullptr, nullptr, nullptr, getter_AddRefs(mChannel));\nrv = mChannel->AsyncOpen(this);",
    # DoH: DNS lookups and the Region fetch, no HTTP API at all
    "toolkit/components/doh/DoHHeuristics.sys.mjs": "request = Services.dns.asyncResolve(hostname, type, flags, null, listener);",
    "toolkit/components/doh/DoHConfig.sys.mjs": "await lazy.Region._fetchRegion();",
    # storage.sync: the Kinto HTTP client
    "toolkit/components/extensions/ExtensionStorageSyncKinto.sys.mjs":
        'KintoHttpClient: "resource://services-common/kinto-http-client.sys.mjs",',
    # ASRouter: remote image URLs in message definitions
    "browser/components/asrouter/modules/FeatureCalloutMessages.sys.mjs":
        'imageURL: "https://firefox-settings-attachments.cdn.mozilla.net/main-workspace/ms-images/x.svg",',
}


def test_every_blind_spot_is_now_inventoried(tmp_path):
    inv = audit.inventory(_tree(tmp_path, BLIND_SPOTS))
    assert sorted(inv) == sorted(BLIND_SPOTS)
    assert "open(METHOD)" in inv["toolkit/actors/NetErrorParent.sys.mjs"]["apis"]
    apis = inv["toolkit/components/reputationservice/ApplicationReputation.cpp"]["apis"]
    assert "NewChannel( (C++)" in apis and "AsyncOpen( (C++)" in apis
    assert "asyncResolve( (DNS)" in inv["toolkit/components/doh/DoHHeuristics.sys.mjs"]["apis"]
    assert "Region fetch" in inv["toolkit/components/doh/DoHConfig.sys.mjs"]["apis"]
    assert "Kinto client" in inv["toolkit/components/extensions/ExtensionStorageSyncKinto.sys.mjs"]["apis"]
    assert inv["browser/components/asrouter/modules/FeatureCalloutMessages.sys.mjs"]["apis"] == [
        "remote asset URL firefox-settings-attachments.cdn.mozilla.net"]


def test_each_new_pattern_on_its_own():
    cases = {
        'xhr.open("POST", u)': "open(METHOD)",
        "this._fetch(url, { method: 'HEAD' })": "method: HEAD/POST/PUT",
        "nsresult rv = NS_NewChannelWithTriggeringPrincipal(": "NS_NewChannel",
        "mIOService->NewChannelFromURI(uri)": "NewChannel( (C++)",
        "channel->AsyncOpen(listener)": "AsyncOpen( (C++)",
        "dns->AsyncResolveNative(host)": "asyncResolve( (DNS)",
        "Region.fetchRegion()": "Region fetch",
        "new lazy.Kinto({})": "Kinto client",
        '{"icon": "https://img.cdn.mozilla.net/a.png"}': "remote asset URL img.cdn.mozilla.net",
    }
    for text, label in cases.items():
        assert label in audit.network_apis(text), (text, audit.network_apis(text))


def test_no_new_false_hits_on_local_code():
    for text in ('keywords.fetch(x)', 'this.#fetch(y)', 'async fetch({a}) {', 'el.open = true', 'dialog.open("x")',
                 'let url = "https://www.mozilla.org/privacy/"', 'method: "GET"', "resolve(value)"):
        assert audit.network_apis(text) == [], text


def test_json_message_files_and_test_dirs(tmp_path):
    root = _tree(tmp_path, {
        "browser/components/asrouter/modules/messages.json": '{"image": "https://x.cdn.mozilla.net/i.svg"}',
        "toolkit/components/doh/test/browser_doh.js": "Services.dns.asyncResolve(h)",       # tests are skipped
        "toolkit/actors/LocalOnlyChild.sys.mjs": "export class LocalOnlyChild {}",          # no network: not inventoried
    })
    assert sorted(audit.inventory(root)) == ["browser/components/asrouter/modules/messages.json"]


def test_a_newly_inventoried_file_without_a_disposition_fails_source_policy(tmp_path):
    root = _tree(tmp_path, BLIND_SPOTS)
    approved = {"by": "owner", "how": "terminal"}
    disp = {f: {"disposition": "cut-in-source", "approval": approved} for f in BLIND_SPOTS}
    st = audit.static_audit(root, disp)
    assert st["unapproved"] == [] and st["undecided"] == [] and gate.source_messages(st) == []
    # one file newly inventoried, nobody decided it: SOURCE_POLICY fails and names it
    del disp["toolkit/actors/NetErrorParent.sys.mjs"]
    st = audit.static_audit(root, disp)
    msgs = gate.source_messages(st)
    assert st["undecided"] == ["toolkit/actors/NetErrorParent.sys.mjs"] and len(msgs) == 2
    assert "NetErrorParent" in msgs[1]
    # a disposition written but not approved: still a failure (unapproved), but not "undecided"
    disp["toolkit/actors/NetErrorParent.sys.mjs"] = {"disposition": "OPEN", "approval": None}
    st = audit.static_audit(root, disp)
    assert st["unapproved"] == ["toolkit/actors/NetErrorParent.sys.mjs"] and st["undecided"] == []
    assert len(gate.source_messages(st)) == 1


# ---------------------------------------------------------------------------------------------- vendor host inventory
def _ja(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, t in files.items():
            z.writestr(n, t)
    return b.getvalue()


def _install(tmp_path):
    inst = tmp_path / "build"
    (inst / "browser").mkdir(parents=True)
    (inst / "omni.ja").write_bytes(_ja({"modules/A.sys.mjs": 'u = "https://aus5.mozilla.org/update"; w = "wss://push.services.mozilla.com/";'
                                                             ' p = "http://plain.mozilla.org/"; e = "https://example.com/";'}))
    (inst / "browser" / "omni.ja").write_bytes(_ja({"chrome/b.json": '{"a": "https://safebrowsing.googleapis.com/v4", '
                                                                     '"b": "https://mozilla.cloudflare-dns.com/dns-query",'
                                                                     ' "c": "https://login.live.com/"}',
                                                    "img/x.png": "https://never.mozilla.org/ (not a text member)"}))
    return inst


def test_vendor_hosts_need_a_disposition_or_an_allowlist_entry(tmp_path):
    inst = _install(tmp_path)
    disp = {"host:aus5.mozilla.org": {"approval": {"by": "owner"}},
            "host:*.live.com": {"approval": {"by": "owner"}},
            "host:mozilla.cloudflare-dns.com": {"approval": None}}                 # written, never approved
    allow = {"entries": [{"kind": "dest", "values": ["*.googleapis.com"], "scenarios": ["drm-request"], "approval": {"by": "owner"}}]}
    bi = audit.binary_audit(inst, None, disp, allow)
    assert bi["vendor_hosts"] == ["aus5.mozilla.org", "login.live.com", "mozilla.cloudflare-dns.com", "push.services.mozilla.com",
                                  "safebrowsing.googleapis.com"]
    assert bi["vendor_unlisted"] == ["mozilla.cloudflare-dns.com", "push.services.mozilla.com"]
    assert bi["vendor_pending"] == ["mozilla.cloudflare-dns.com"]
    assert bi["vendor_where"]["push.services.mozilla.com"] == ["omni.ja:modules/A.sys.mjs"]
    msgs = gate.vendor_messages(bi)
    assert len(msgs) == 1 and "push.services.mozilla.com" in msgs[0] and "1 only pending" in msgs[0]
    # plain http (not https/wss), non-vendor hosts and non-text members are outside this check
    assert "plain.mozilla.org" not in bi["vendor_hosts"] and "example.com" not in bi["vendor_hosts"]
    assert "never.mozilla.org" not in bi["vendor_hosts"]


def test_vendor_check_passes_when_everything_is_decided(tmp_path):
    inst = _install(tmp_path)
    disp = {"host:*.mozilla.org": {"approval": {"by": "o"}}, "host:*.mozilla.com": {"approval": {"by": "o"}}, "host:*cloudflare-dns.com": {"approval": {"by": "o"}},
            "host:*.live.com": {"approval": {"by": "o"}}, "host:*.googleapis.com": {"approval": {"by": "o"}}}
    bi = audit.binary_audit(inst, None, disp)
    assert bi["vendor_unlisted"] == [] and gate.vendor_messages(bi) == []


def test_vendor_domain_suffix_matching():
    assert audit.vendor_domain("mozilla.org") and audit.vendor_domain("a.b.mozilla.net") and audit.vendor_domain("x.windows.net")
    assert not audit.vendor_domain("notmozilla.org") and not audit.vendor_domain("mozilla.org.evil.example")


def test_embedded_hosts_still_reports_xul_hosts_and_the_new_scheme_map(tmp_path):
    inst = _install(tmp_path)
    (inst / "xul.dll").write_bytes(b"\x00https://crash-reports.mozilla.com/submit\x00")
    schemes = {}
    hosts = audit.embedded_hosts(lambda rel: (inst / rel).read_bytes(), schemes=schemes)
    assert "crash-reports.mozilla.com" in hosts and "crash-reports.mozilla.com" not in schemes   # xul.dll: diff only
    assert schemes["push.services.mozilla.com"] == {"wss"} and schemes["plain.mozilla.org"] == {"http"}
