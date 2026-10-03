"""Excision creep at compile time: a file 157 added or changed includes a header from a component the fork removed.

Live run 16, stop 7 (2026-10-02 05:17): ipc/glue/UtilityProcessImpl.cpp gained
`#  include "mozilla/llama/LlamaRuntimeLinker.h"` and `mozilla::llama::LlamaRuntimeLinker::Init();` for the
HW_INFERENCE sandbox; backends/llama is excised, so clang-cl stopped with `fatal error: ... file not found`.

The deterministic part: recognise the stop (the missing header's directory is one the fork deletes or excises),
remove the include with a GORILLA comment, and remove every use of the header's namespace/class ONLY when each use is
the whole body of an `if (...) { ...; }` block or a lone statement - the shapes seen so far. Anything else (a use
inside an expression, a declaration, a member) is left for a person with the exact lines listed; the loop stops with
them in the journal. Nothing is guessed, nothing is left half-cut: the file must still parse by brace balance.
"""
import re
from pathlib import Path

NOT_FOUND = re.compile(r"(?P<file>\S+?\.(?:cpp|h|mm|c))\((?P<line>\d+),\d+\): fatal error: '(?P<hdr>[^']+)' file not found")
INCLUDE = re.compile(r'^\s*#\s*include\s+"(?P<hdr>[^"]+)"')


def missing_headers(lines):
    """-> [(source file rel path, header)] from a mach log."""
    text = "\n".join(lines)
    out = []
    for m in NOT_FOUND.finditer(text):
        f = m.group("file").replace("\\", "/")
        out.append((f, m.group("hdr")))
    return out


def rel_to_tree(path, workdir):
    p = path.replace("\\", "/")
    w = str(workdir).replace("\\", "/").rstrip("/") + "/"
    return p[len(w):] if p.lower().startswith(w.lower()) else p


def inside_tree(path, workdir):
    """The file a compiler line names, resolved (symlinks and `..` followed), or None when it lies outside the
    working copy. A log line is not trusted to name a file this tool may write: only files under the task's
    workdir are ever edited."""
    w = Path(workdir).resolve()
    p = Path(str(path).replace("\\", "/"))
    p = (p if p.is_absolute() else w / p).resolve()
    try:
        p.relative_to(w)
    except ValueError:
        return None
    return p


def excised_header(hdr, excised_dirs):
    """Is this header's first path component one the fork removes (e.g. mozilla/llama/ <- backends/llama)?"""
    parts = hdr.replace("\\", "/").split("/")
    stems = {d.rstrip("/").split("/")[-1] for d in excised_dirs}
    return any(p in stems for p in parts[:-1])


def namespace_of(hdr):
    """mozilla/llama/LlamaRuntimeLinker.h -> ("mozilla::llama::", "LlamaRuntimeLinker")."""
    parts = hdr[:-2].split("/") if hdr.endswith(".h") else hdr.split("/")
    return "::".join(parts[:-1]) + "::", parts[-1]


def excise(lines, hdr, nl_comment="//"):
    """-> (new_lines, removed, leftover). Removes the include and the self-contained uses; `leftover` lists uses it
    would not touch (line numbers, 1-based) - when non-empty the caller must not write."""
    ns, cls = namespace_of(hdr)
    new, removed, leftover = list(lines), [], []
    uses = [i for i, l in enumerate(lines) if (ns in l or cls + "::" in l) and not INCLUDE.match(l)]
    # self-contained use: a lone statement `X::Y(...);` optionally the only body line of `if (...) {` ... `}`
    for i in sorted(uses, reverse=True):
        stmt = lines[i].strip()
        if not re.match(r"^[\w:]+\([^;]*\);$", stmt):
            leftover.append(i + 1)
            continue
        prev, nxt = (lines[i - 1].strip() if i else ""), (lines[i + 1].strip() if i + 1 < len(lines) else "")
        if re.match(r"^(if|else if)\s*\(.*\)\s*\{$", prev) and nxt == "}":
            indent = lines[i - 1][:len(lines[i - 1]) - len(lines[i - 1].lstrip())]
            new[i - 1:i + 2] = [f"{indent}{nl_comment} GORILLA excised: {stmt} ({hdr} removed)"]
            removed.append((i, stmt))
        else:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            new[i] = f"{indent}{nl_comment} GORILLA excised: {stmt} ({hdr} removed)"
            removed.append((i + 1, stmt))
    for i, l in enumerate(new):
        m = INCLUDE.match(l)
        if m and m.group("hdr") == hdr:
            indent = l[:len(l) - len(l.lstrip())]
            new[i] = f"{indent}{nl_comment} GORILLA excised: #include \"{hdr}\" (component removed)"
            removed.append((i + 1, l.strip()))
    return new, sorted(removed), sorted(leftover)


def balanced(lines):
    text = "\n".join(lines)
    return text.count("{") == text.count("}") and text.count("(") == text.count(")")
