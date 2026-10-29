"""fieldkit hog: every rule on fake facts (fake `lms ps`, fake process tables), the registry, the actions, the hook
line and the schedules. Run: python -m pytest -q tests/test_hog.py"""
import json
import subprocess
import sys
import time

import pytest

from fieldkit import hog
from fieldkit.hog import install, probe, registry, rules

GB = 1024 ** 3
MB = 1024 ** 2
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_HOG_DIR", str(tmp_path / "hog"))
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")      # read at import time; never the real ROOM.log


@pytest.fixture
def cfg():
    return hog.config()


def facts(**kw):
    f = {"t": NOW, "ram": {"total": 64 * GB, "used": 10 * GB}, "commit": {"used": 10 * GB, "limit": 64 * GB},
         "procs": [], "alive": [], "lms": {}, "ollama": {}, "disks": [], "temp": {"c": None}}
    f.update(kw)
    return f


# ---- ram / commit ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("used,sev", [(51.1, None), (51.3, "warn"), (57.5, "warn"), (57.7, "act")])
def test_ram_thresholds(cfg, used, sev):
    # 64 GB: 80% = 51.2 GB, 90% = 57.6 GB
    f = rules.ram(facts(ram={"total": 64 * GB, "used": used * GB},
                        procs=[{"pid": 1, "ctime": 1.0, "name": "llama-server.exe", "rss": 43 * GB}]), cfg)
    assert [x["severity"] for x in f] == ([sev] if sev else [])
    if sev:
        assert "llama-server.exe 1 43.0 GB" in f[0]["line"]


@pytest.mark.parametrize("used,sev", [(54.3, None), (54.5, "warn"), (60.7, "warn"), (60.9, "act")])
def test_commit_thresholds(cfg, used, sev):
    f = rules.commit(facts(commit={"used": used * GB, "limit": 64 * GB}), cfg)
    assert [x["severity"] for x in f] == ([sev] if sev else [])


def test_no_commit_on_macos(cfg):
    assert rules.commit(facts(commit=None), cfg) == []


# ---- LM Studio ---------------------------------------------------------------------------------------------------
LMS_JSON = json.dumps([{"type": "llm", "identifier": "qwen3.6-35b-a3b", "modelKey": "qwen/qwen3.6-35b-a3b",
                        "sizeBytes": 22 * GB, "status": "idle"}])


#: `lms ps --json` as lms printed it on this laptop, 2026-10-26 01:40 (fields trimmed)
REAL = ('[{"type":"embedding","modelKey":"text-embedding-nomic-embed-text-v1.5","sizeBytes":84106624,'
        '"identifier":"text-embedding-nomic-embed-text-v1.5","ttlMs":null,"lastUsedTime":1792978806900,'
        '"status":"idle","queued":0}]')


def test_parse_lms_ps():
    assert probe.parse_lms_ps(LMS_JSON) == [{"id": "qwen3.6-35b-a3b", "key": "qwen/qwen3.6-35b-a3b",
                                             "size": 22 * GB, "status": "idle", "last_used": None}]
    assert probe.parse_lms_ps(REAL)[0]["last_used"] == 1792978806.9
    assert probe.parse_lms_ps("[]") == []


def test_lms_last_used_time_counts_as_activity(cfg):
    x = _lms(600)
    x["lms"]["models"][0]["last_used"] = NOW - 60          # lms says it was used a minute ago: keep it
    assert rules.lms_models(facts(**x), cfg, NOW) == []


def _lms(last_min_ago, status="idle", by_model=True):
    t = NOW - last_min_ago * 60
    act = {"last_any": t, "by_model": {"qwen/qwen3.6-35b-a3b": t} if by_model else {}}
    m = probe.parse_lms_ps(LMS_JSON)[0]
    m["status"] = status
    return {"lms": {"exe": "lms", "models": [m], "activity": act}}


@pytest.mark.parametrize("ago,unload", [(14.9, False), (15.1, True)])
def test_lms_idle_limit(cfg, ago, unload):
    f = rules.lms_models(facts(**_lms(ago)), cfg, NOW)
    assert bool(f and f[0]["action"]) is unload
    if unload:
        assert f[0]["action"] == {"kind": "unload_lms", "id": "qwen3.6-35b-a3b"}


