"""The page set the benches load, generated the same way every run (fixed seeds, gzip with mtime 0).

The article imitates the shape of a Wikipedia article as captured on 2026-09-26: about 100 KB of gzip HTML, two
style sheets (one for print), three scripts (async, defer and a classic one at the end), two web fonts, a lead image,
five thumbnails, an SVG logo and a favicon. Text is pseudo-words from a fixed vocabulary, so it compresses roughly
like prose. Images are valid PNGs of noise (incompressible, like photographs). The fonts are font-shaped blobs: the
browser fetches them because the style sheet uses them, then its font sanitiser rejects them; the bytes are what
matters here. There is no video: a valid one cannot be made without an encoder, so S2/S3 (media prefs) are not
exercised by B1 (the result says so).

Every bench page carries a small inline script (NB_JS) that reports to the unshaped report host: DOMContentLoaded,
load, paint timings when the browser exposes them, and asks for the next step. Its bytes are part of the HTML. The
article waits `settle` seconds after its load event before moving on, so requests that do not hold up the load
event (the favicon) land in the same step every run.
"""
import gzip
import hashlib
import json
import random
import struct
import zlib

SEED = 20261003
REPORT = "https://report.netbench.test/"

NB_JS = r"""
var NB=(function(){
 var q=new URLSearchParams(location.search), run=q.get('run')||'', step=q.get('step')||'';
 var R='%REPORT%';
 function mark(ev, extra, st){
  var u=R+'mark?run='+encodeURIComponent(run)+'&step='+encodeURIComponent(st||step)+'&ev='+encodeURIComponent(ev);
  return fetch(u,{method:'POST',body:JSON.stringify(extra||{}),cache:'no-store'}).then(function(r){return r.text();}).catch(function(){return '';});
 }
 function next(){
  return fetch(R+'next?run='+encodeURIComponent(run)+'&step='+encodeURIComponent(step),{cache:'no-store'})
   .then(function(r){return r.json();}).then(function(j){
    if(j && j.url){ return mark('start',{},j.step).then(function(){ location.href=j.url; }); }
   }).catch(function(e){ mark('error',{e:String(e)}); });
 }
 function timing(){
  var o={paint:{},nav:null};
  try{ performance.getEntriesByType('paint').forEach(function(p){ o.paint[p.name]=p.startTime; }); }catch(e){}
  try{ var n=performance.getEntriesByType('navigation')[0]; if(n){ o.nav={dcl:n.domContentLoadedEventEnd,load:n.loadEventStart,
       response_end:n.responseEnd,transfer:n.transferSize,proto:n.nextHopProtocol}; } }catch(e){}
  try{ o.resources=performance.getEntriesByType('resource').length; }catch(e){}
  return o;
 }
 return {q:q,run:run,step:step,mark:mark,next:next,timing:timing};
})();
""".replace("%REPORT%", REPORT)

ARTICLE_JS = """
document.addEventListener('DOMContentLoaded',function(){ NB.mark('dcl'); });
addEventListener('load',function(){ setTimeout(function(){ NB.mark('load',NB.timing()).then(function(){
  setTimeout(NB.next,(+NB.q.get('settle')||0)*1000); }); },0); });
"""

B2_JS = """
async function nbMain(){
 var down=+NB.q.get('down'), up=+NB.q.get('up');
 await fetch('/blob/1024?warm='+NB.step,{cache:'no-store'}).then(function(r){return r.arrayBuffer();});
 await NB.mark('dl-begin');
 var r=await fetch('/blob/'+down+'?dl='+NB.step,{cache:'no-store'}); var b=await r.arrayBuffer();
 await NB.mark('dl-done',{bytes:b.byteLength});
 var body=new Uint8Array(up), x=2463534242;
 for(var i=0;i<up;i++){ x^=x<<13; x^=x>>>17; x^=x<<5; body[i]=x&255; }
 await NB.mark('ul-begin');
 var s=await fetch('/sink?ul='+NB.step,{method:'POST',body:body,cache:'no-store'}).then(function(r){return r.json();});
 await NB.mark('ul-done',s);
 await NB.next();
}
addEventListener('load',function(){ nbMain().catch(function(e){ NB.mark('error',{e:String(e)}).then(NB.next); }); });
"""

B4_JS = """
async function nbMain(){
 var size=+NB.q.get('size'), secs=+NB.q.get('secs'), gap=+NB.q.get('gap');
 await NB.mark('wl-begin');
 var r=await fetch('/blob/'+size+'?slow='+NB.step,{cache:'no-store'}); var rd=r.body.getReader(), got=0, t0=Date.now();
 while(Date.now()-t0<secs*1000){ var c=await rd.read(); if(c.done){break;} got+=c.value.byteLength;
   await new Promise(function(res){ setTimeout(res,gap); }); }
 await NB.mark('wl-end',{bytes:got});
 try{ rd.cancel(); }catch(e){}
 await NB.next();
}
addEventListener('load',function(){ nbMain().catch(function(e){ NB.mark('error',{e:String(e)}).then(NB.next); }); });
"""

