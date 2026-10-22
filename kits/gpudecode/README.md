# gpudecode

Proves whether a browser decodes video on the graphics chip's video engine, on Windows, with Microsoft's own
tools: `wpr.exe` (ships with Windows) records every GPU work packet; `wpaexporter.exe` (Windows Performance
Toolkit, Windows ADK) exports them through `gpu-packets.wpaProfile`; `gpudecode.py verdict` counts the
browser's packets on a VideoDecode engine.

```
python -I gpudecode.py doctor
python -I gpudecode.py adk plan | adk install --yes
python -I gpudecode.py trace --seconds 30 --yes
python -I gpudecode.py verdict CSV --engines engines.json
python -I gpudecode.py live --seconds 20
python -I gpudecode.py explain
```

| File | What it is |
|---|---|
| `gpudecode.py` | the tool (stdlib only) |
| `gpu-packets.wpaProfile` | WPA profile: GPU Utilization, no key columns, so the export is one row per packet |
| `testdata/packets-iris-xe.csv` | 3 s (200-203 s) of a real export: Gorilla on Iris Xe, 1080p60 H.264 |
| `testdata/engines-iris-xe.json` | the same machine's engine map (`typeperf -qx "GPU Engine"`) |
| `mutations.yaml` | the rules `fieldkit mutate` breaks on purpose; all must be caught |

Test: `python -m pytest -q tests/test_gpudecode.py`. Procedure: skill `fieldkit-gpudecode`.
macOS (DTrace on the Intel decoder bundle + a VideoToolbox hardware-only session) is planned, not built.
