---
name: fieldkit-screen-compare
description: Compare two screenshots the way a person does and measure what differs - find the place with a pixel grid, crop it, enlarge it without smoothing, stack two side by side, make a contact sheet of many, and measure the text inside a box (stroke width, ink, blur, coloured LCD smoothing, extent); fetch an old Mac's screen first with fieldkit mac shot. Use when text looks thin and crisp on one machine and thick, bold or blurry on another, when someone asks "is that anti-aliasing or the font?", when a font or theme change must be proven on screen, or when screenshots from two machines or two builds must be compared.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Comparing screens and fonts

Born 2026-10-22. The owner's Linux about:about was thin and crisp; the MacBook Air's looked "oversmoothed thick
blurry almost". The question was anti-aliasing or serif. These commands answered it with numbers.

## The order

1. Get both screens. On an old Mac: `fieldkit mac shot NAME --out mac.png` (screencapture over SSH, fetched; a
   dropped Wi-Fi is retried three times). Your own screenshots are files already.
2. `fieldkit shot sheet FILE...` when there are many: find the right one by eye.
3. `fieldkit shot grid FILE` draws a pixel grid every 100 px: read the box `X,Y,W,H` around the text off it.
4. `fieldkit shot crop FILE X,Y,W,H --zoom 3` cuts the box and enlarges with NEAREST (each pixel becomes a 3x3
   block: what is seen is what was on the screen, never a smoothed copy).
5. `fieldkit shot text-compare A BOX_A B BOX_B --out both.png` measures the SAME sentence on both and stacks the
   two boxes at 3x. Read: strokes (Bx A), ink, blur (half-tone), coloured smoothing, extent.
6. Change ONE thing, take the same screenshot again, run 5 again. A change whose numbers do not move did nothing.

## How to read the numbers (same text, same size, two screens; relative, not a standard)

- strokes 1.25x or more: B is clearly bolder. 2026-10-22: Mac about:about 2.97 px vs Linux 2.12 px = 1.40x.
- extent differs for the same sentence: a different font (or size), not smoothing.
- coloured smoothing above 0: LCD subpixel smoothing (red and blue fringes); 0: greyscale.
- equal numbers after a change: the change did nothing. 2026-10-22: the macOS global AppleFontSmoothing key, set
  and removed, gave identical numbers on a Gorilla page, so it is NOT a fix for the browser and was not added to
  tune_sierra.sh.

## What 2026-10-22 established on the Air (test4)

- Web page text in Segoe UI on the Air: 2.12 px, the same as Linux. Thin already.
- The browser's OWN pages and interface (about:, settings, menus) use macOS's interface font (San Francisco) with
  LCD smoothing: that is the thick look. The fix is in the browser build (Gorilla's own UI in Segoe UI on the Mac),
  not in macOS.
- Segoe UI vs Segoe UI Variable on the Air: 1.00x (the Variable font's size designs are not used on 10.13).

## Traps

- `open -a App about:about` and `open -a App data:...` are read as FILE names on macOS: write a local .html and
  open that, or `osascript -e 'tell application "Gorilla Unleashed" to open POSIX file "/tmp/x.html"'`.
- `/usr/local/bin/python3` on the Air is not on the SSH PATH: give the full path.
- Compare boxes holding the same words; a box with a picture or a border in it measures the picture.
