"""fieldkit metrics (idea I-01, owner 2026-10-29): every count from a fixture, each one proven able to fail."""
import json
from datetime import datetime, timedelta, timezone

from fieldkit import metrics

UTC = timezone.utc
T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
SINCE, UNTIL = T0 - timedelta(days=1), T0 + timedelta(days=2)


def _line(kind, ts, content=None, usage=None, mid=None):
    msg = {"content": content or []}
    if usage:
        msg.update(usage=usage, id=mid)
    return json.dumps({"type": kind, "timestamp": ts.isoformat().replace("+00:00", "Z"), "message": msg})


def _use(name, inp):
    return {"type": "tool_use", "name": name, "input": inp}


def _res(text, err=True):
    return {"type": "tool_result", "is_error": err, "content": text}


def _transcript(tmp_path):
    proj = tmp_path / "projects" / "P"
    proj.mkdir(parents=True)
    u = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 5}
    rows = [
        _line("assistant", T0, [_use("Bash", {"command": "ls"})], u, "m1"),
        _line("assistant", T0 + timedelta(seconds=1), [_use("Bash", {"command": "ls"})], u, "m1"),  # same msg id
        _line("user", T0 + timedelta(seconds=2), [_res("PreToolUse:Bash hook error: TOOL ROUTER: use x")]),
        _line("assistant", T0 + timedelta(seconds=3), [_use("Edit", {"f": 1})], u, "m2"),
        _line("user", T0 + timedelta(seconds=4), [_res("The user doesn't want to proceed with this tool use.")]),
        _line("user", T0 + timedelta(seconds=5), [_res("Exit code 1")]),
        _line("user", T0 + timedelta(seconds=6), [_res("fine", err=False)]),
        _line("assistant", T0 - timedelta(days=5), [_use("Bash", {"command": "old"})], u, "m0"),     # outside
    ]
    (proj / "s1.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    sub = proj / "s1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "a.jsonl").write_text(_line("assistant", T0, [_use("Read", {})], u, "s1") + "\n", encoding="utf-8")
    return tmp_path / "projects"


def test_transcript_counts_tokens_tools_refusals_repeats(tmp_path):
    tr = metrics.transcripts(_transcript(tmp_path), SINCE, UNTIL)
    assert tr["sessions"] == 1 and tr["subagents"] == 1
    assert tr["api_calls"] == 3                       # m1 once (deduped), m2, the subagent's s1; m0 is outside
    assert tr["input"] == 30 and tr["cache_read"] == 3000 and tr["output"] == 15
    assert tr["executed"] == 4                        # ls, ls (repeat), Edit, Read; the old call is outside
    assert tr["repeated"] == 1
    assert tr["refused"] == 1 and tr["declined"] == 1 and tr["errors"] == 1
    assert tr["active_days"] == ["2026-10-28"]


