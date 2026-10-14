# PhotoKit

Review, de-duplicate and sort a folder of photos, offline (Python + Pillow).
Card `photokit` in `fieldkit/desk/tools.yaml`; test `tests/test_photokit.py`.
The full procedure that uses it (tenant photos against a letting agent's inventory report)
is the skill `fieldkit-property-inventory`.

| Command | What it does |
|---|---|
| `sheet SRC OUT [--per 12] [--strip IMG_2026...]` | labelled thumbnail grids, to look at many photos at once |
| `dupes SRC [--window 1] [--threshold 16] [--sheet OUT]` | near-duplicate candidates by perceptual hash; report only |
| `compare SRC OUT A:B ... [--prefix P]` | side-by-side sheets of chosen pairs: the duplicate decision is made by looking |
| `check SRC MAPPING [--prefix P]` | every photo mapped exactly once (or on the `skip` line) |
| `sort SRC MAPPING OUT [--prefix P]` | per-item folders `OUT/<ref>/<ref>_<stem>.jpg`, rotated, 2000 px; originals untouched |
| `fetch LIST OUT` | download `label|https-url` lines (the report's own photos) |

Measured 2026-10-12 on 136 phone photos of a white-walled room: the hash over all pairs
grouped 45 unrelated wall photos together, and missed true repeats taken a second apart
(distance 17-30). `--window 1` plus `compare` and a visual call found the 16 real duplicates.

