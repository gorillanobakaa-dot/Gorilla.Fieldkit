"""exam - measure whether a model can do real work, with and without the kit.

Small local models (Gemma 4 E2B, Qwen3-Coder 30B-A3B, Granite...) are run on
the same tasks with two toolsets:

  raw  list_dir, read_file, search_text   - what a basic coding agent has
  kit  raw + find (pfind, ranked, short) + triage (build-log rules)

Every task is graded by a script from the final answer and the tool trace -
no model judges another (the rule from model-eval). Each run records pass/fail,
rounds, tool calls, prompt tokens, seconds and malformed tool calls: on this
laptop prompt size is what makes a small model slow (Gemma reads ~72 tokens/s,
Qwen ~31), so a kit that shows 20 lines instead of 400 must show up in the numbers.
"""