@pytest.mark.parametrize("ago,unload", [(4.9, False), (5.1, True)])
def test_lms_idle_limit_when_ram_critical(cfg, ago, unload):
    f = rules.lms_models(facts(**_lms(ago)), cfg, NOW, crit=True)
    assert bool(f and f[0]["action"]) is unload


def test_lms_busy_model_is_never_unloaded(cfg):
    assert rules.lms_models(facts(**_lms(600, status="generating")), cfg, NOW) == []


def test_lms_without_log_evidence_is_not_unloaded(cfg):
    x = _lms(600)
    x["lms"]["activity"] = {"last_any": None, "by_model": {}}
    f = rules.lms_models(facts(**x), cfg, NOW)
    assert f[0]["severity"] == "warn" and f[0]["action"] is None


def test_lms_falls_back_to_last_server_line(cfg):
    f = rules.lms_models(facts(**_lms(30, by_model=False)), cfg, NOW)
    assert f[0]["action"]["kind"] == "unload_lms"


def test_trigger_case_evaluates_to_unload(cfg):
    """2026-10-26 01:2x: 58.6/63.7 GB, llama-server 43 GB, Qwen idle for hours."""
    f = rules.evaluate(facts(ram={"total": int(63.7 * GB), "used": int(58.6 * GB)},
                             procs=[{"pid": 9, "ctime": 1.0, "name": "llama-server.exe", "rss": 43 * GB}],
                             **_lms(240)), [], [], cfg)
    assert rules.worst(f) == 2
    assert [x["action"] for x in f if x["action"]] == [{"kind": "unload_lms", "id": "qwen3.6-35b-a3b"}]
    assert rules.summary(f).startswith("hog: ACT - RAM 58.6/63.7 GB (92%)")


def test_lms_activity_ignores_lms_cli_probes():
    lines = ["[2026-10-26 00:10:00][INFO][qwen/qwen3.6-35b-a3b] Running chat completion on conversation with 2 messages.",
             "[2026-10-26 00:20:00][INFO] llama_server: something real",
             "[2026-10-26 01:23:16][INFO][LMSAuthenticator][Client=lms-cli][Endpoint=listLoaded] Listing loaded models",
             "[2026-10-26 01:31:29][DEBUG][LMSAuthenticator][Client=lms-cli] Client created."]
    a = probe.lms_activity(lines)
    assert time.strftime("%H:%M", time.localtime(a["last_any"])) == "00:20"
    assert time.strftime("%H:%M", time.localtime(a["by_model"]["qwen/qwen3.6-35b-a3b"])) == "00:10"


def test_lms_not_run_when_lm_studio_is_not_running(cfg):
    def boom(*a, **k):
        raise AssertionError("lms must not be started")
    assert probe.lms([{"name": "explorer.exe"}], cfg, run=boom)["models"] == []


def test_lms_reads_fake_ps_and_log(cfg, tmp_path, monkeypatch):
    log = tmp_path / "x.log"
    log.write_text("[2026-10-26 00:10:00][INFO][qwen/qwen3.6-35b-a3b] Running chat completion on conversation with 2 messages.\n")
    monkeypatch.setattr(probe, "lms_path", lambda c=None: "lms")
    r = probe.lms([{"name": "LM Studio.exe"}], cfg, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, LMS_JSON, ""),
                  logs=[log])
    assert r["models"][0]["id"] == "qwen3.6-35b-a3b" and r["activity"]["by_model"]


# ---- Ollama ------------------------------------------------------------------------------------------------------
def test_ollama_idle_by_unmoved_expiry(cfg):
    exp = "2318-01-01T00:00:00Z"
    hist = [{"t": NOW - m * 60, "procs": [], "ollama": {"llama3": exp}} for m in (20, 15, 10, 5)]
    f = rules.ollama_models(facts(ollama={"models": [{"name": "llama3", "size": GB, "expires_at": exp}]}), hist, cfg, NOW)
    assert f[0]["action"] == {"kind": "unload_ollama", "name": "llama3"}
    hist[-2]["ollama"]["llama3"] = "2318-01-01T00:00:01Z"         # a request 10 min ago moved it
    assert rules.ollama_models(facts(ollama={"models": [{"name": "llama3", "expires_at": exp}]}), hist, cfg, NOW) == []


