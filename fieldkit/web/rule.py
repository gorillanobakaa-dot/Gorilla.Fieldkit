"""When is an HTTP answer "blocked or empty", so that the browser is tried? One rule, one place.

Gorilla OpenCode's fetch tool (internal/llm/tools/fetch.go) tells a script-only page apart from an empty one in its own
words ("This is NOT the same as the page being empty") and returns that note to the model; it has no browser to try.
This rule is the Fieldkit side of the same question, over the evidence fetcher's snapshot record.
"""
BLOCK_STATUS = (401, 403, 406, 429, 451, 503)
MIN_TEXT_CHARS = 200          # a real article is longer; a bot-check or a script-only shell is not


def blocked_or_empty(rec):
    """-> '' when the HTTP answer is usable, else the reason the browser should be tried."""
    status = rec.get("status") or 0
    if status in BLOCK_STATUS:
        return f"HTTP {status}"
    if status >= 400:
        return ""                                     # 404, 410, 5xx: the page is gone or broken; a browser will not help
    if rec.get("challenge"):
        return "bot check page"
    if not rec.get("has_text"):
        return "no text in the answer"
    if (rec.get("text_chars") or 0) < MIN_TEXT_CHARS:
        return f"only {rec.get('text_chars') or 0} characters of text"
    return ""


def text_unusable(text):
    """The same question for text a browser rendered: a bot check or next to nothing."""
    from ..evidence.fetch import CHALLENGE
    text = text or ""
    return len(text) < MIN_TEXT_CHARS or (len(text) < 2000 and bool(CHALLENGE.search(text)))