B5_JS = """
addEventListener('load',function(){
 fetch('https://h1b.netbench.test/trickle/'+(+NB.q.get('busy'))+'?s='+NB.step,{cache:'no-store'}).then(function(r){return r.text();})
  .then(function(t){ NB.mark('busy-done',{len:t.length}); }).catch(function(e){ NB.mark('busy-error',{e:String(e)}); });
 NB.mark('load').then(function(){
  setTimeout(function(){
   NB.mark('pre-reuse').then(function(){ return fetch('/ping.txt?after-idle='+NB.step,{cache:'no-store'}); }).then(function(r){return r.text();})
    .then(function(t){ return NB.mark('reuse',{len:t.length}); }).then(NB.next)
    .catch(function(e){ NB.mark('error',{e:String(e)}).then(NB.next); });
  }, (+NB.q.get('idle'))*1000);
 });
});
"""

B3_JS = """
async function nbMain(){
 for(var i=0;i<6;i++){
  await fetch('/ping.txt?n='+i+'&s='+NB.step,{cache:'no-store'}).then(function(r){return r.text();});
  await new Promise(function(res){ setTimeout(res,700); });
 }
 await NB.mark('load',NB.timing());
 await NB.next();
}
addEventListener('load',function(){ nbMain().catch(function(e){ NB.mark('error',{e:String(e)}).then(NB.next); }); });
"""


def _vocab(rng, n=420):
    syl = ["ba", "go", "ri", "la", "ne", "to", "su", "ma", "ki", "do", "re", "vi", "an", "el", "or", "us", "te",
           "ca", "mo", "lu", "pe", "sa", "ti", "no", "ra", "de", "li", "ve", "pa", "gu"]
    words = set()
    while len(words) < n:
        words.add("".join(rng.choice(syl) for _ in range(rng.choice((1, 2, 2, 3, 3, 4)))))
    return sorted(words)


def _sentence(rng, vocab, lo=8, hi=22):
    w = [rng.choice(vocab) for _ in range(rng.randint(lo, hi))]
    return w[0].capitalize() + " " + " ".join(w[1:]) + "."


def png_noise(w, h, seed):
    """A valid RGB PNG of noise: deterministic for a seed, about w*h*3 bytes."""
    rng = random.Random(seed)
    raw = b"".join(b"\x00" + rng.randbytes(w * 3) for _ in range(h))

    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _js_file(rng, vocab, target):
    out, i = [], 0
    while sum(len(x) for x in out) < target:
        out.append(f"function nb_{i}(a,b){{var s=\"{' '.join(rng.choice(vocab) for _ in range(12))}\";"
                   f"return a+b+s.length*{rng.randint(1, 999)};}}\n")
        i += 1
    return "".join(out).encode()


def _css_file(rng, target, fonts=False):
    out = []
    if fonts:
        out.append("@font-face{font-family:'NB Serif';src:url(/fonts/serif.woff2) format('woff2');font-display:swap}\n"
                   "@font-face{font-family:'NB Sans';src:url(/fonts/sans.woff2) format('woff2');font-display:swap}\n"
                   "h1,h2{font-family:'NB Serif',serif}body{font-family:'NB Sans',sans-serif;max-width:60em;margin:auto}\n"
                   ".thumb{float:right;margin:0 0 1em 1em}\n")
    i = 0
    while sum(len(x) for x in out) < target:
        out.append(f".c{i}{{margin:{rng.randint(0, 9)}px {rng.randint(0, 9)}px;color:#{rng.randrange(4096):03x};"
                   f"padding:{rng.randint(0, 5)}px}}\n")
        i += 1
    return "".join(out).encode()


def page(title, body, js, head_extra=""):
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{title}</title>"
            f"<script>{NB_JS}{js}</script>{head_extra}</head><body>{body}</body></html>").encode()


