"""fieldkit update: what is behind, and the one rule it is built around.

THE RULE: "could not check" must never look like "up to date". Every test that
matters here is a test of that, because the failure it prevents is silent.
"""
import pytest

from fieldkit import update as U


def fake_run(table):
    """A runner that answers from a table of (first-arg-match -> (rc, text))."""
    def run(cmd, timeout=120):
        key = " ".join(str(c) for c in cmd)
        for match, answer in table.items():
            if match in key:
                return answer
        return (127, f"no fake for {key}")
    return run


# ------------------------------------------------------------ version maths
def test_versions_compare_by_number_not_by_text():
    """0.1.132 is older than 0.1.147, and 9 is older than 155.

    String comparison gets both of these wrong, and the second one bit this
    estate for real: a Firefox source tree named firefox-9.0 would sort above
    firefox-155.0 and be picked as the newest.
    """
    assert U.is_newer("0.1.147", "0.1.132")
    assert not U.is_newer("0.1.132", "0.1.147")
    assert U.is_newer("firefox-155.0", "firefox-9.0")
    assert U.is_newer("v157.0-win64", "155.0-3")


def test_a_tag_with_a_v_prefix_still_compares():
    assert not U.is_newer("v0.1.147", "0.1.147")
    assert U.is_newer("v0.1.148", "0.1.147")


# ------------------------------------------------------- could not check
def test_a_broken_apt_is_unknown_and_never_ok(monkeypatch):
    """The whole point. An empty list from a broken apt reads like a tidy machine."""
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/apt-get")
    r = U.apt_pending(run=fake_run({"apt-get": (100, "E: Could not open lock file")}))
    assert r["status"] == U.UNKNOWN
    assert r["items"] == []
    assert "lock file" in r["why"]


def test_a_missing_apt_is_unknown_not_a_clean_bill(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: None)
    r = U.apt_pending(run=fake_run({}))
    assert r["status"] == U.UNKNOWN and "not on this machine" in r["why"]