def test_net_part_counts_zero_changes_stations_machines_in_window():
    at = lambda d: (T0 + timedelta(hours=d)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    entries = [(1, {"kind": "open", "at": at(0), "epoch": 1, "text": "Zero has been established"}),
               (2, {"kind": "checkin", "at": at(1), "from": "claude:aaaaaaaa@HOSTA"}),
               (3, {"kind": "checkin", "at": at(2), "from": "claude:bbbbbbbb@VAIO"}),
               (4, {"kind": "handover", "at": at(3), "epoch": 2, "text": "Zero is now b"}),
               (5, {"kind": "handover", "at": at(-100), "epoch": 0, "text": "outside"})]
    n = metrics.net_part(SINCE, UNTIL, entries=entries, net={"net": "open", "epoch": 2,
                                                           "zero": {"station": "claude:bbbbbbbb@VAIO"}})
    assert [c["kind"] for c in n["zero_changes"]] == ["open", "handover"]
    assert n["stations"] == 2 and n["machines"] == ["HOSTA", "VAIO"] and n["epoch_now"] == 2
    assert n["first"] < SINCE                        # the span reaches back to the first record


def test_tasks_part_by_device_and_status():
    at = lambda d: (T0 + timedelta(hours=d)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    led = {"a": {"at": at(0), "on": "here", "status": "done"}, "b": {"at": at(1), "on": "peer", "status": "died"},
           "c": {"at": at(2), "on": "peer", "status": "done"}, "d": {"at": at(-200), "on": "here", "status": "done"}}
    t = metrics.tasks_part(SINCE, UNTIL, led=led)
    assert t["total"] == 3 and t["by_device"] == {"here": 1, "peer": 2} and t["by_status"] == {"done": 2, "died": 1}


def test_rotations_counted_in_window(tmp_path):
    f = tmp_path / "h.jsonl"
    f.write_text("\n".join(json.dumps({"at": (T0 + timedelta(hours=h)).astimezone().strftime("%Y-%m-%d %H:%M")})
                           for h in (0, 5, -100)) + "\n", encoding="utf-8")
    assert metrics.rotations(SINCE, UNTIL, path=f) == 2
    assert metrics.rotations(SINCE, UNTIL, path=tmp_path / "none.jsonl") is None


def test_github_filters_window_and_counts_conclusions():
    iso = lambda h: (T0 + timedelta(hours=h)).isoformat().replace("+00:00", "Z")
    data = {"pr": [{"number": 1, "state": "MERGED", "createdAt": iso(0), "mergedAt": iso(1)},
                   {"number": 2, "state": "OPEN", "createdAt": iso(2), "mergedAt": None},
                   {"number": 3, "state": "MERGED", "createdAt": iso(-200), "mergedAt": iso(-199)}],
            "run": [{"conclusion": "success", "createdAt": iso(0)}, {"conclusion": "failure", "createdAt": iso(1)},
                    {"conclusion": "failure", "createdAt": iso(2)}, {"conclusion": "success", "createdAt": iso(-200)}]}
    g = metrics.github(SINCE, UNTIL, gh=lambda args: data[args[0]])
    assert g["prs_opened"] == 2 and g["prs_merged"] == 1 and g["prs_open"] == 1
    assert g["ci_runs"] == 3 and g["ci_by_conclusion"] == {"success": 1, "failure": 2} and not g["runs_cap_hit"]


def _parts(tmp_path, money=None, github=None):
    return {"net": lambda s, u: {"first": T0, "last": T0, "epoch_now": 3, "zero_now": "z", "zero_changes": [],
                                 "stations": 1, "machines": ["HOSTA"]},
            "tasks": lambda s, u: {"total": 1, "by_device": {"here": 1}, "by_status": {"died": 1}, "first": T0},
            "rotations": lambda s, u: None,
            "transcripts": metrics.transcripts,
            "money": money or (lambda s, u: {"usd_both": 1.5, "usd_mine": 1.5, "usd_peer": 0, "peer_machine": "",
                                            "unpriced_calls": 0, "peer_days_missing": ["2026-10-28"]}),
            "github": github or (lambda s, u: {"prs_opened": 0, "prs_merged": 0, "prs_open": 0, "ci_runs": 2,
                                              "ci_by_conclusion": {"failure": 2}, "runs_listed": 2,
                                              "runs_cap_hit": False})}


def test_collect_never_estimates_it_lists_what_it_could_not_read(tmp_path):
    def boom(s, u):
        raise RuntimeError("no prices")
    r = metrics.collect(SINCE, UNTIL, offline=True, root=_transcript(tmp_path), parts=_parts(tmp_path, money=boom))
    whats = " | ".join(u["what"] for u in r["unmeasured"])
    assert r["money"] is None and "money" in whats and "PRs and CI runs" in whats and "session rotations" in whats
    assert "other machine" in whats
    assert r["failures"]["tasks_died"] == 1 and r["failures"]["ci_failed"] is None
    text = "\n".join(metrics.lines(r))
    assert "NOT MEASURED" in text and "money:" in text and "refused by a hook 1" in text


def test_collect_default_since_is_the_first_record_and_cli_json(tmp_path, capsys, monkeypatch):
    r = metrics.collect(None, UNTIL, root=_transcript(tmp_path), parts=_parts(tmp_path))
    assert r["since"].startswith(T0.isoformat()[:16]) and r["failures"]["ci_failed"] == 2
    assert any("1 day(s)" in u["what"] for u in r["unmeasured"])          # a peer day without a cost file
    monkeypatch.setattr(metrics, "collect", lambda *a, **k: r)
    assert metrics.main(["--json"]) == 0 and json.loads(capsys.readouterr().out)["kind"] == "metrics"


def test_the_1000_run_cap_is_reported_only_when_it_cuts_into_the_window():
    iso = lambda h: (T0 + timedelta(hours=h)).isoformat().replace("+00:00", "Z")
    inside = [{"conclusion": "success", "createdAt": iso(1)}] * 1000
    g = metrics.github(SINCE, UNTIL, gh=lambda a: [] if a[0] == "pr" else inside)
    assert g["runs_cap_hit"]
    reaches_back = inside[:999] + [{"conclusion": "success", "createdAt": iso(-200)}]
    assert not metrics.github(SINCE, UNTIL, gh=lambda a: [] if a[0] == "pr" else reaches_back)["runs_cap_hit"]
