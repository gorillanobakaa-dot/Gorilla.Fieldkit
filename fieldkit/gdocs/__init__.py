"""Gorilla.Documentation.IBM.Style - Fieldkit's dual-track documentation workflow.

Every module group of fieldkit/ is documented twice, by DualTrackAgent's
dual_track.py: a layman track (for someone who has never opened a terminal)
and a developer track (for someone who will audit or change the code). This
package turns the hand-run routine into commands, so every writer gets the same
rules and every rendered document is checked the same way:

    fieldkit docs plan                  groups, their state (fresh / stale) and the steps
    fieldkit docs prep [GROUP...]       stage sources, run dual_track prep, add the writer brief
    fieldkit docs render [GROUP...]     dual_track render, then the Gorilla checks (exit 3 on findings)
    fieldkit docs check [--strict]      coverage + Gorilla checks on the committed docs, no rendering
    fieldkit docs index                 write docs/dual-track/README.md

The pipeline gorilla-documentation-ibm-style chains these (`fieldkit next
gorilla-documentation-ibm-style`). The rules a writer must follow are in
WRITER_BRIEF.md beside this file; the deterministic checks are in checks.py.
"""
