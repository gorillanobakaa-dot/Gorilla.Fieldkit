"""HTML to text, links and a title with html.parser (standard library). script, style, noscript and template are dropped."""
from html.parser import HTMLParser

SKIP = {"script", "style", "noscript", "template", "head"}
BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
         "header", "footer", "nav", "main", "pre", "blockquote", "hr", "dt", "dd", "form"}


class _P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.out = []                 # text chunks, "\n" for a block break
        self.events = []              # ("a", href, text) and ("t", text), in order
        self._a = None

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in SKIP and tag != "head":
            self.skip += 1
        if tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and self._a is None:
            self._a = [dict(attrs).get("href") or "", []]

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in SKIP and tag != "head" and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and self._a is not None:
            text = " ".join("".join(self._a[1]).split())
            self.events.append(("a", self._a[0], text))
            self._a = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
            return
        if self.skip:
            return
        self.out.append(data)
        if self._a is not None:
            self._a[1].append(data)
        elif data.strip():
            self.events.append(("t", " ".join(data.split())))


def parse(html):
    """-> {"title", "text", "events"}."""
    p = _P()
    p.feed(html or "")
    p.close()
    lines = (" ".join(ln.split()) for ln in "".join(p.out).split("\n"))
    text = "\n".join(ln for ln in lines if ln)
    return {"title": " ".join(p.title.split()), "text": text, "events": p.events}


def to_text(html):
    return parse(html)["text"]