def test_ollama_url_must_be_loopback():
    with pytest.raises(ValueError):
        probe.loopback("http://192.168.1.5:11434")
    assert probe.loopback("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"


def test_ollama_not_asked_when_not_running(cfg):
    def boom(*a, **k):
        raise AssertionError("no request when Ollama is not running")
    assert probe.ollama([{"name": "x.exe"}], cfg, opener=boom)["models"] == []


# ---- leaks / cpu -------------------------------------------------------------------------------------------------
def _hist(rss_gb, step_min=5, cpu=None, pid=7):
    out = []
    n = len(rss_gb)
    for i, r in enumerate(rss_gb[:-1]):
        out.append({"t": NOW - (n - 1 - i) * step_min * 60,
                    "procs": [[pid, 1.0, "leaky.exe", int(r * GB), cpu[i] if cpu else None]]})
    cur = facts(procs=[{"pid": pid, "ctime": 1.0, "name": "leaky.exe", "rss": int(rss_gb[-1] * GB),
                        "cpu_s": cpu[-1] if cpu else None}])
    return out, cur


def test_leak_reported_with_curve(cfg):
    h, cur = _hist([1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.1])
    f = rules.leaks(cur, h, cfg)
    assert f and f[0]["numbers"]["curve_gb"] == [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.1] and f[0]["action"] is None


def test_leak_needs_a_full_gb(cfg):
    h, cur = _hist([1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.99])
    assert rules.leaks(cur, h, cfg) == []
    h, cur = _hist([1, 1.1, 1.2, 1.3, 1.4, 1.5, 2.0])
    assert rules.leaks(cur, h, cfg)


def test_leak_that_dropped_is_not_a_leak(cfg):
    h, cur = _hist([1, 1.5, 1.4, 1.8, 2.0, 2.2, 2.5])
    assert rules.leaks(cur, h, cfg) == []


def test_leak_needs_enough_samples_and_span(cfg):
    h, cur = _hist([1, 1.5, 2.0, 2.5, 3.0])                 # 5 samples < 6
    assert rules.leaks(cur, h, cfg) == []
    h, cur = _hist([1, 1.3, 1.6, 2.0, 2.3, 2.6], step_min=4)   # 6 samples over 20 min < 24 min
    assert rules.leaks(cur, h, cfg) == []
    h, cur = _hist([1, 1.3, 1.6, 2.0, 2.3, 2.6], step_min=5)   # 6 samples over 25 min >= 24 min
    assert rules.leaks(cur, h, cfg)


def test_cpu_pinned(cfg):
    # 5-minute samples, 290 cpu-seconds per 300 s = 96.7%
    h, cur = _hist([1, 1, 1, 1], cpu=[0, 290, 580, 870])
    f = rules.cpu_pinned(cur, h, cfg)
    assert f and f[0]["numbers"]["min_pct"] == pytest.approx(96.7, abs=0.1)
    h, cur = _hist([1, 1, 1, 1], cpu=[0, 290, 560, 850])     # one interval at 90%
    assert rules.cpu_pinned(cur, h, cfg) == []


def test_cpu_pinned_needs_ten_minutes(cfg):
    h, cur = _hist([1, 1], cpu=[0, 299])                     # one 5-minute interval only
    assert rules.cpu_pinned(cur, h, cfg) == []


# ---- temp / disk -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("c,sev", [(84.9, None), (85, "warn"), (94.9, "warn"), (95, "act")])
def test_temp(cfg, c, sev):
    f = rules.temp(facts(temp={"c": c, "sensor": "tpfan_status"}), cfg)
    assert [x["severity"] for x in f] == ([sev] if sev else [])


@pytest.mark.parametrize("free,sev", [(10.0, None), (9.9, "warn"), (5.0, "warn"), (4.9, "act")])
def test_disk(cfg, free, sev):
    f = rules.disk(facts(disks=[{"path": "C:\\", "total": 100 * GB, "free": free * GB}]), cfg)
    assert [x["severity"] for x in f] == ([sev] if sev else [])


# ---- orphans -----------------------------------------------------------------------------------------------------
def _entry(**kw):
    e = {"pid": 50, "ctime": NOW - 600, "name": "python.exe", "cls": "pytest", "session": "s1",
         "owner_pid": 40, "owner_ctime": NOW - 7200, "cap_s": None, "source": "tree"}
    e.update(kw)
    return e


def test_orphan_when_session_ended(cfg):
    f = rules.orphans(facts(alive=[[50, NOW - 600]]), [_entry()], cfg, NOW)
    assert f[0]["action"] == {"kind": "kill", "pid": 50, "ctime": NOW - 600, "name": "python.exe"}


def test_no_orphan_while_session_lives(cfg):
    assert rules.orphans(facts(alive=[[50, NOW - 600], [40, NOW - 7200]]), [_entry()], cfg, NOW) == []


def test_reused_pid_is_never_a_candidate(cfg):
    assert rules.orphans(facts(alive=[[50, NOW - 30]]), [_entry()], cfg, NOW) == []


def test_cap_applies_to_registered_not_to_tree(cfg):
    alive = [[50, NOW - 5 * 3600], [40, NOW - 7200]]
    assert rules.orphans(facts(alive=alive), [_entry(ctime=NOW - 5 * 3600)], cfg, NOW) == []
    f = rules.orphans(facts(alive=alive), [_entry(ctime=NOW - 5 * 3600, source="register")], cfg, NOW)
    assert f and "past its cap" in f[0]["line"]
    alive = [[50, NOW - 3.9 * 3600], [40, NOW - 7200]]
    assert rules.orphans(facts(alive=alive), [_entry(ctime=NOW - 3.9 * 3600, source="register")], cfg, NOW) == []


def test_link_listener_outlives_its_session_and_is_never_stopped(cfg):
    """2026-10-28 19:43, the L15: "orphan python.exe 8040 (link-listener, started by 8dc32564): its session ended:
    stop"; the L15 had no link listener until 20:37. A service class is never an orphan, nor past a cap."""
    lis = _entry(pid=8040, name="python.exe", cls="link-listener", session="8dc32564", source="printed")
    rel = _entry(pid=8041, name="python.exe", cls="link-relay", session="8dc32564", source="register")
    alive = [[8040, NOW - 600], [8041, NOW - 600]]
    assert rules.orphans(facts(alive=alive), [lis, rel], cfg, NOW) == []
    old = [dict(lis, ctime=NOW - 9 * 3600)]
    assert rules.orphans(facts(alive=[[8040, NOW - 9 * 3600]]), old, cfg, NOW) == []
    found = rules.evaluate(facts(alive=alive), [], [lis, rel], cfg)
    assert not [f for f in found if f.get("action")]
    assert rules.orphans(facts(alive=alive), [dict(lis, cls="pytest")], cfg, NOW)   # the same entry otherwise goes


def test_dead_service_is_reported_with_its_restart_line_not_acted_on(cfg):
    lis = _entry(pid=8040, cls="link-listener", session="8dc32564")
    f = rules.services(facts(alive=[]), [lis], cfg)
    assert len(f) == 1 and f[0]["check"] == "service" and f[0]["action"] is None
    assert "fieldkit peers link restart" in f[0]["line"] and "8040" in f[0]["line"]
    assert [x for x in rules.evaluate(facts(alive=[]), [], [lis], cfg) if x["check"] == "service"]
    assert rules.services(facts(alive=[[8040, NOW - 600]]), [lis], cfg) == []              # alive: nothing to say
    new = _entry(pid=9000, ctime=NOW - 60, cls="link-listener")
    assert rules.services(facts(alive=[[9000, NOW - 60]]), [lis, new], cfg) == []          # a live one replaced it
    assert rules.services(facts(alive=[]), [_entry(cls="pytest")], cfg) == []              # a dead runner is no news


def test_orphan_classes(cfg):
    c = cfg["orphan_classes"]
    assert rules.orphan_class("python.exe", "python -m pytest -q tests", c) == "pytest"
    assert rules.orphan_class("go.exe", "go test ./...", c) == "go-test"
    assert rules.orphan_class("firefox.exe", "firefox.exe -headless", c) == "browser"
    assert rules.orphan_class("python.exe", "python -m fieldkit peers link serve", c) == "link-listener"
    assert rules.orphan_class("python.exe", "python -P -m fieldkit peers link relay listen", c) == "link-relay"
    assert set(cfg["service_classes"]) == {"link-listener", "link-relay"}
    assert rules.orphan_class("WINWORD.EXE", "WINWORD.EXE", c) is None
    assert rules.orphan_class("python.exe", "python -m fieldkit hog watch", c) is None


def test_a_shell_is_never_classed_by_the_command_text_it_carries(cfg):
    class P:
        def __init__(self, name, cmd):
            self._n, self._c = name, cmd

        def name(self):
            return self._n

        def cmdline(self):
            return self._c.split()
    assert registry._cls(P("bash.exe", "bash -c timeout 600 python -m pytest -q"), cfg["orphan_classes"]) is None
    assert registry._cls(P("python.exe", "python -m pytest -q"), cfg["orphan_classes"]) == "pytest"


# ---- pile-up -----------------------------------------------------------------------------------------------------
def _table(n, cls_name="conhost.exe", parent=("claude.exe", 16824, 17880), parent_ct=NOW - 30 * 3600,
           kid_ct=lambda i: NOW - 29 * 3600 + i * 60, rss=8 * MB):
    """The 2026-10-26 shape: Claude Desktop's main process 17880, its NodeService utility 16824, n consoles under it."""
    name, pid, ppid = parent
    rows = [[ppid, 4, NOW - 31 * 3600, name, 200 * MB], [pid, ppid, parent_ct, name, 100 * MB]]
    rows += [[1000 + i, pid, kid_ct(i), cls_name, rss] for i in range(n)]
    return rows


def test_pileup_names_parent_count_memory_and_fix(cfg):
    f = rules.pileup(facts(table=_table(33)), cfg)
    assert len(f) == 1 and f[0]["severity"] == "warn" and f[0]["action"] is None
    n = f[0]["numbers"]
    assert (n["pid"], n["cls"], n["count"], n["rss"], n["parent_pid"]) == (16824, "conhost", 33, 33 * 8 * MB, 17880)
    line = f[0]["line"]
    assert line.startswith(f"Claude (claude.exe 16824, started {rules.stamp(NOW - 30 * 3600)}, parent claude.exe 17880)"
                           f" holds 33 consoles since {rules.stamp(NOW - 29 * 3600)}, 264 MB: restart Claude Desktop")


def test_pileup_threshold_is_more_than_n(cfg):
    assert rules.pileup(facts(table=_table(20)), cfg) == []          # 20 = the limit, not over it
    assert rules.pileup(facts(table=_table(21)), cfg)


def test_pileup_counts_each_class_and_parent_apart(cfg):
    t = _table(15) + [[2000 + i, 16824, NOW - 3600, "bash.exe", MB] for i in range(15)]
    t += [[3000 + i, 17880, NOW - 3600, "conhost.exe", MB] for i in range(15)]
    assert rules.pileup(facts(table=t), cfg) == []                  # 45 children, no pair over 20


def test_pileup_stale_children_left_behind(cfg):
    # 12 consoles a day old and 2 fresh ones under the same parent: below 20, but left behind
    t = _table(12, kid_ct=lambda i: NOW - 26 * 3600) + [[5000 + i, 16824, NOW - 60, "conhost.exe", MB] for i in range(2)]
    f = rules.pileup(facts(table=t), cfg)
    assert f and f[0]["numbers"]["stale"] == 12 and "12 consoles started over 12 h before its newest (of 14)" in f[0]["line"]
    t = _table(9, kid_ct=lambda i: NOW - 26 * 3600) + [[5000, 16824, NOW - 60, "conhost.exe", MB]]
    assert rules.pileup(facts(table=t), cfg) == []                  # 9 stale < 10
    t = _table(10, kid_ct=lambda i: NOW - 26 * 3600) + [[5000, 16824, NOW - 60, "conhost.exe", MB]]
    assert rules.pileup(facts(table=t), cfg)                        # 10 stale is enough
    t = _table(12, kid_ct=lambda i: NOW - 11.9 * 3600) + [[5000, 16824, NOW, "conhost.exe", MB]]
    assert rules.pileup(facts(table=t), cfg) == []                  # 11.9 h apart is not over 12 h


def test_pileup_dead_parent_or_reused_pid_holds_nothing(cfg):
    t = _table(33)[2:]                                              # parent and grandparent gone
    assert rules.pileup(facts(table=t), cfg) == []
    t = _table(33, parent_ct=NOW - 60)                              # 16824 now a process younger than its "children"
    assert rules.pileup(facts(table=t), cfg) == []
    t = _table(33)[1:]                                              # grandparent gone: still reported
    assert "parent gone)" in rules.pileup(facts(table=t), cfg)[0]["line"]


def test_pileup_linux_shape(cfg):
    """Linux/macOS: no .exe; a shell holding python workers; init adopting what a dead terminal left."""
    t = [[1, 0, NOW - 86400, "systemd", 10 * MB], [900, 1, NOW - 7200, "bash", MB]]
    t += [[1000 + i, 900, NOW - 3600, "python3.12", 30 * MB] for i in range(25)]
    t += [[2000 + i, 1, NOW - 3000, "bash", MB] for i in range(22)]
    f = rules.pileup(facts(table=t), cfg)
    assert [(x["numbers"]["pid"], x["numbers"]["cls"], x["numbers"]["count"]) for x in f] == \
        [(900, "python", 25), (1, "bash", 23)]                     # 22 adopted + bash 900 itself
    assert "close or restart bash when nothing it runs is working" in f[0]["line"]
    assert f[1]["line"].startswith("init (their own parent died) (systemd 1,") and "pgrep -alP 1" in f[1]["line"]


def test_pileup_classes(cfg):
    c = cfg["pileup_classes"]
    got = {n: next((k for k, rx in c.items() if __import__("re").search(rx, n.lower())), None)
           for n in ("conhost.exe", "bash", "python3.12", "pythonw.exe", "node.exe", "pwsh", "PowerShell.exe",
                     "cmd.exe", "WINWORD.EXE", "bashful", "nodejs-helper")}
    assert got == {"conhost.exe": "conhost", "bash": "bash", "python3.12": "python", "pythonw.exe": "python",
                   "node.exe": "node", "pwsh": "powershell", "PowerShell.exe": "powershell", "cmd.exe": "cmd",
                   "WINWORD.EXE": None, "bashful": None, "nodejs-helper": None}


def test_pileup_is_wired_into_evaluate_and_never_acts(cfg):
    f = rules.evaluate(facts(table=_table(33)), [], [], cfg)
    assert [x["check"] for x in f] == ["pileup"] and rules.worst(f) == 1
    assert hog.act(cfg, facts(table=_table(33)), f, settle=0, do=lambda a: (True, "")) == []


def test_probe_table_has_ppid(cfg):
    import os
    _p, _a, rows = probe.processes(5)
    me = next(r for r in rows if r["pid"] == os.getpid())
    assert me["ppid"] == os.getppid()


def test_registry_add_dedupes_and_prunes():
    e = _entry()
    assert registry.add([e]) == [e]
    assert registry.add([e]) == []
    assert registry.prune([[50, NOW - 600]]) == [e]
    assert registry.prune([[50, NOW - 1]]) == []


def test_kill_stops_a_real_registered_child_and_refuses_a_reused_pid():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        import psutil
        c = probe.ct(psutil.Process(p.pid).create_time())
        ok, why = hog.kill({"pid": p.pid, "ctime": c + 1})
        assert not ok and "reused" in why and p.poll() is None
        ok, why = hog.kill({"pid": p.pid, "ctime": c})
        assert ok
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()


def test_observe_registers_printed_pid_of_an_orphan_class(monkeypatch, cfg):
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)  # pytest"])
    try:
        import psutil
        fake_sess = psutil.Process()        # this test process stands in for the agent's session process
        monkeypatch.setattr(registry, "session_process", lambda me=None: fake_sess)
        got = registry.observe({"session_id": "s9", "tool_response": {"stdout": f"started pid={p.pid}"}},
                               cfg["orphan_classes"])
        # this process is the fake session: its real children (a runner's conhost.exe) are not the test's business
        assert [(g["pid"], g["cls"]) for g in got if g["pid"] == p.pid] == [(p.pid, "pytest")]
        assert got[0]["owner_pid"] == fake_sess.pid
        registry.save([])
        later = registry.observe({"session_id": "s9", "tool_response": f"pid={p.pid}"}, cfg["orphan_classes"],
                                 now=time.time() + 3600)       # printed an hour after it started: not ours to claim
        assert [g for g in later if g["pid"] == p.pid] == []
    finally:
        p.kill()


