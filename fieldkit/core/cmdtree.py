"""The command tree of the live argparse parsers, and `fieldkit cmd check`: no command reaches a person, a file or a
shell unless it exists (plan P9, owner 2026-11-12).

Incident: a sudoers line for `python -m fieldkit leakgate *` was handed to the owner; the verb does not exist (the real
one is `fieldkit build-harness leakgate TASK`), the owner ran it, the job stopped. The source of truth is the LIVE
argparse tree, never a hand list:

  generate()        walk cli.build_parser() and every sub-main's own parser -> a JSON tree (fieldkit/core/cmdtree.json)
  tree()            the committed JSON (loaded in a few ms; no argparse, no imports of the verbs)
  find(text)        every fieldkit invocation in a text: `fieldkit X`, `python -m fieldkit X`, `.venv/bin/python -m
                    fieldkit X`, `fieldkit.exe X`, inside sudoers / bash -c / PowerShell / heredoc / markdown lines
  check(text)       -> findings, each unknown verb, sub-verb or flag with the closest real command
  python -m fieldkit.core.cmdtree write   regenerate the JSON (the test `test_cmdtree` fails when it is stale)
  fieldkit cmd check TEXT | --file F        exit 3 on any unknown

Node shape: {"v": {verb: node}, "f": {flag: takes_value 0|1}, "c": [choices of the first positional], "open": true
when the parser takes a free-form rest (REMAINDER) or has no argparse parser we can read: nothing under it is judged}.
"""
import contextlib
import difflib
import io
import json
import os
import re
import sys
from pathlib import Path

TREE_FILE = Path(__file__).with_name("cmdtree.json")
_CACHE = {}


# -- generation (needs argparse and imports every verb's module; run by the test and by --write only) -----------------

class _Captured(Exception):
    def __init__(self, parser):
        super().__init__("captured")
        self.parser = parser


def _walk(p):
    import argparse
    node = {"v": {}, "f": {}, "c": [], "open": False}
    first = True
    for a in p._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                node["v"][name] = _walk(sp)
            first = False
            continue
        if a.option_strings:
            takes = 0 if a.nargs == 0 else 1
            for o in a.option_strings:
                node["f"][o] = takes
            continue
        if a.nargs in (argparse.REMAINDER, argparse.PARSER):
            node["open"] = True
        elif first and a.choices:
            node["c"] = sorted(str(c) for c in a.choices)
        first = False
    return node


def _capture(argv):
    """The first parser a sub-main builds and parses with `argv`, or None. Nothing is run past the parse."""
    import argparse
    from .. import cli
    from . import crossos
    real = {n: getattr(argparse.ArgumentParser, n) for n in ("parse_args", "parse_known_args")}

    def grab(self, *a, **k):
        raise _Captured(self)

    argparse.ArgumentParser.parse_args = grab
    argparse.ArgumentParser.parse_known_args = grab
    old_handle = crossos.handle
    crossos.handle = lambda args, main, say=None, err=None: None
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                cli.main(list(argv))
            except _Captured as c:
                return c.parser
            except BaseException:  # noqa: BLE001  SystemExit from a manual --help, any error: no parser to read
                return None
    finally:
        argparse.ArgumentParser.parse_args = real["parse_args"]
        argparse.ArgumentParser.parse_known_args = real["parse_known_args"]
        crossos.handle = old_handle
    return None


def _merge(into, other):
    into["f"].update(other["f"])
    for k, v in other["v"].items():
        into["v"][k] = v
    if other["c"] and not into["c"]:
        into["c"] = other["c"]
    into["open"] = other["open"] and not other["v"]


def generate():
    """-> the tree: every top-level verb of cli.build_parser(), deepened by the parser its own main builds."""
    from .. import cli
    top = cli.build_parser()
    sub = next(a for a in top._actions if a.dest == "cmd")
    root = {"v": {}, "f": {"--json": 0, "--version": 0, "-h": 0, "--help": 0}, "c": [], "open": False}
    for name, sp in sorted(sub.choices.items()):
        node = _walk(sp)
        node["f"].update({"-h": 0, "--help": 0})
        cap = _capture([name, "--help"])
        if cap is not None and cap.prog != top.prog:
            deep = _walk(cap)
            _merge(node, deep) if (deep["v"] or deep["f"] or deep["c"] or not deep["open"]) else None
            node["open"] = deep["open"] and not deep["v"] and not deep["c"]
        elif node["open"]:
            node["open"] = True
        root["v"][name] = node
    return root


