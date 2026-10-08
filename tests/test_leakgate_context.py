"""leakgate-context: each host a disposition is about, found in the install's own omni.ja archives with path:line and
context (2026-10-04: build 26's vendor hosts were reviewed from extracted folders with a throwaway script)."""
import json
import zipfile

from fieldkit import cli
from fieldkit.leakgate import audit


def fake_install(tmp_path):
    inst = tmp_path / "install"
    (inst / "browser").mkdir(parents=True)
    with zipfile.ZipFile(inst / "omni.ja", "w") as z:
        z.writestr("modules/Relay.sys.mjs", "a\n| google.com.ar | https://accounts.google.com.ar |\n")
        z.writestr("greprefs.js", 'pref("x", "accounts.google.com/o/oauth2/");\n')
        z.writestr("res/logo.png", b"\x89PNG accounts.google.com")
    with zipfile.ZipFile(inst / "browser" / "omni.ja", "w") as z:
        z.writestr("chrome/shims/artstationLogin.js",
                   "// signs in through accounts.google.com, which needs access\n"
                   'const STORAGE_ACCESS_ORIGIN = "https://accounts.google.com";\n'
                   "const other = 'myaccounts.google.com';\n")
    return inst


def test_every_occurrence_with_its_line_and_not_a_longer_name(tmp_path):
    inst = fake_install(tmp_path)
    ctx = audit.host_context(inst, ["accounts.google.com", "ads.example.org"], width=20,
                             where={"accounts.google.com": ["xul.dll", "browser/omni.ja:chrome/shims/artstationLogin.js"]})
    rows = ctx["accounts.google.com"]
    assert {"where": "xul.dll", "binary": True} in rows
    found = [(r["where"], r["line"]) for r in rows if not r.get("binary")]
    assert found == [("omni.ja:greprefs.js", 1), ("browser/omni.ja:chrome/shims/artstationLogin.js", 1),
                     ("browser/omni.ja:chrome/shims/artstationLogin.js", 2)]       # not .com.ar, not myaccounts., not the PNG
    assert ctx["ads.example.org"] == []
    text = "\n".join(audit.context_lines(ctx))
    assert 'artstationLogin.js:2: ...S_ORIGIN = "https://accounts.google.com";...' in text and "?? not found" in text


def test_the_hosts_to_review_come_from_a_run_or_a_reviewer_list():
    bh = {"new": ["n.example", "ok.example"], "unapproved_new": ["n.example"], "vendor_unlisted": ["v.mozilla.org"],
          "where": {"n.example": ["omni.ja:a.js"]}, "vendor_where": {"v.mozilla.org": ["xul.dll"]}}
    assert audit.hosts_to_review(bh) == (["n.example", "v.mozilla.org"], {"n.example": ["omni.ja:a.js"], "v.mozilla.org": ["xul.dll"]})
    assert audit.hosts_to_review([{"host": "a.example", "where": ["omni.ja:x.js"], "set": "vendor"}, "b.example"]) == \
        (["a.example", "b.example"], {"a.example": ["omni.ja:x.js"]})


def test_the_command_takes_hosts_or_a_hosts_file_and_exits_3_when_one_is_not_found(tmp_path, capsys):
    inst = fake_install(tmp_path)
    assert cli.main(["build-harness", "leakgate-context", "t1", "accounts.google.com", "width=10",
                     "--install-dir", str(inst)]) == 0
    assert "greprefs.js:1:" in capsys.readouterr().out
    hf = tmp_path / "hosts.json"
    hf.write_text(json.dumps([{"host": "accounts.google.com", "where": []}, {"host": "gone.example", "where": []}]), encoding="utf-8")
    assert cli.main(["build-harness", "leakgate-context", "t1", f"hosts={hf}", "--install-dir", str(inst)]) == 3
    assert "===== gone.example" in capsys.readouterr().out