# ---- act / log / hook --------------------------------------------------------------------------------------------
def test_act_logs_every_action_to_state_and_room(cfg, tmp_path):
    f = rules.evaluate(facts(**_lms(60)), [], [], cfg)
    done = hog.act(cfg, facts(**_lms(60)), f, settle=0, do=lambda a: (True, "Model unloaded."))
    assert done[0]["ok"] and done[0]["action"]["kind"] == "unload_lms"
    rec = json.loads((tmp_path / "hog" / "actions.jsonl").read_text().splitlines()[-1])
    assert rec["why"].startswith("LM Studio model qwen3.6-35b-a3b") and "ram_before" in rec and "ram_after" in rec
    room = (tmp_path / "room" / "ROOM.log").read_text()
    assert "\tstop\tmodel=qwen3.6-35b-a3b\thog unload_lms OK" in room


def test_act_respects_the_switches(cfg):
    cfg["act_unload_idle_models"] = False
    f = rules.evaluate(facts(**_lms(60)), [], [], cfg)
    assert hog.act(cfg, facts(**_lms(60)), f, settle=0, do=lambda a: (True, "")) == []


def test_dry_run_does_nothing(cfg):
    f = rules.evaluate(facts(**_lms(60)), [], [], cfg)
    called = []
    d = hog.act(cfg, facts(**_lms(60)), f, dry=True, do=lambda a: called.append(a))
    assert d[0]["ok"] is None and not called


