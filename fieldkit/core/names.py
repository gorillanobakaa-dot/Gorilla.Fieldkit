"""Names the harness did not choose, and what they do to tools that read them.

A wifi SSID, a filename from a download, a device name, a printer, a Bluetooth
peer: all arrive from outside and all can contain anything. This says what a
given name will trip, so a tool can warn instead of silently misbehaving.

See docs/HOSTILE-NAMES.md for the plain-language version.

# THE THREE DISTINCT PROBLEMS, which people wrongly treat as one

1. SEPARATOR. ":" ";" "," "|" "=" and a space separate fields in countless
   tools. A name containing one gets split, and every later field shifts along.
   LOUD: something crashes, so somebody notices.

2. INSTRUCTION. "|" "&" ";" "$" "`" "(" ")" "<" ">" mean something to a shell.
   If a name is ever interpolated into a shell string it stops being a name and
   becomes a command. SILENT AND SERIOUS: an SSID arrives from the air, so
   anybody within range can supply one.

3. ESCAPING. The fix for (1) is to mark the character with a backslash. That
   only works if the NEXT program removes it again. If it forgets, it looks for
   a thing that does not exist. SILENT AND WORST TO FIND: no error at all.

Found for real on 2026-10-13: the owner names his networks ":(" and ":) :) _|_"
deliberately, to trip exactly this. It worked. It found a crash in one module
and, in another, a watchdog that would have failed to reconnect him silently,
at the one moment it existed for.
"""

SEPARATORS = set(':;,|= \t')
SHELL_META = set('|&;$`()<>!*?[]{}~\n')
QUOTES = set('"\'')


def classify(name):
    """-> what this name will trip, and how bad. Never raises."""
    # Every return from this function MUST have the same keys. An early return
    # that omits one makes callers write r.get("advice") defensively, or crash,
    # which is the same class of carelessness this module is about.
    if name is None:
        return {"name": None, "problems": ["is None"], "safe": False,
                "worst": "separator", "advice": advice("separator")}
    if name == "":
        return {"name": "", "problems": ["empty: tools read it as missing, not as a name"],
                "safe": False, "worst": "separator", "advice": advice("separator")}

    problems, worst = [], None
    sep = sorted(SEPARATORS & set(name))
    if sep:
        problems.append("contains %s, which many tools use to separate fields, so the name "
                        "gets split and later values shift along"
                        % ", ".join(repr(c) for c in sep))
        worst = "separator"
    meta = sorted(SHELL_META & set(name))
    if meta:
        problems.append("contains %s, which a shell treats as an instruction. If this name is "
                        "ever put into a shell string it stops being a name and becomes a "
                        "command. Pass it as its own argv element, never spliced into text."
                        % ", ".join(repr(c) for c in meta))
        worst = "instruction"
    if "\\" in name:
        problems.append("contains a backslash, which is also the character used to MARK the "
                        "awkward ones. A tool that escapes without unescaping ends up looking "
                        "for a name that does not exist, and reports nothing at all")
        worst = worst or "escaping"
    q = sorted(QUOTES & set(name))
    if q:
        problems.append("contains %s, which ends a quoted string early"
                        % ", ".join(repr(c) for c in q))
        worst = worst or "separator"
    if name.startswith("-"):
        problems.append("starts with '-', so a command line reads it as an option, not a value. "
                        "Use '--' before it, or an explicit flag")
        worst = worst or "instruction"
    if name != name.strip():
        problems.append("has leading or trailing whitespace, which is invisible and survives "
                        "copy and paste")
        worst = worst or "separator"
    if any(ord(c) < 32 for c in name):
        problems.append("contains a control character, which can rewrite a terminal line and "
                        "hide what is really there")
        worst = "instruction"

    return {"name": name, "problems": problems, "safe": not problems,
            "worst": worst, "advice": advice(worst) if problems else ""}


def advice(worst):
    return {
        "separator": "parse from the RIGHT, or ask the tool for one field at a time",
        "instruction": "NEVER build a shell string. Pass the name as its own argv element",
        "escaping": "undo the tool's escaping before using the value (see unescape)",
    }.get(worst, "")


def unescape(value, escaped=":\\"):
    """Undo backslash escaping, as `nmcli -t` and friends apply it.

    nmcli escapes ":" and "\\" with a backslash. Handing the escaped form
    straight back to nmcli asks for something that does not exist, which is the
    silent failure in problem 3 above.
    """
    if not value:
        return value
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def split_from_right(line, fields, sep=":"):
    """Split a record whose FIRST field may contain the separator.

    The common shape: `nmcli -t -f SSID,CHAN,FREQ,SIGNAL` gives a name that can
    contain colons followed by N fields that cannot. Splitting left to right
    shifts everything; splitting from the right does not.

    -> (name, [the N fixed fields]) or (None, []) when the line is too short.
    """
    # Split only on separators that are NOT escaped. rsplit with a count works
    # when the caller gets the count exactly right, and silently eats the name
    # when they do not: passing 5 for a 4-field record returned name='\\' in
    # testing. Looking for unescaped separators is correct whatever the count.
    import re as _re
    pieces = _re.split(r"(?<!\\)" + _re.escape(sep), line)
    if len(pieces) < fields + 1:
        return None, []
    # anything beyond the fixed tail belongs to the name, rejoined as it was
    name = sep.join(pieces[:len(pieces) - fields])
    return name, pieces[len(pieces) - fields:]


def safe_for_shell(name):
    """True only if this name could survive being put in a shell string.

    The right answer is never to put it in one. This exists so a tool can REFUSE
    rather than hope.
    """
    return bool(name) and not (SHELL_META & set(name)) and not (QUOTES & set(name))
