"""C3 part b: an AI job's result needs a review verdict before it counts as a pass (docs/HARNESS-PLAN.md C3, I7).

An `agent` job used to pass on exit code 0 alone: a cheap worker model said it was done and nothing checked it. The
reviewer (I5, I7: Opus/Fable or a local Qwen, reading the task and the result only) is not built yet, so this is the
interface and its safe default:

    verdict = {"verdict": pass | send-back | fix-itself | unreviewed, "explanation": TEXT}

  pass        the result meets its acceptance check;
  send-back   the reviewer explains the mistake and the worker tries again;
  fix-itself  the worker failed twice, so the reviewer fixes it;
  unreviewed  no reviewer is wired: the default now. It is NOT a pass.

`review(job, rc, output, artefacts, reviewer=None)` calls `reviewer(request) -> verdict dict` when one is given, and
returns `unreviewed` when none is. A reviewer that raises, answers something other than the four words, or says pass
with no explanation also yields `unreviewed`: a broken reviewer never turns into a pass. When I7 lands, it passes its
function as `reviewer` to `orchestrate_work.work(review=...)`; nothing else changes.
"""
PASS, SEND_BACK, FIX_ITSELF, UNREVIEWED = "pass", "send-back", "fix-itself", "unreviewed"
VERDICTS = (PASS, SEND_BACK, FIX_ITSELF)


def request(job, rc, output, artefacts):
    """What a reviewer is given: the task and the result, not the history (I5: a small context)."""
    return {"task": job.get("brief") or "", "model": job.get("model") or "", "exit": rc,
            "output_tail": (output or "")[-4000:],
            "artefacts": [{k: a.get(k) for k in ("name", "size", "sha256")} for a in artefacts or []]}


def review(job, rc, output, artefacts, reviewer=None):
    if reviewer is None:
        return {"verdict": UNREVIEWED, "explanation": "no reviewer is wired yet (I7): the result is not checked, so it "
                                                      "does not count as a pass"}
    try:
        v = reviewer(request(job, rc, output, artefacts))
    except Exception as e:  # noqa: BLE001 - a broken reviewer is "unreviewed", never a pass
        return {"verdict": UNREVIEWED, "explanation": f"the reviewer failed: {type(e).__name__}: {e}"}
    word = (v or {}).get("verdict") if isinstance(v, dict) else None
    why = ((v or {}).get("explanation") or "").strip() if isinstance(v, dict) else ""
    if word not in VERDICTS:
        return {"verdict": UNREVIEWED, "explanation": f"the reviewer answered {word!r}, not one of {', '.join(VERDICTS)}"}
    if not why:
        return {"verdict": UNREVIEWED, "explanation": f"the reviewer said {word} with no explanation"}
    return {"verdict": word, "explanation": why}
