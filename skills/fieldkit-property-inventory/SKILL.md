---
name: fieldkit-property-inventory
description: Compare a tenant's own photos of a rented room or flat with the letting agent's inventory / check-in / check-out report (InventoryBase "generate/task" pages and similar), sort every photo under the report item it belongs to, drop duplicates, draft a short dispute comment per item, and prepare per-item folders for upload. Use when the owner gives an inventory report link plus a folder of photos, says "do the inventory", "compare my photos with their report", "label and upload my photos", or asks to repeat this for another property. Also use for contact sheets of any large photo folder.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Tenant photos against an inventory report

Tool: `photokit` (card in `fieldkit/desk/tools.yaml`; code `kits/photokit/photokit.py` in this repo).
Run it with `python -I`. It writes only into the OUT folder you name and never touches originals.
First used 2026-10-12: 136 photos, one bedroom, 13 report items, about 2 hours including building the tool.

## What the owner wants out of it

- Every photo sorted under the report item it shows (door, walls, ceiling, window, bed...).
- Duplicates left out. Keep the sharper or wider shot, and any shot that adds context.
- One short, factual comment per item. Quote what the report claims, then list what the
  owner's photos show that the report's photos do not, with the photo time stamps.
- The clerk's usual blind spots. Spell each one out when it applies:
  - a mattress photographed only with its cover on ("mattress cover in good condition");
  - distant, flat-lit wall shots that hide cracks, holes and lifting paper;
  - "no visible cracks" on a ceiling rose;
  - window reveals and sills shown only from across the room.

## One-time setup (what has to be installed)

- **First run `python -I photokit.py doctor`** (`--install` pip-installs Pillow from PyPI). It also reports
  whether Chrome is present. PhotoKit needs nothing else. On a new machine, clone this repository:
  `kits/photokit` is the tool.
- **Claude in Chrome**, needed for photo uploads: the in-app browser cannot attach local files.
  - Store page: <https://chromewebstore.google.com/detail/fcoeoabgfenejglbffodgkkbkcdhcgfn>.
    Before installing, check the publisher shows as Anthropic. It needs a paid Claude plan.
  - Run it in a **dedicated Chrome profile** with no personal logins. Profile folder
    `%LOCALAPPDATA%\InventoryChrome`. Start it via WMI so it outlives the agent session:
    `Invoke-CimMethod Win32_Process Create @{CommandLine='"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="%LOCALAPPDATA%\InventoryChrome" --no-first-run <store url> <report url>'}`
  - The **owner** clicks "Add to Chrome" and signs in to Claude inside the extension.
    An agent must not enter credentials, and it cannot click inside Chrome.
  - Cost, so state it whenever proposing it: the extension updates itself from the Chrome Web Store, it
    can act on every site open in that profile, and Anthropic sees the pages and actions taken through it.
    Keep that profile for inventory work only.
  - Check it with `list_connected_browsers`, then `tabs_context_mcp`.
- Claude Code permission mode **Manual** for posting (Auto refused the writes). Never Bypass.

## Procedure

1. **Read the report as text.** In the built-in browser, call `get_page_text` on the link.
   Note each item: ref (5.1, 5.2...), name, description, condition. These are the clerk's claims.
2. **Get the report's photos, labelled by ref.** InventoryBase thumbnails sit in the page with
   a caption "Ref #5.4". Run this in the page with `javascript_tool`:
   ```js
   const out=[],seen=new Set();document.querySelectorAll('img').forEach(i=>{const s=i.src;if(!s.includes('thumbnail')||seen.has(s))return;let n=i,lab='';for(let k=0;k<5&&n;k++){n=n.parentElement;const m=(n?.innerText||'').match(/Ref #[\d.]+/);if(m&&n.innerText.length<80){lab=m[0];break;}}seen.add(s);out.push(lab+'|'+s)});out.join('\n')
   ```
   Turn the result into `label|url` lines, such as `5.4_a|https://...-thumbnail.jpg`.
   Then run `photokit fetch LIST OUT\theirs` and `photokit sheet OUT\theirs OUT\sheets_theirs --per 16 --size 400`.
   An image labelled "Tenant Uploaded Attachment" is one the owner has already added: do not upload it again.
3. **Look at the owner's photos.** Run `photokit sheet SRC OUT\sheets_mine --per 12 --size 480 --strip IMG_YYYYMMDD_`,
   then Read every sheet. Phone photos carry a burned-in date and time stamp, so cite photos by that time.
4. **Write the mapping**, one line per item, as in `5.4 192948 192954 ...`. Item choice for borderline photos:
   - splits or lifting paper at a window reveal: walls (5.4); the sill junction itself: window (5.9);
   - a gap between a fire surround and the chimney breast: fireplace;
   - a cracked ceiling rose: ceiling and coving rather than lighting;
   - marks just above the mattress: the bed (furnishings).
   Then run `photokit check SRC MAP --prefix IMG_YYYYMMDD_` until it prints OK: every photo mapped exactly once.
5. **Duplicates.** Start with `photokit dupes SRC --window 1 --threshold 16 --sheet OUT\pairs` for burst shots.
   Add any pairs you noticed on the sheets, then run `photokit compare SRC OUT\pairs A:B ...`.
   **Decide by looking at the pairs. Never decide by the hash distance.** Measured 2026-10-12:
   - comparing all pairs grouped 45 unrelated plain-white wall photos together;
   - true repeats taken seconds apart scored distance 17 to 30, above any sane threshold;
   - only the side-by-side look was reliable.
   Put the dropped photos on a `skip` line, with a `#` comment saying why.
