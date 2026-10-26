# Changes from upstream (Apache-2.0 section 4(b))

Upstream https://github.com/anthropics/skills at commit dbd4588f9e1033efb41dad4bef2f7947c8993d44. Files changed by `fieldkit skills vendor` from the strips in ADOPTION.yaml:

- removed skills/claude-api/shared/preserved-thinking-migration/drop_block_probe.py -- replays captured conversations against api.anthropic.com with the caller's key (a paid vendor channel); prefix_diff.py does the offline half
- changed skills/claude-api/SKILL.md -- upstream description is 1068 characters, over the spec's 1024 (fieldkit skills validate); cut to 1008
- changed skills/claude-api/php/claude-api/tool-use.md -- placeholder address at a real domain (co.com); RFC 2606 example.com, which the privacy gate accepts
- changed skills/claude-api/python/claude-api/tool-use.md -- placeholder address at a real domain (co.com); RFC 2606 example.com, which the privacy gate accepts
- changed skills/claude-api/typescript/claude-api/tool-use.md -- placeholder address at a real domain (co.com); RFC 2606 example.com, which the privacy gate accepts
- changed skills/skill-creator/eval-viewer/viewer.html -- Google Fonts on every page view; the CSS falls back to the system fonts
- changed skills/skill-creator/eval-viewer/viewer.html -- remote script from a CDN; a local copy (pulled and checked by the owner) is used, the SRI hash still checks it
- changed skills/skill-creator/assets/eval_review.html -- Google Fonts on every page view; the CSS falls back to the system fonts
- changed skills/skill-creator/scripts/generate_report.py -- Google Fonts in every generated report; the CSS falls back to the system fonts
