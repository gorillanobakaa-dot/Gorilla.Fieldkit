"""fluent-check: the two failures of the Linux 158 port (2026-10-14) that no parse error shows."""
import json
import subprocess

from fieldkit.buildh import fluentcheck


def test_an_attribute_left_without_its_value_is_found():
    broken = "places-delete-folder =\n  .label = \n# Variables:\nplaces-delete-page =\n  .label = Delete\n"
    assert [n for n, _ in fluentcheck.empty_values(broken)] == [2]


def test_the_windows_export_shape_is_not_an_alarm():
    flat = ("menu-prefs =\n    .label =\n        { PLATFORM() ->\n        [windows] Options\n        *[other] Preferences\n"
            "        }\n        .accesskey =\n        { PLATFORM() ->\n        [windows] O\n        *[other] P\n        }\n")
    assert fluentcheck.empty_values(flat) == []


def test_an_attribute_followed_by_the_next_attribute_is_empty():
    assert fluentcheck.empty_values("x =\n    .label =\n    .accesskey = X\n") == [(2, "    .label =")]


def _repo(tmp_path, before, after):
    w = tmp_path / "w"
    (w / "l10n").mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (w / "l10n" / "a.ftl").write_text(before, encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "pristine")
    (w / "l10n" / "a.ftl").write_text(after, encoding="utf-8")
    return w


def test_a_lost_upstream_message_is_found_unless_the_policy_names_it(tmp_path):
    before = "stocks-learn-more = Learn more\ngenai-title = Assist\nkeep = Keep\n"
    after = "keep = Gorilla Keep\n"
    w = _repo(tmp_path, before, after)
    r = fluentcheck.check(w)
    assert not r["ok"] and sorted(p.split("message ")[1].split(" ")[0] for p in r["problems"]) == ["genai-title", "stocks-learn-more"]
    pol = tmp_path / "policy"
    (pol / "config").mkdir(parents=True)
    (pol / "config" / "patch_policy.json").write_text(json.dumps({"fluent_removed": {"genai-": "the AI excision"}}))
    r = fluentcheck.check(w, pol)
    assert [p for p in r["problems"]] == ["lost: l10n/a.ftl: message stocks-learn-more is in the pristine release and not in the ported file"]
