"""leakgate: the owner's deterministic leak, telemetry and network-behaviour release gate (Gorilla.firefox/leakgate/SPEC.md).

PRIMARY RULE: FAIL CLOSED. Every observation from every sensor is matched against an explicit allowlist
(Gorilla.firefox/leakgate/allow.json). No entry -> unexpected -> FAIL. An entry nobody approved -> FAIL (pending).
A policy whose required sensors did not collect -> FAIL (not collected), never PASS by absence.

Modules:
  allow     the allowlist: schema, matching, owner-only approval at a real terminal
  sensors   independent collectors (Windows today; linux.py for the Debian network-namespace runner)
  scenarios the controlled local test server (canaries, workers, WebRTC) and the scenario table
  audit     static source audit (network-capable components, dispositions) and embedded-URL binary audit
  gate      runs scenarios x sensors, correlates, writes the machine-readable result and the release verdict
"""