def test_hook_line_quiet_when_ok_and_one_line_when_not(tmp_path):
    assert hog.hook_line(now=NOW, ram_fn=lambda: (10 * GB, 64 * GB)) == ""
    (tmp_path / "hog").mkdir(exist_ok=True)
    (tmp_path / "hog" / "last.json").write_text(json.dumps({"t": NOW - 60, "severity": 1, "summary": "hog: WARN - x"}))
    assert hog.hook_line(now=NOW, ram_fn=lambda: (10 * GB, 64 * GB)) == "hog: WARN - x"
    assert hog.hook_line(now=NOW + 16 * 60, ram_fn=lambda: (10 * GB, 64 * GB)) == ""          # stale
    line = hog.hook_line(now=NOW + 16 * 60, ram_fn=lambda: (52 * GB, 64 * GB))
    assert line.startswith("hog: RAM 52.0/64.0 GB (81%)") and "\n" not in line


def test_room_hook_carries_the_hog_line(tmp_path, monkeypatch, capsys):
    import io
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setattr(hog, "hook_line", lambda: "hog: ACT - RAM 58.6/63.7 GB (92%)")
    room.hook(io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "abc"})))
    out = json.loads(capsys.readouterr().out)
    # the machine tag line (peers/machine.py) follows at a session's first prompt
    assert out["hookSpecificOutput"]["additionalContext"].splitlines()[0] == "hog: ACT - RAM 58.6/63.7 GB (92%)"


