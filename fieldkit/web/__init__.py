"""Web pages through a real browser when the plain HTTP reader is blocked or gets nothing (plan item P3).

    fieldkit web fetch URL [--browser auto|firefox|safari] [--timeout S] [--json]
    fieldkit web doctor [--install]

`auto` reads the page with the evidence fetcher first (robots.txt obeyed, private addresses refused) and goes to the
browser only when that answer is blocked or empty (rule.py). The browser is the owner's Gorilla Firefox, headless,
with a fresh throwaway profile in the scratch root that is deleted after the fetch, driven by Selenium and a pinned
geckodriver. Safari (safaridriver) is the macOS fallback. Selenium Manager is never used: no download, no telemetry
(SE_AVOID_STATS, SE_OFFLINE; the driver and browser paths are always given). Paths: fieldkit.local.json
"browser_fetch": {"firefox": PATH, "geckodriver": PATH}.
"""