def test_github_unreachable_is_unknown_not_current(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    row = U.github_state({"package": "gorilla-opencode", "repo": "o/r"},
                         run=fake_run({"dpkg-query": (0, "0.1.132"),
                                       "gh release": (1, "could not resolve host")}))
    assert row["status"] == U.UNKNOWN
    assert row["installed"] == "0.1.132"
    assert "could not resolve host" in row["why"]


def test_no_gh_command_is_unknown(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: None if n == "gh" else "/usr/bin/" + n)
    tag, why = U.github_latest("o/r", run=fake_run({}))
    assert tag is None and "gh command is not installed" in why


def test_the_report_says_out_loud_that_unchecked_is_not_current():
    r = {"apt": {"status": U.UNKNOWN, "why": "apt-get is not on this machine", "items": []},
         "github": [], "native": [], "behind_count": 0, "unknown_count": 1}
    text = "\n".join(U.lines(r))
    assert "COULD NOT CHECK" in text
    assert "not 'up to date'" in text
    assert "An unchecked thing is not a current thing." in text


# ------------------------------------------------------------- the findings
def test_an_installed_package_behind_its_release_is_reported_with_the_command(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    row = U.github_state({"package": "gorilla-opencode", "repo": "o/r", "what": "x"},
                         run=fake_run({"dpkg-query": (0, "0.1.132"),
                                       "gh release view": (0, '{"tagName": "v0.1.147"}')}))
    assert row["status"] == U.BEHIND
    assert row["installed"] == "0.1.132" and row["latest"] == "v0.1.147"
    assert "gh release download v0.1.147" in row["command"]


def test_a_package_that_is_not_installed_is_absent_not_behind(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    row = U.github_state({"package": "nothing-here", "repo": "o/r"},
                         run=fake_run({"dpkg-query": (1, "")}))
    assert row["status"] == U.ABSENT
    assert row["latest"] is None


def test_a_suffix_picks_the_newest_release_carrying_THAT_asset(monkeypatch):
    """One repository, two platforms, separate tags.

    gorilla-firefox publishes v157.0-win64 and, older, v155.0-3 with the Linux
    .deb. Asking for 'latest' answers Windows and makes the Linux install look
    two versions behind when it is the newest Linux build there is.
    """
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/" + n)
    calls = {"gh release list": (0, '[{"tagName":"v157.0-win64"},{"tagName":"v155.0-3"}]'),
             "gh release view v157.0-win64": (0, '{"assets":[{"name":"setup.exe"}]}'),
             "gh release view v155.0-3": (0, '{"assets":[{"name":"gorilla-unleashed_155.0-3_amd64.deb"}]}')}
    tag, why = U.github_latest("o/r", asset_suffix="_amd64.deb", run=fake_run(calls))
    assert tag == "v155.0-3", why


def test_security_updates_are_counted_separately(monkeypatch):
    monkeypatch.setattr(U.shutil, "which", lambda n: "/usr/bin/apt-get")
    out = ("Inst claude-desktop [2.31226.1] (2.37337.1 Anthropic:stable [amd64])\n"
           "Inst curl [8.14.1-2+deb13u5] (8.14.1-2+deb13u7 Debian-Security:13/stable-security [amd64]) []\n")
    r = U.apt_pending(run=fake_run({"apt-get": (0, out)}), watch=["claude-desktop"])
    assert r["status"] == U.BEHIND and len(r["items"]) == 2
    assert r["security_count"] == 1
    assert [i for i in r["items"] if i["watched"]][0]["name"] == "claude-desktop"


# -------------------------------------------------------------- native
def test_a_fetched_update_that_never_took_effect_is_caught(tmp_path, monkeypatch):
    """The recorded failure: `claude update` printed success and left the old
    directory in place. The new tree is on disk, the launcher still points at
    the old one, and the program keeps running the old version while its own
    updater says it worked."""
    root = tmp_path / "versions"
    (root / "2.1.296").mkdir(parents=True)
    (root / "2.2.1").mkdir()
    launcher = tmp_path / "claude"
    launcher.symlink_to(root / "2.1.296")
    row = U.native_state({"name": "claude", "root": str(root), "launcher": str(launcher),
                          "trust_its_exit_code": False})
    assert row["status"] == U.BEHIND
    assert row["installed"] == "2.1.296"
    assert "never took effect" in row["why"]
    assert row["command"].startswith("ln -sfn")
    assert row["verify_with"].endswith("--version")


def test_a_launcher_on_the_newest_tree_is_ok_and_says_what_it_did_not_check(tmp_path):
    root = tmp_path / "versions"
    (root / "2.1.296").mkdir(parents=True)
    launcher = tmp_path / "claude"
    launcher.symlink_to(root / "2.1.296")
    row = U.native_state({"name": "claude", "root": str(root), "launcher": str(launcher)})
    assert row["status"] == U.OK
    assert "not checked here" in row["why"], "it must not imply it checked upstream"


def test_an_absent_native_install_is_absent(tmp_path):
    row = U.native_state({"name": "claude", "root": str(tmp_path / "nope"),
                          "launcher": str(tmp_path / "nope/claude")})
    assert row["status"] == U.ABSENT


# ---------------------------------------------------------------- apply
def test_apply_never_offers_to_run_anything_needing_root():
    r = {"apt": {"status": U.BEHIND, "items": [{"name": "curl"}], "command": "sudo apt upgrade"},
         "github": [{"name": "gorilla-opencode", "status": U.BEHIND, "command": "sudo apt install x"}],
         "native": [{"name": "claude", "status": U.BEHIND, "command": "ln -sfn a b",
                     "verify_with": "claude --version"}]}
    plan = U.apply_plan(r)
    assert [d["what"] for d in plan["doable"]] == ["claude"]
    assert {n["what"] for n in plan["needs_root"]} == {"1 apt package(s)", "gorilla-opencode"}
    for item in plan["doable"]:
        assert "sudo" not in item["command"], "a rootless step must not contain sudo"


def test_the_shipped_sources_file_is_readable_and_names_real_mechanisms():
    src = U.load_sources()
    assert set(src) == {"apt", "github", "native"}
    for entry in src["github"]:
        assert entry["package"] and "/" in entry["repo"]
    for entry in src["native"]:
        assert entry["root"].startswith("~") and entry["launcher"].startswith("~")