def test_check_writes_last_and_samples(cfg, tmp_path):
    f = facts(procs=[{"pid": 1, "ctime": 1.0, "name": "a", "rss": GB, "cpu_s": 1.0}], alive=[[1, 1.0]])
    _facts, found = hog.check(cfg, facts=f, hist=[], reg=[], sample=True)
    assert found == []
    assert json.loads((tmp_path / "hog" / "last.json").read_text())["summary"] == "hog: OK"
    assert json.loads((tmp_path / "hog" / "samples.jsonl").read_text().splitlines()[-1])["procs"] == [[1, 1.0, "a", GB, 1.0]]


def test_local_thresholds_override(tmp_path):
    (tmp_path / "hog").mkdir(exist_ok=True)
    (tmp_path / "hog" / "thresholds.json").write_text(json.dumps({"model_idle_min": 30}))
    assert hog.config()["model_idle_min"] == 30 and hog.config()["ram_warn_pct"] == 80


def _pf(free_gb, total_gb=64, found_lms=False):
    f = facts(ram={"total": total_gb * GB, "used": (total_gb - free_gb) * GB},
              procs=[{"pid": 9, "ctime": 1.0, "name": "llama-server.exe", "rss": 43 * GB, "cpu_s": 1.0}],
              **(_lms(60) if found_lms else {}))
    return f, (lambda: ((total_gb - free_gb) * GB, total_gb * GB))


