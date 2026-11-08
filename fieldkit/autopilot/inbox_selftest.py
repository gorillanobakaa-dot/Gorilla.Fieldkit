"""`fieldkit inbox self-test`: the owner inbox stays empty by construction (owner, 2026-11-01: "get rid of the morning
inbox"). Runs in a scratch folder, never touches the real inbox, and exits 0 only when every refusal holds:

  1. an agent item of any kind but "hardline" (approval, decision, report, paused, failed, net) is refused the owner
     inbox and lands in the agent ledger;
  2. `fieldkit zero rollcall` (net.check) on a station that went silent and then MISSING files nothing for the owner;
  3. the positive control: a "hardline" item does reach the owner inbox, so the checks above can fail.
"""
import os
import tempfile
from pathlib import Path
from unittest import mock

from . import inbox

AGENT_KINDS = ("approval", "decision", "report", "paused", "failed", "net")


def run(say=print) -> int:
    from ..zero import net
    problems = []
    with tempfile.TemporaryDirectory(prefix="fk-inbox-selftest-") as td:
        td = Path(td)
        env = {"FIELDKIT_COORDINATION_DIR": str(td), "FIELDKIT_AUTOPILOT_STATE": str(td / "autopilot"),
               "FIELDKIT_LINK_HOME": str(td / "link")}
        with mock.patch.dict(os.environ, env), mock.patch.object(inbox, "INBOX", td / "autopilot" / "inbox"), \
                mock.patch.object(net, "ingest_cable", lambda *a, **k: 0), mock.patch.object(net, "_key", lambda: None), \
                mock.patch.object(net, "machine", lambda: "SELFTEST"):
            owner = lambda: inbox.items("open")                                  # noqa: E731
            for kind in AGENT_KINDS:
                inbox.add(f"selftest {kind}", "an agent approval", kind=kind)
            if owner():
                problems.append(f"agent items reached the owner inbox: {[i['title'] for i in owner()]}")
            ledger = inbox.ledger_file().read_text(encoding="utf-8") if inbox.ledger_file().is_file() else ""
            if ledger.count("\n") != len(AGENT_KINDS):
                problems.append(f"agent ledger holds {ledger.count(chr(10))} of {len(AGENT_KINDS)} agent items")
            else:
                say(f"PASS agent approvals refused: {len(AGENT_KINDS)} kinds went to the ledger, owner inbox empty")
            t0 = 1_800_000_000.0
            zero, gem = "claude:aaaaaaaa@SELFTEST", "gemini:g1@SELFTEST"
            net.open_net(zero, now=t0, env={"CLAUDE_CODE_SESSION_ID": "aaaaaaaa-1"})
            net.checkin(gem, now=t0, env={})
            for t in (t0 + 1801, t0 + 3601):                                      # SILENT, then MISSING
                states = {r["station"]: r["state"] for r in net.check(heal=False, now=t, board=False)["rows"]}
            if states.get(gem) in (None, "OK"):
                problems.append(f"the rollcall never saw the station go quiet (state {states.get(gem)!r}): test is void")
            if owner():
                problems.append(f"rollcall filed a {states.get(gem)} station as an owner item: {[i['title'] for i in owner()]}")
            else:
                say(f"PASS rollcall: station went {states.get(gem)}, nothing filed for the owner")
            inbox.add("selftest hardline", "a HARD LINE of rules/CORE.md", kind="hardline")
            if len(owner()) != 1:
                problems.append("a hardline item did not reach the owner inbox: the refusals above prove nothing")
            else:
                say("PASS control: a hardline item does reach the owner inbox")
    for p in problems:
        say("FAIL " + p)
    return 3 if problems else 0
