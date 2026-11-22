"""fieldkit hog churn: the room hook must not start processes (60,000 console hosts, 2026-11-22)."""
import sys
import textwrap

import pytest

from fieldkit.hog import churn


# ---- scan: the gate on the real tree ------------------------------------------------------------------------------
def test_room_hook_starts_no_new_process():
    """A NEW hit means a process-starting call became reachable from the room hook. Make it in-process, or add it to
    fieldkit/hog/churn_allow.yaml with the reason it runs at most once per session."""
    res = churn.scan()
    assert res["new"] == [], "\n".join(churn.scan_lines(res))
    assert res["stale"] == [], "\n".join(churn.scan_lines(res))


def test_allow_entry_without_reason_is_not_allowed(tmp_path):
    p = tmp_path / "allow.yaml"
    p.write_text("allow:\n  a:b:subprocess.run: ''\n  c:d:os.system: 'runs once at SessionStart'\n")
    assert churn.load_allow(p) == {"c:d:os.system": "runs once at SessionStart"}


# ---- scan: precision on a small package ---------------------------------------------------------------------------
def _pkg(tmp_path, files):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name, src in files.items():
        (root / f"{name}.py").write_text(textwrap.dedent(src))
    return root


def _keys(root):
    return {h["key"] for h in churn.Graph(root).scan(("pkg.room:hook",))}


def test_scan_flags_a_subprocess_call_reached_through_an_import(tmp_path):
    root = _pkg(tmp_path, {
        "room": "from pkg import helper\ndef hook():\n    helper.probe()\n",
        "helper": "import subprocess\ndef probe():\n    subprocess.run(['git', 'status'])\ndef unused():\n"
                  "    subprocess.run(['x'])\n",
    })
    assert _keys(root) == {"pkg.helper:probe:subprocess.run"}


def test_scan_ignores_main_guard_name_lists_and_regex(tmp_path):
    root = _pkg(tmp_path, {
        "room": """
            import re, subprocess
            NAMES = ("powershell", "netsh")
            PAT = re.compile(r"powershell|typeperf")
            def hook():
                return [n for n in NAMES if PAT.search(n)]
            if __name__ == "__main__":
                subprocess.run(["powershell", "-c", "x"])
            """,
    })
    assert _keys(root) == set()


def test_scan_flags_program_name_handed_to_a_call(tmp_path):
    root = _pkg(tmp_path, {
        "room": "def hook(run):\n    run(['netsh', 'wlan', 'show', 'interfaces'])\n",
    })
    assert _keys(root) == {"pkg.room:hook:str:netsh"}


def test_scan_follows_a_thread_target(tmp_path):
    root = _pkg(tmp_path, {
        "room": """
            import os, threading
            def work():
                os.system("whoami")
            def hook():
                threading.Thread(target=work, daemon=True).start()
            """,
    })
    assert _keys(root) == {"pkg.room:work:os.system"}


# ---- trace: pure parts --------------------------------------------------------------------------------------------
def test_module_of():
    assert churn.module_of("pythonw.exe -m fieldkit.autopilot.room --hook") == "fieldkit.autopilot.room"
    assert churn.module_of("python -m fieldkit hog leaks") == "fieldkit hog"
    assert churn.module_of("C:\\Windows\\explorer.exe") == ""


def test_group_trace_projects_an_hourly_rate():
    ev = [{"name": "conhost", "starter": "python -m fieldkit.autopilot.room --hook"}] * 3 + \
         [{"name": "git", "starter": "python -m fieldkit.autopilot.room --hook"}] + \
         [{"name": "conhost", "starter": "explorer.exe"}]
    g = churn.group_trace(ev, 60)
    assert g[0]["module"] == "fieldkit.autopilot.room"
    assert g[0]["count"] == 4 and g[0]["names"] == {"conhost": 3, "git": 1}
    assert g[0]["per_hour"] == 240
    assert g[1]["starter"] == "explorer.exe" and g[1]["per_hour"] == 60


def test_starter_of_walks_past_traced_names():
    table = {1: ("python.exe", 0, "python -m fieldkit.autopilot.room --hook"),
             2: ("powershell.exe", 1, "powershell -c x"),
             3: ("conhost.exe", 2, "conhost 0x4"),
             4: ("git.exe", 9, "git status")}
    assert churn.starter_of(3, table) == "python -m fieldkit.autopilot.room --hook"
    assert churn.starter_of(4, table) == "<gone>"


# ---- hooks: verdict -----------------------------------------------------------------------------------------------
def test_verdict_uses_the_budget():
    res = [{"event": "SessionStart", "rc": 0, "secs": 1.0, "children": [(1, "git.exe", "git")] * 8},
           {"event": "UserPromptSubmit", "rc": 0, "secs": 0.1, "children": [(2, "conhost.exe", "conhost")]}]
    assert churn.verdict(res) == ["UserPromptSubmit: 1 children > budget 0"]
    assert churn.verdict(res, {"churn_budget": {"SessionStart": 2, "UserPromptSubmit": 1}}) == \
        ["SessionStart: 8 children > budget 2"]


@pytest.mark.skipif(sys.platform != "win32", reason="console hosts are a Windows cost")
def test_user_prompt_submit_starts_no_child(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_INSTALLED_CACHE", str(tmp_path / "cache.json"))
    r = churn.run_hook("UserPromptSubmit")
    assert r["children"] == [], churn.hooks_lines([r])
