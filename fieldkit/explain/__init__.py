"""Written explanations, on screen, at no model cost.

    fieldkit explain                      what can be explained
    fieldkit explain TOPIC                in English
    fieldkit explain TOPIC --lang ro      in another language

# WHY THIS EXISTS

On 2026-10-13 the owner asked what "Enable Mesh" does on an EE hub. Answering it
properly took reading the hub's firmware, its language file and its error
strings, then a long explanation. The next person to ask will get the same
answer, and there is no reason for a model to derive it again, burn the tokens
again, and risk getting it slightly different.

So the answer is a FILE. Reading it costs nothing, says the same thing every
time, and can be corrected once for everybody.

# THE RULE THIS FOLLOWS

An explanation goes in here when it is TRUE REGARDLESS OF THE MACHINE: what mesh
is, why a 2.4 GHz antenna cannot help a 3500 MHz carrier, why an SSID is not a
band. Anything that depends on THIS machine's state belongs in a doctor, which
measures, not here.

The test: if the answer would change on somebody else's laptop, it is not an
explanation, it is a diagnosis. Keep them apart.

# LANGUAGES

English is the baseline and must always exist: `topics/<name>.md`. Translations
are `topics/<name>.<lang>.md`, matching the convention already used by
docs/academic/GUIDE.<lang>.md.

Per the owner's "grey man" decision of 2026-10-13, a topic must NEVER exist in
Romanian alone. `check()` enforces that, and the test fails on an orphan.
"""
import os
import re

TOPIC_DIR = os.path.join(os.path.dirname(__file__), "topics")
BASE_LANG = "en"


def _path(topic, lang=None):
    if lang and lang != BASE_LANG:
        return os.path.join(TOPIC_DIR, f"{topic}.{lang}.md")
    return os.path.join(TOPIC_DIR, f"{topic}.md")


def topics():
    """-> {topic: [languages available]}, English always first."""
    out = {}
    if not os.path.isdir(TOPIC_DIR):
        return out
    for f in sorted(os.listdir(TOPIC_DIR)):
        if not f.endswith(".md"):
            continue
        stem = f[:-3]
        m = re.match(r"^(.*)\.([a-z]{2})$", stem)
        if m:
            out.setdefault(m.group(1), []).append(m.group(2))
        else:
            out.setdefault(stem, [])
    for k in out:
        out[k] = [BASE_LANG] + sorted(out[k])
    return out


def explain(topic, lang=None):
    """-> the text, or a refusal. Reads a file. No model, no tokens, no variance."""
    p = _path(topic, lang)
    if not os.path.exists(p) and lang and lang != BASE_LANG:
        # fall back to English rather than saying nothing, and SAY that it fell back
        base = _path(topic)
        if os.path.exists(base):
            with open(base, encoding="utf-8") as fh:
                return {"topic": topic, "lang": BASE_LANG, "requested": lang,
                        "fell_back": True, "text": fh.read()}
    if not os.path.exists(p):
        have = topics()
        return {"topic": topic, "error": f"no explanation for {topic!r}",
                "available": sorted(have)}
    with open(p, encoding="utf-8") as fh:
        return {"topic": topic, "lang": lang or BASE_LANG, "fell_back": False,
                "text": fh.read()}


def check():
    """Every translation must have an English original. The grey-man rule, enforced."""
    problems = []
    for topic, langs in topics().items():
        if not os.path.exists(_path(topic)):
            problems.append(f"{topic} exists only in {', '.join(l for l in langs if l != BASE_LANG)} "
                            f"with no English original (grey man: a topic must never be in one "
                            f"language alone)")
    return {"topics": len(topics()), "problems": problems, "ok": not problems}


def lines(d):
    if "text" in d:
        out = []
        if d.get("fell_back"):
            out.append(f"[no {d['requested']} version yet; showing English]\n")
        out.append(d["text"])
        return out
    if "error" in d:
        return [d["error"], "", "available:"] + [f"  {t}" for t in d.get("available", [])]
    if "problems" in d:
        return [f"{d['topics']} topic(s)"] + [f"  PROBLEM {p}" for p in d["problems"]]
    return [str(d)]


def exit_code(d):
    return 0 if ("text" in d or d.get("ok")) else 3
