# Offline Pre-Check: verify-and-build

*Generated 2026-10-02 19:09:42 by rules only. No model was involved, so everything below is a deterministic finding about the files as they are on disk.*

## Files Scanned

| File | Language | Lines | Code | Complexity | SHA-256 |
|---|---|---|---|---|---|
| `buildh__audit.py` | py | 208 | 177 | 62 | `13f633db57fc818c` |
| `buildh__buildrun.py` | py | 600 | 513 | 128 | `2a82ee2170dada15` |
| `buildh__claude_watch.py` | py | 156 | 132 | 62 | `94db967bea1b73a3` |
| `buildh__compare.py` | py | 53 | 44 | 18 | `a953a0e0d66c6d58` |
| `buildh__compile.py` | py | 187 | 161 | 80 | `f7c35929a2e868ad` |
| `buildh__creepfix.py` | py | 95 | 77 | 26 | `490b8928a6c98104` |
| `buildh__icons.py` | py | 81 | 66 | 15 | `b0978a2949ef6057` |
| `buildh__mozbuild_rules.py` | py | 77 | 66 | 20 | `f0f9966553c908a8` |
| `buildh__ownercheck.py` | py | 116 | 98 | 32 | `9133adbef0705b45` |
| `buildh__preflight.py` | py | 110 | 95 | 31 | `9b05cb1da17df8bb` |
| `buildh__recorder.py` | py | 325 | 262 | 112 | `fbf3539389526959` |
| `buildh__repair.py` | py | 227 | 199 | 68 | `e72fe35c70982e51` |
| `buildh__symbols.py` | py | 106 | 87 | 31 | `8fa0a6a84442510e` |
| `buildh__truthbound.py` | py | 86 | 68 | 18 | `1f9109b1cb20ce86` |
| `buildh__verify.py` | py | 492 | 422 | 201 | `5d0dd18dbb2cb745` |

## Findings

🔴 P0: 0 · 🟠 P1: 0 · 🟡 P2: 1 · 🟢 P3: 0

### 🟡 P2-001 — P2

- **Plain English:** A sticky note saying 'finish this later' was left inside the machine. It still works, but somebody meant to come back to it.
- **Technical:** buildh__recorder.py: 1 TODO/FIXME/XXX/HACK marker(s) in the file.
- **Fix:** Resolve it, or convert it into a tracked item so it is visible outside the source.
