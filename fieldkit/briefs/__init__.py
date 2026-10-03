"""Decision briefs: every time the harness needs a person's decision it produces a complete, deterministic brief
(what is affected, verbatim, with counts and locations; every option with what it changes and what it costs; a
recommendation with its reason; what will be recorded) instead of a bare question, and it records the answer.

    schema     the Brief: validation (refuses an incomplete brief), sha256, plain and technical rendering
    producers  build briefs from harness state (decisions, claims audit, leak gate, deferred steps, owner edits)
    record     record a person's answer (owner terminal only) into the register, allowlist or dispositions
"""