6. **Sort.** Run `photokit sort SRC MAP "<SRC> - sorted by report item" --prefix ...`.
   This gives one folder per ref, 2000 px images, date stamp kept. Copy the mapping into that folder.
7. **Draft the comments.** Write them in British English, blunt and factual, with no adjectives that you cannot see
   in a photo. Do not name a cause you cannot prove: say "dark spot marks", not "bed bugs", and "black deposits",
   not "mould". Show the owner every comment before anything is posted.
8. **Post and upload** only after the owner's yes in chat. That yes covers that one report.

## Posting and uploading: what works and what does not (2026-10-12)

- InventoryBase tenant page (jQuery; API at `api.inventorybase.com`):
  - each item row is `tr#ref-5.12` (use `getElementById`: `#ref-5.12` is not a valid CSS selector);
  - comment box `textarea.tenant-comment-v2-adder`. Its **blur** handler turns the text into a pending
    comment (`[data-saved="false"]`). The Save button (`button.tenant-comment-btn.save`, hidden) POSTs
    **every** pending comment of that item;
  - a camera icon opens an uploader with a hidden `input[type=file][multiple]` (jpg/png).
    Its **change** handler POSTs each file to `/contact-attachments`;
  - your own saved comment shows as "Anonymous: ..." (or a masked email) with the date.
- **Posting comments: WORKED 2026-10-12** (9 items). This is the method:
  - in one `javascript_tool` call per item: set `t.value`, then `jQuery(t).trigger('blur')`,
    then `jQuery(saveBtn).trigger('click')`, wait about 3.5 s;
  - read back the new `data-comment-id`. Exactly one new id per item means it saved;
  - a plain `.click()` without the blur sends nothing.
- **Duplicate trap:** any earlier real click in a box followed by leaving it has already queued a pending
  comment. One Save then posts two. Check the ids. Remove the extra with its bin button; it asks "Delete comment?".
  The DELETE returns 204.
- **Coordinate trap:** `computer` coordinates are in the screenshot frame (800 px wide), not the page's
  `innerWidth` (1024). Coordinates taken from `getBoundingClientRect` land in the wrong place:
  on 2026-10-12 one click opened a photo viewer instead. Scale by 800/innerWidth, or avoid clicking (use the jQuery triggers).
- **Uploading photos: WORKED 2026-10-12 through Claude in Chrome**. 106 photos went in, in about 10 minutes:
  - label each input first, in JS: `input.setAttribute('aria-label','photo-upload-5.4')`;
  - `find` it, then `file_upload` with that ref and up to 9.5 MB of paths per call;
  - check `#tenant-attachments-container-<item-id> a` counts afterwards.
  - The page **replaces the input after every upload**. Relabel and re-`find` before each batch.
    A stale ref is accepted by the tool but uploads nothing.
- **REPORT PHOTO CAP.** The whole report accepted **107 tenant photos**. After that every POST returns 400:
  "You have reached the maximum number of photos for this report", and the server asks you to email the clerk.
  So, **before uploading**:
  - count the photos against the cap;
  - if they exceed it, choose what to send: the worst defects first, one photo per defect, items in order of
    severity, and never upload in file-name order. On 2026-10-12 the 14 most damning wall photos missed the cap
    because they sorted last;
  - put the remainder in a zip with an index (file, time, what it shows) and the item comment, for the owner to email.
  - To read the server's reason for a refusal, hook `jQuery(document).ajaxError` and collect `responseText`.
    The resource-timing buffer fills at about 150 entries, so call `performance.setResourceTimingBufferSize(2000)` first.
- **Clerk email (overflow photos), done 2026-10-12 through Gmail in the inventory Chrome profile.**
  - Send **from the tenant's account**: the one on the tenancy, not the owner's own. Gmail `u/0`, `u/1`... are
    different accounts, so check the tab title shows the right address before typing anything.
  - Click Compose **once**. A second click opens a second, empty window, and text can land in the wrong one.
  - Attach by labelling `input[type=file][name=Filedata]` (aria-label), then `find`, then `file_upload`.
  - Before Send, read the dialog back in JS: the `[email]` chips, `input[name=subjectbox]`, the
    `div[aria-label="Message Body"]` text, and the attachment shown exactly once. Send only after the owner's yes.
  - Afterwards, open `#sent` and confirm the message is the top row. Close your own tab.
  - Sign as the tenant named on the tenancy. Do not invent or guess a name.
  - Gmail page text exposes the whole mailbox. Read only what the task needs; never repeat personal data or keys from it.
- Older note, same day: the built-in browser has no file-upload tool.
  - `photokit serve` (local-only, read-only, idle stop) plus a page `fetch` into the file input was
    **refused by the permission classifier ("Security Weaken")**, even in Manual mode. Do not work around that.
  - Working route: **Claude in Chrome** `file_upload` on each item's `input[type=file]`, max 10 MB per call.
    Check `list_connected_browsers` first. The owner has to install and connect it; say who can act through it.
  - Otherwise the owner drags each sorted folder into the matching item's uploader (about 5 minutes for 9 folders).
- Permission mode: comment posting was refused in **Auto** mode ("External System Writes"). It worked after
  the owner switched to **Manual**. Never pick Bypass. When something is refused, stop, report it and keep
  `COMMENTS.txt` ready to paste.
- Keep screenshots few and small (`scale` 0.5-0.6). Read with `get_page_text` and `javascript_tool`.

## Finish

- Report one line per item: photos, the main differences, and what is posted versus what is pending.
- Scratch work goes in the session scratchpad. Any new script goes into `kits/photokit` with a test,
  never into a loose folder. Then run `fieldkit sweep`.