def render(t):
    return json.dumps(t, indent=1, sort_keys=True, ensure_ascii=True) + "\n"


def write():
    TREE_FILE.write_text(render(generate()), encoding="utf-8", newline="\n")
    return TREE_FILE


# -- the check (JSON only: no argparse, no verb imports) -----------------------------------------------------------------

def tree(path=None):
    p = Path(path) if path else TREE_FILE
    key = str(p)
    if key not in _CACHE:
        _CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
    return _CACHE[key]


#  fieldkit as a program: optionally a path (either slash) and .exe, or `python -m fieldkit`
_PREFIX = r"(?:-m\s+fieldkit|(?:[\w.~$%{}:\\/-]*[\\/])?fieldkit(?:\.exe)?)"
_INV = re.compile(r"(?<![\w.\\/-])(?P<prog>" + _PREFIX + r")(?P<q>['\"]?)(?=\s|$|['\"`)])(?P<rest>[^\n]*)")
#  a bare `fieldkit` (no path, no python -m) is a command only at a command position: line start, `$ `, sudo, an
#  operator, `bash -c '`, a backtick or a NOPASSWD: list; never inside prose ("the fieldkit tools")
_BARE_OK = re.compile(r"(?:^|[;&|(`'\"]|\$\(|\bsudo(?:\s+-\S+)*|\bNOPASSWD:|\bNEXT:|\bdo|\bthen|\belse|[$#>]|&&|\|\|)\s*$",
                      re.I)
_BARE_STRICT = re.compile(r"(?:[;&|(`'\"]|\$\(|\bsudo(?:\s+-\S+)*|\bNOPASSWD:|\bNEXT:|\bdo|\bthen|\belse|[$#>]|&&|\|\|)\s*$",
                          re.I)
_STOP = re.compile(r"\s*(?:;|&&|\|\||\||&(?!>)|`|\)|\s[12]?>|\s<)")
_PLACEHOLDER = re.compile(r"^(?:[A-Z][A-Z0-9_./-]*|<.*>|\[.*\]|\*|\.\.\.|\$.*|%.*|\{.*\}|.*[*?].*|\d.*|[./~\\].*)$")


_WORD = re.compile(r"\"[^\"]*\"|'[^']*'|\S+")


def _split(rest):
    """-> (tokens, quoted): the words up to the first unquoted ; && || | & ` ) or redirect; `quoted` are the bodies of
    quoted words (a command inside `inbox add T W "fieldkit doctor"` is judged on its own)."""
    toks, quoted = [], []
    for m in _WORD.finditer(rest):
        w = m.group(0)
        if w[0] in "\"'" and len(w) > 1 and w[-1] == w[0]:
            quoted.append(w[1:-1])
            toks.append(w)
            continue
        cut = re.search(r";|&&|\|\||\||&(?!>)|`|\)|^[12]?[<>]", w)
        if cut:
            if cut.start():
                toks.append(w[:cut.start()])
            break
        toks.append(w)
    out = []
    for t in toks:
        t = t.strip("'\"")
        if t:
            out.append(t)
    return out, quoted


def find(text, strict=False):
    """-> [{"line": n, "prog": str, "argv": [tokens after fieldkit]}] for every invocation in `text`."""
    found = []
    for n, line in enumerate((text or "").splitlines(), 1):
        for m in _INV.finditer(line):
            prog = m.group("prog")
            bare = prog in ("fieldkit", "fieldkit.exe")
            if bare and (strict == "module" or not (_BARE_STRICT if strict else _BARE_OK).search(line[:m.start()])):
                continue
            argv, quoted = _split("" if m.group("q") and prog.startswith("-m") else m.group("rest"))
            # a quoted body that is itself a command: its words are not this command's arguments
            argv = [t for t in argv if not any(t == q for q in quoted if _INV.search(q))]
            found.append({"line": n, "prog": prog, "argv": argv})
            for q in quoted:
                for sub in find(q, strict):
                    found.append(dict(sub, line=n))
    return found


def _paths(node, prefix=()):
    for k, v in sorted(node["v"].items()):
        yield prefix + (k,)
        yield from _paths(v, prefix + (k,))
    for c in node.get("c", []):
        if c not in node["v"]:
            yield prefix + (c,)