def test_preflight_check_ok_warn_stop(cfg):
    f, ram = _pf(40)
    assert hog.preflight_check({}, cfg, facts=f, ram_fn=ram) == ("ok", "hog: RAM 24.0/64.0 GB (38%)")
    f, ram = _pf(10)                                      # 84% used: warn, runs
    st, line = hog.preflight_check({}, cfg, facts=f, ram_fn=ram)
    assert st == "warn" and "RAM 54.0/64.0 GB" in line
    f, ram = _pf(6.3)                                     # 9.8% free after reclaiming: under the 10% floor
    st, line = hog.preflight_check({}, cfg, facts=f, ram_fn=ram)
    assert st == "stop" and "under the floor of 10%" in line and "llama-server.exe 9 43.0 GB" in line
    f, ram = _pf(6.5)
    assert hog.preflight_check({}, cfg, facts=f, ram_fn=ram)[0] != "stop"


def test_preflight_check_reclaims_first(cfg):
    f, _ = _pf(5, found_lms=True)
    freed = []
    st, line = hog.preflight_check({}, cfg, facts=f, ram_fn=lambda: (24 * GB, 64 * GB),
                                   do=lambda a: (freed.append(a) or True, "unloaded"))
    assert freed == [{"kind": "unload_lms", "id": "qwen3.6-35b-a3b"}] and st != "stop" and "1 action(s)" in line


