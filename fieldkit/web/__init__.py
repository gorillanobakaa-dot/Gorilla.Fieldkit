"""Web pages and web search through the browsers already installed (plan items P3, P3b). No extra dependency.

    fieldkit web browsers [--json]                       every installed browser, its engine and how it is driven
    fieldkit web fetch URL [--browser auto|NAME] [--engine chromium|gecko|webkit] [--timeout S] [--json]
    fieldkit web search QUERY [--engine NAME] [-n N] [--json]
    fieldkit web doctor [--json]                         what cannot be driven, and the one step that would fix it

`auto` reads the page with the evidence fetcher first (robots.txt obeyed, private addresses refused) and goes to a
browser only when that answer is blocked or empty (rule.py). Drivers (drivers.py, standard library only): Chromium
family `--headless --dump-dom`; Gecko (Firefox 129+, Gorilla Firefox, LibreWolf, Waterfox) WebDriver BiDi over a small
websocket client (wsclient.py); Safari through safaridriver. Every run has a fresh profile in the scratch root, deleted
after, with the vendor channels off; the browser is stopped by PID. Nothing is installed or downloaded: Selenium is
used only for a Firefox older than 129 when it and a geckodriver are already there. Order of trying: fieldkit.local.json
"browser_fetch": {"prefer": [NAMES], "extra": [PATHS]}, the owner-built Gorilla Firefox, other Gecko, Chromium, Safari.
"""
