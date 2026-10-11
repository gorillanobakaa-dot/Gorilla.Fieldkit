"""The words of a fetched page, and the comparison that decides whether a quote is in them."""
import html
import re
import unicodedata
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "template", "svg", "head"}
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header",
         "footer", "blockquote", "pre", "table", "ul", "ol", "dd", "dt", "figcaption", "td", "th", "title"}
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "application/ld+json")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.title, self._in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP and tag != "head":
            self.skip += 1
        if tag == "title":
            self._in_title = True
        if tag in BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and tag != "head" and self.skip:
            self.skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def charset(content_type, body):
    m = re.search(r"charset=[\"']?([\w-]+)", content_type or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
        if m:
            return m.group(1).decode("ascii", "replace")
    return m.group(1) if m else "utf-8"


def extract(body, content_type):
    """-> (text, title, how). text is None when the type is not text (an image, an archive); how says why."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf" or body[:5] == b"%PDF-":
        return _pdf(body)
    if not (ct.startswith(TEXT_TYPES) or not ct):
        return None, "", f"no text in {ct}"
    enc = charset(content_type, body)
    try:
        s = body.decode(enc, errors="replace")
    except LookupError:
        s = body.decode("utf-8", errors="replace")
    if "html" in ct or (not ct and re.search(r"<html|<body|<p[ >]", s[:2000], re.I)):
        p = _Text()
        p.feed(s)
        p.close()
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return text, html.unescape(p.title).strip(), "html"
    return s, "", ct or "text"


def _pdf(body):
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None, "", "a PDF, and pypdfium2 is not installed (pip install pypdfium2)"
    try:
        doc = pdfium.PdfDocument(body)
    except Exception as e:                      # a damaged or encrypted PDF is a fact about the page, not a crash
        return None, "", f"a PDF that could not be opened ({e})"
    pages = []
    for i in range(len(doc)):
        tp = doc[i].get_textpage()
        pages.append(tp.get_text_range())
    return "\n\n".join(pages), "", f"pdf, {len(pages)} page(s)"


def fold(s):
    """What does not make two passages different: Unicode form, case, curly quotes and dashes, invisible
    characters, and every run of white space."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    s = s.translate({0x2018: "'", 0x2019: "'", 0x201A: "'", 0x201B: "'", 0x201C: '"', 0x201D: '"', 0x201E: '"',
                     0x00AB: '"', 0x00BB: '"', 0x2013: "-", 0x2014: "-", 0x2212: "-", 0x00AD: None,
                     0x200B: None, 0x200C: None, 0x200D: None, 0xFEFF: None})
    return re.sub(r"\s+", " ", s).strip()


def contains(text, quote):
    """Is the quote in the text, as words? Also tried with words broken across lines by a hyphen (PDFs)."""
    q, t = fold(quote), fold(text)
    if not q:
        return False
    if q in t:
        return True
    return re.sub(r"(\w)- (\w)", r"\1\2", q) in re.sub(r"(\w)- (\w)", r"\1\2", t)