def _article(rng, vocab, target_gz=100_000):
    head = ('<link rel=stylesheet href=/static/site.css><link rel=stylesheet media=print href=/static/print.css>'
            '<script async src=/static/startup.js></script><script defer src=/static/modules.js></script>'
            '<link rel=icon href=/favicon.ico>')
    parts = ['<header><img src=/img/logo.svg width=120 height=40 alt=logo><h1>Gorilla</h1></header><main>',
             '<figure class=thumb><img src=/img/lead.png width=165 height=165 alt=lead></figure>']
    sec, thumbs = 0, 0
    while True:
        html_so_far = "".join(parts)
        if len(gzip.compress(html_so_far.encode(), 6, mtime=0)) >= target_gz:
            break
        sec += 1
        parts.append(f"<h2 id=s{sec}>{' '.join(rng.choice(vocab) for _ in range(3)).title()}</h2>")
        if thumbs < 5 and sec % 3 == 0:
            thumbs += 1
            parts.append(f"<figure class=thumb><img src=/img/thumb{thumbs}.png width=92 height=92 alt=t{thumbs}></figure>")
        for _ in range(rng.randint(3, 6)):
            parts.append("<p class=c%d>%s</p>" % (rng.randrange(800), " ".join(_sentence(rng, vocab) for _ in range(rng.randint(3, 7)))))
        if sec % 5 == 0:
            parts.append("<table class=wikitable>" + "".join(
                "<tr>" + "".join(f"<td>{rng.choice(vocab)} {rng.randint(1, 9999)}</td>" for _ in range(4)) + "</tr>"
                for _ in range(8)) + "</table>")
    parts.append("<ol class=references>" + "".join(f"<li id=r{i}>{_sentence(rng, vocab, 5, 12)}</li>" for i in range(60))
                 + "</ol></main><script src=/static/legacy.js></script>")
    return page("Gorilla - NetBench", "".join(parts), ARTICLE_JS, head), thumbs


def build():
    """-> {path: {"body": bytes, "type": str, "cache": bool, "gzip": bytes|None}} and the manifest."""
    rng = random.Random(SEED)
    vocab = _vocab(rng)
    files = {}

    def add(path, body, ctype, cache=True, compress=False):
        files[path] = {"body": body, "type": ctype, "cache": cache,
                       "gzip": gzip.compress(body, 6, mtime=0) if compress else None}
    art, thumbs = _article(rng, vocab)
    add("/wiki/Gorilla", art, "text/html; charset=utf-8", cache=False, compress=True)
    add("/static/site.css", _css_file(rng, 40_000, fonts=True), "text/css", compress=True)
    add("/static/print.css", _css_file(rng, 8_000), "text/css", compress=True)
    add("/static/startup.js", _js_file(rng, vocab, 120_000), "text/javascript", compress=True)
    add("/static/modules.js", _js_file(rng, vocab, 150_000), "text/javascript", compress=True)
    add("/static/legacy.js", _js_file(rng, vocab, 30_000), "text/javascript", compress=True)
    add("/fonts/serif.woff2", random.Random(SEED + 1).randbytes(30_000), "font/woff2")
    add("/fonts/sans.woff2", random.Random(SEED + 2).randbytes(30_000), "font/woff2")
    add("/img/lead.png", png_noise(165, 165, SEED + 3), "image/png")
    for i in range(1, thumbs + 1):
        add(f"/img/thumb{i}.png", png_noise(92, 92, SEED + 10 + i), "image/png")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">' + "".join(
        f'<circle cx="{rng.randint(0, 120)}" cy="{rng.randint(0, 40)}" r="{rng.randint(1, 6)}" fill="#{rng.randrange(4096):03x}"/>'
        for _ in range(110)) + "</svg>").encode()
    add("/img/logo.svg", svg, "image/svg+xml", compress=True)
    add("/favicon.ico", random.Random(SEED + 4).randbytes(1150), "image/x-icon")
    add("/ping.txt", b"pong\n", "text/plain", cache=False)
    small = "<p>" + " ".join(_sentence(rng, vocab) for _ in range(20)) + "</p>"
    add("/b2.html", page("B2", small, B2_JS), "text/html; charset=utf-8", cache=False, compress=True)
    add("/b3.html", page("B3", small, B3_JS), "text/html; charset=utf-8", cache=False, compress=True)
    add("/b4.html", page("B4", small, B4_JS), "text/html; charset=utf-8", cache=False, compress=True)
    add("/b5.html", page("B5", small + "<img src=/img/thumb1.png width=92 height=92 alt=t>", B5_JS),
        "text/html; charset=utf-8", cache=False, compress=True)
    return files


def manifest(files):
    """Path -> size, gzip size and sha256; and one digest over all of them (proves the same set every run)."""
    rows = {p: {"bytes": len(f["body"]), "gzip_bytes": len(f["gzip"]) if f["gzip"] else None,
                "sha256": hashlib.sha256(f["body"]).hexdigest()} for p, f in sorted(files.items())}
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    article = [p for p in rows if not p.startswith(("/b", "/ping"))]
    wire = sum(rows[p]["gzip_bytes"] or rows[p]["bytes"] for p in article)
    return {"digest": digest, "files": rows, "article_set_bytes_compressed": wire}


_BLOCK = random.Random(SEED + 99).randbytes(1 << 20)


def blob_chunks(n, chunk=65536):
    """Deterministic incompressible bytes, n of them, in chunks (a 1 MiB block repeated)."""
    off = 0
    while off < n:
        k = min(chunk, n - off)
        s = off % len(_BLOCK)
        piece = _BLOCK[s:s + k]
        if len(piece) < k:
            piece += _BLOCK[:k - len(piece)]
        yield piece
        off += k