def suggest(path, t=None):
    """The closest real commands for an unknown `path` (a list of words): the same last word under another verb first,
    then the nearest names. -> ["fieldkit build-harness leakgate", ...]"""
    t = t or tree()
    allp = list(_paths(t))
    word = path[-1]
    out = [" ".join(p) for p in allp if p[-1] == word and list(p) != list(path)]
    names = sorted({p[-1] for p in allp})
    for c in difflib.get_close_matches(word, names, n=3, cutoff=0.6):
        out += [" ".join(p) for p in allp if p[-1] == c and len(p) == len(path)][:2]
    seen, res = set(), []
    for s in out:
        if s not in seen and s != " ".join(path):
            seen.add(s)
            res.append("fieldkit " + s)
    return res[:4]


def _flag_known(node, flag):
    name = flag.split("=", 1)[0]
    if name in node["f"]:
        return True
    if name.startswith("--") and len(name) > 2:               # argparse accepts a unique prefix
        return any(f.startswith(name) for f in node["f"])
    return False


def check_argv(argv, t=None, flags=True):
    """-> None when `argv` (the words after `fieldkit`) names real commands, else a finding dict.
    Exactness: the top-level verbs are the complete list (cli.build_parser). Below them the tree is what each verb's
    own parser says, and several verbs also route a few sub-verbs by hand before argparse (`peers link`, `skills
    package`), so a word that is not in the tree is a finding only when a real command is close to it (a typo) or the
    same word is a real command elsewhere. Flags are judged only on a leaf parser (no sub-verbs, no choices)."""
    t = t or tree()
    node, path = t, []
    argv = [x.rstrip(",.:;)\\") if not x.startswith("-") else x.rstrip(",.:;)\\") for x in argv]
    for i, w in enumerate(argv):
        if w == "--":
            return None
        if not w:
            continue
        if node.get("open"):
            return None
        if w.startswith("-"):
            if flags and not node["v"] and not node["c"] and path and not _flag_known(node, w):
                return {"kind": "flag", "at": "fieldkit " + " ".join(path), "word": w,
                        "known": sorted(node["f"])[:12], "suggest": [], "path": path}
            continue
        if i and argv[i - 1].startswith("-") and node["f"].get(argv[i - 1].split("=")[0], 0) and "=" not in argv[i - 1]:
            continue                                                  # the value of the flag before it
        top = not path
        if not re.match(r"^[a-z][a-z0-9-]*$", w) and (top or not _PLACEHOLDER.match(w)):
            return None                                               # not a word of a command (prose, a version)
        if node["v"] and w in node["v"]:
            node = node["v"][w]
            path.append(w)
            continue
        if _PLACEHOLDER.match(w) and (path or re.match(r"^(?:[A-Z]|<.*>|\[.*\]|\*|\.\.\.|\$.*)$", w)):
            return None
        if node["v"] or (node["c"] and w not in node["c"]):
            pool = sorted(node["v"]) or node["c"]
            near = difflib.get_close_matches(w, pool, n=2, cutoff=0.75)
            if top or near:
                return {"kind": "verb" if node["v"] else "choice", "at": "fieldkit " + " ".join(path), "word": w,
                        "known": pool[:12], "suggest": suggest(path + [w], t) if node["v"] or top else
                        ["fieldkit " + " ".join(path + [c]) for c in near], "path": path + [w]}
            return None                                               # probably a hand-routed sub-verb: not judged
        return None                                                   # the first positional of a leaf: free text
    return None


#  a line that says the command does NOT exist is quoting a wrong command on purpose (an incident, a test case)
_NEGATED = re.compile(r"does not exist|doesn't exist|\bnot a (?:real )?command|nonexistent|no such (?:verb|command)|"
                      r"unknown (?:verb|command)|\bwrong:|not registered|documented but|documents `|docstring", re.I)