def test_parse_meminfo():
    assert probe.parse_meminfo("MemTotal: 100 kB\nCommitLimit: 2000 kB\nCommitted_AS: 1500 kB\n") == \
        {"used": 1500 * 1024, "limit": 2000 * 1024}


# ---- schedules ---------------------------------------------------------------------------------------------------
def test_install_plans_all_three_platforms(tmp_path):
    w = install.plan("win32", python=r"C:\py\python.exe", root=r"C:\fk", home=tmp_path)
    assert w[-1][1][:3] == ["schtasks", "/Create", "/TN"] and "PT5M" in w[0][2] and "InteractiveToken" in w[0][2]
    assert "-m fieldkit hog watch" in w[0][2]
    lx = install.plan("linux", python="/usr/bin/python3", root="/fk", home=tmp_path)
    assert "OnCalendar=*:0/5" in lx[1][2] and "ExecStart=/usr/bin/python3 -m fieldkit hog watch" in lx[0][2]
    if sys.platform != "win32":
        mac = install.plan("darwin", python="/usr/bin/python3", root="/fk", home=tmp_path)
        assert "<integer>300</integer>" in mac[0][2]
    else:
        assert "<integer>300</integer>" in install.launchd_plist("/usr/bin/python3", "/fk")


def test_install_dry_run_changes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(install, "plan", lambda: [("write", tmp_path / "x", "y"), ("run", ["false"])])
    assert install.main(apply=False) == 0
    assert not (tmp_path / "x").exists() and "nothing changed" in capsys.readouterr().out


def test_cli_exit_code_is_severity(monkeypatch, capsys):
    monkeypatch.setattr(hog, "check", lambda *a, **k: (facts(), [rules._f("ram", "warn", "RAM high")]))
    assert hog.main(["check"]) == 1
    assert capsys.readouterr().out.strip() == "hog: WARN - RAM high"
