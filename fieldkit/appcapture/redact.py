"""Redaction for anything appcapture prints: tokens never reach the screen, a log or a transcript."""
import re

SECRET_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-goog-api-key", "x-api-key",
                  "x-goog-user-project-token"}
_PATTERNS = [
    (re.compile(r"ya29\.[\w.\-]+"), "ya29.<redacted>"),                       # Google OAuth access token
    (re.compile(r"(?<![\w/])1//[\w.\-]{10,}"), "1//<redacted>"),              # Google refresh token
    (re.compile(r"AIza[\w\-]{20,}"), "AIza<redacted>"),                       # Google API key
    (re.compile(r"eyJ[\w\-]{8,}\.[\w\-]{8,}\.[\w\-]*"), "<jwt redacted>"),    # any JWT
    (re.compile(r"(?i)\b(Bearer|Basic)\s+[\w.\-+/=]{8,}"), r"\1 <redacted>"),
    (re.compile(r'(?i)("?(?:access_token|refresh_token|id_token|client_secret|api[_-]?key|password)"?\s*[:=]\s*)"[^"]*"'),
     r'\1"<redacted>"'),
    (re.compile(r"(?i)\b((?:access_token|refresh_token|id_token|client_secret)=)[^&\s]+"), r"\1<redacted>"),
]


def text(s):
    """A string with every token-shaped part replaced."""
    if not s:
        return s
    for rx, rep in _PATTERNS:
        s = rx.sub(rep, s)
    return s


def headers(h):
    """{name: value} -> the same with secret headers blanked and token shapes removed from the rest."""
    return {k: ("<redacted>" if k.lower() in SECRET_HEADERS else text(v)) for k, v in (h or {}).items()}