def code_of(path, text):
    """-> (text, strict): the part of a file that can hold a command. Markdown: fenced blocks and `inline spans`
    (the prose around them says "fieldkit is a set of tools"); anything else: all of it, but a bare `fieldkit` must
    follow a quote, `$`, `NEXT:` or an operator (a docstring line starting "fieldkit does ..." is prose)."""
    if str(path).lower().endswith(".py"):
        return text, "module"                      # source: only `-m fieldkit X` (a generated unit or task line)
    if str(path).lower().endswith((".md", ".markdown")):
        out, fence = [], False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
                out.append("")
            elif fence:
                out.append(line)
            else:
                out.append(" ; ".join(re.findall(r"`([^`\n]+)`", line)))
        return "\n".join(out), False
    return text, True


def check(text, t=None, flags=True, skip_negated=False, strict=False, context=None):
    """-> the findings for every fieldkit command in `text` ([] = every one exists)."""
    t = t or tree()
    out = []
    lines = (context if context is not None else text or "").splitlines()
    for inv in find(text, strict):
        if skip_negated and _NEGATED.search(lines[inv["line"] - 1]):
            continue
        f = check_argv(inv["argv"], t, flags)
        if f:
            f.update(line=inv["line"], command=("fieldkit " + " ".join(inv["argv"])).strip())
            out.append(f)
    return out


def scan_file(path, flags=False, t=None):
    """-> the findings for one file (code_of decides which part is judged); lines that say a command does not exist
    are skipped."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    body, strict = code_of(path, text)
    return check(body, t, flags=flags, skip_negated=True, strict=strict, context=text)


def describe(f):
    s = f"line {f['line']}: `{f['command']}`: unknown {f['kind']} `{f['word']}` after `{f['at']}`"
    if f.get("suggest"):
        s += "; did you mean: " + " | ".join(f["suggest"])
    elif f.get("known"):
        s += "; known here: " + ", ".join(f["known"][:8])
    return s


_QUOTED = re.compile(r"(?<!-c )(?<!-lc )(?<!-Command )(?<!-command )(\"(?:\\.|[^\"\\])*\"|'[^']*')", re.S)


def _unquoted(command):
    """A shell command with its quoted strings blanked, except what follows `bash -c` / `-Command` (that is a command):
    a commit message or an echo that talks about `fieldkit nosuch` is text, not a call."""
    return _QUOTED.sub('""', command)


def verb_refusal(command):
    """The tool router's check (a Bash or PowerShell command): a unique message for the first unknown VERB, else ''.
    Verbs only (never flags), fail open on any error: the router teaches, the hard lines are the safety."""
    try:
        if "fieldkit" not in (command or "").lower():
            return ""
        for f in check(_unquoted(command), flags=False):
            if f["kind"] == "verb":
                return describe(f)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def tree_stale():
    """-> True when the committed JSON differs from the live parsers (the CI check)."""
    return TREE_FILE.read_text(encoding="utf-8") != render(generate())


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit cmd", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["check", "tree", "write", "stale"], help="check TEXT | --file F; tree: print "
                    "the tree; write: regenerate cmdtree.json; stale: exit 3 when cmdtree.json is out of date")
    ap.add_argument("text", nargs="*", help="the text (a command line, a sudoers line, a document)")
    ap.add_argument("--file", "-f", help="read the text from a file ('-' for stdin)")
    ap.add_argument("--no-flags", action="store_true", help="judge verbs only")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    say = (lambda o: print(json.dumps(o, indent=1))) if a.json else None
    if a.action == "write":
        p = write()
        print(f"wrote {p}\nNEXT: commit it")
        return 0
    if a.action == "stale":
        s = tree_stale()
        print("STALE: fieldkit/core/cmdtree.json differs from the live parsers\nNEXT: python -m fieldkit.core.cmdtree "
              "write" if s else "FRESH: cmdtree.json matches the live parsers\nNEXT: none")
        return 3 if s else 0
    if a.action == "tree":
        print(render(tree()), end="")
        return 0
    if a.file:
        text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8", errors="replace")
    else:
        text = " ".join(a.text)
    inv = find(text)
    fs = check(text, flags=not a.no_flags)
    if say:
        say({"ok": not fs, "invocations": len(inv), "findings": fs})
    else:
        for f in fs:
            print("UNKNOWN " + describe(f))
        print(f"{'FAIL' if fs else 'PASS'}: {len(inv)} fieldkit command(s) found, {len(fs)} unknown"
              + ("" if inv else " (none found: nothing was judged)"))
        print("NEXT: " + ("use the real command (see did you mean), then check again" if fs else "none"))
    return 3 if fs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["tree"]))
