"""The tool registry: what exists, where, whether it is safe to probe, what is tested.

`check()` never runs a tool. It confirms the file exists, that it parses, and
decides whether it is safe to call with --help, from the code itself:

    safe to probe = has an `if __name__ == "__main__"` guard, imports argparse
                    and calls parse_args (read from the syntax tree, not by
                    searching the text), and has no statement at module level
                    that does work.

Harmless at module level: imports, function and class definitions (their
decorators, defaults and annotations included), docstrings, `pass`, the main
guard, and assignments whose value is a constant expression (literals, names,
attribute chains and operators on those, plus a short list of pure calls such
as re.compile and Path(...) - see _PURE_CALLS). `if`/`try` blocks count when
everything inside them does. Any other call makes the file unsafe, apart from
a few exact console/warning/sys.path set-up calls whose arguments are
themselves constant (see _SETUP_CALLS).

That rule exists because on 2026-09-29 `organize.py --help` was run to see
its options; it has no argument handling and ran its whole job instead. Only
tests listed in tools.yaml are ever executed, and only with run_tests=True.
"""
import ast
import subprocess
import sys
from pathlib import Path

from ..core import settings
from ..core.host import platform_ok

REGISTRY = Path(__file__).resolve().parent / "tools.yaml"
# The owner's own tools (private folders, personal projects) live beside the
# checkout in local/, which git ignores: same format, merged at load time.
LOCAL_REGISTRY = settings.ROOT / "local" / "tools.yaml"


# Module-level calls that only set the process up. Exact dotted names, and only when every
# argument is a constant expression: `x = do_work()` or `atexit.register(job)` stays unsafe.
_SETUP_CALLS = {"sys.stdout.reconfigure", "sys.stderr.reconfigure", "warnings.filterwarnings",
                "warnings.simplefilter", "sys.path.insert", "sys.path.append"}
# Decorators that only wrap or mark a definition (exact names, or name(constant args)).
_SAFE_DECORATORS = {"staticmethod", "classmethod", "property", "dataclass", "dataclasses.dataclass",
                    "functools.lru_cache", "lru_cache", "functools.cache", "cache", "functools.cached_property",
                    "cached_property", "functools.total_ordering", "total_ordering", "contextmanager",
                    "contextlib.contextmanager", "abstractmethod", "abc.abstractmethod", "overload",
                    "typing.overload", "unique", "enum.unique", "functools.singledispatch", "singledispatch"}


# Pure calls: they compute a value from their arguments and touch nothing (no writes, no
# network, no processes), so `RX = re.compile(r"...")` or `HERE = Path(__file__).resolve().parent`
# is still a constant. Exact dotted names only; every argument must itself be constant.
# Empty this set to make every module-level call unsafe.
_PURE_CALLS = {"re.compile", "Path", "pathlib.Path", "PurePath", "PureWindowsPath", "PurePosixPath",
               "Path.home", "Path.cwd", "os.path.join", "os.path.dirname", "os.path.abspath", "os.path.basename",
               "os.path.realpath", "os.path.expanduser", "os.path.normpath", "os.environ.get", "os.getenv",
               "frozenset", "set", "tuple", "dict", "list", "int", "float", "str", "bool", "range",
               "namedtuple", "collections.namedtuple", "TypeVar", "typing.TypeVar", "NewType", "typing.NewType",
               "sorted", "len", "min", "max", "field", "dataclasses.field", "os.cpu_count", "shutil.which",
               "sys.stdout.isatty", "sys.stderr.isatty"}
# Methods that are pure when called on a constant value, e.g. Path(__file__).resolve().parent or
# """a b c""".split(). Never "replace"/"rename"/"unlink" and the like: on a Path they change the disk.
_PURE_METHODS = {"resolve", "absolute", "expanduser", "with_name", "with_suffix", "with_stem", "joinpath",
                 "lower", "upper", "strip", "format", "split", "splitlines", "join", "keys", "values", "items"}


def _pure_call(node):
    if not isinstance(node, ast.Call):
        return False
    if not (all(_const_expr(a) for a in node.args) and all(_const_expr(k.value) for k in node.keywords)):
        return False
    if _dotted(node.func) in _PURE_CALLS:
        return True
    f = node.func                       # a pure method on a pure value: Path(x).resolve()
    return isinstance(f, ast.Attribute) and f.attr in _PURE_METHODS and _const_expr(f.value)


def _dotted(node):
    """Name or attribute chain -> 'a.b.c', else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return ".".join([node.id] + parts[::-1])
    return None


def _const_expr(node):
    """True for a literal / constant expression: literals, names, attribute chains, and operators,
    containers, subscripts and f-strings built from them. Any call, lambda, comprehension,
    await, yield or walrus makes it False."""
    if node is None:
        return True
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Call):
        return _pure_call(node)
    if isinstance(node, ast.Attribute):
        return _const_expr(node.value)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_const_expr(e) for e in node.elts)
    if isinstance(node, ast.Starred):
        return _const_expr(node.value)
    if isinstance(node, ast.Dict):
        return all(_const_expr(k) for k in node.keys if k is not None) and all(_const_expr(v) for v in node.values)
    if isinstance(node, ast.BinOp):
        return _const_expr(node.left) and _const_expr(node.right)
    if isinstance(node, ast.UnaryOp):
        return _const_expr(node.operand)
    if isinstance(node, ast.BoolOp):
        return all(_const_expr(v) for v in node.values)
    if isinstance(node, ast.Compare):
        return _const_expr(node.left) and all(_const_expr(c) for c in node.comparators)
    if isinstance(node, ast.IfExp):
        return _const_expr(node.test) and _const_expr(node.body) and _const_expr(node.orelse)
    if isinstance(node, ast.Subscript):
        return _const_expr(node.value) and _const_expr(node.slice)
    if isinstance(node, ast.Slice):
        return _const_expr(node.lower) and _const_expr(node.upper) and _const_expr(node.step)
    if isinstance(node, ast.JoinedStr):
        return all(_const_expr(v) for v in node.values)
    if isinstance(node, ast.FormattedValue):
        return _const_expr(node.value) and _const_expr(node.format_spec)
    if isinstance(node, ast.Lambda):              # a definition: the body runs only when called
        return all(_const_expr(d) for d in node.args.defaults + [d for d in node.args.kw_defaults if d])
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        elts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        return all(_const_expr(e) for e in elts) and all(
            _const_expr(g.iter) and all(_const_expr(i) for i in g.ifs) and not g.is_async for g in node.generators)
    return False


def _is_main_guard(n):
    """Exactly `if __name__ == "__main__":` (either order)."""
    if not (isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and len(n.test.ops) == 1
            and isinstance(n.test.ops[0], ast.Eq)):
        return False
    sides = (n.test.left, n.test.comparators[0])
    return (any(isinstance(s, ast.Name) and s.id == "__name__" for s in sides)
            and any(isinstance(s, ast.Constant) and s.value == "__main__" for s in sides))


def _safe_decorator(d):
    if isinstance(d, ast.Call):
        return (_dotted(d.func) in _SAFE_DECORATORS and all(_const_expr(a) for a in d.args)
                and all(_const_expr(k.value) for k in d.keywords))
    name = _dotted(d)
    return name is not None and (name in _SAFE_DECORATORS or name.rsplit(".", 1)[-1] in ("setter", "getter", "deleter"))


def _name_targets(t):
    if isinstance(t, ast.Name):
        return True
    if isinstance(t, ast.Subscript):              # TABLE["key"] = ...: fills a table this module owns
        return isinstance(t.value, ast.Name) and _const_expr(t.slice)
    if isinstance(t, (ast.Tuple, ast.List)):
        return all(_name_targets(e) for e in t.elts)
    if isinstance(t, ast.Starred):
        return _name_targets(t.value)
    return False


def _work(n, lazy_ann=False):
    """None if a module-level (or class-body) statement does no work when the file is loaded,
    else a short description of the work."""
    ann = (lambda a: True) if lazy_ann else _const_expr
    if isinstance(n, (ast.Import, ast.ImportFrom, ast.Pass)):
        return None
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = n.args
        if not all(_safe_decorator(d) for d in n.decorator_list):
            return "decorator that runs code"
        if not all(_const_expr(d) for d in a.defaults + [d for d in a.kw_defaults if d is not None]):
            return "default value computed by a call"
        args = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
        if not all(ann(x.annotation) for x in args) or not ann(n.returns):
            return "annotation computed by a call"
        return None
    if isinstance(n, ast.ClassDef):
        if not all(_safe_decorator(d) for d in n.decorator_list):
            return "decorator that runs code"
        if not all(_const_expr(b) for b in n.bases) or not all(_const_expr(k.value) for k in n.keywords):
            return "class base computed by a call"
        for b in n.body:
            w = _work(b, lazy_ann)
            if w:
                return f"class body: {w}"
        return None
    if isinstance(n, ast.Assign):
        if not all(_name_targets(t) for t in n.targets):
            return "assignment into another object"
        return None if _const_expr(n.value) else "assignment from a call or computed value"
    if isinstance(n, ast.AnnAssign):
        if not _name_targets(n.target):
            return "assignment into another object"
        if not ann(n.annotation):
            return "annotation computed by a call"
        return None if _const_expr(n.value) else "assignment from a call or computed value"
    if isinstance(n, ast.AugAssign):
        return None if _name_targets(n.target) and _const_expr(n.value) else "assignment from a call or computed value"
    if isinstance(n, ast.Expr):
        if isinstance(n.value, ast.Constant):             # docstring / bare literal
            return None
        if isinstance(n.value, ast.Call):
            c = n.value
            if (_dotted(c.func) in _SETUP_CALLS and all(_const_expr(x) for x in c.args)
                    and all(_const_expr(k.value) for k in c.keywords)):
                return None
            return "call"
        return "expression"
    if isinstance(n, ast.If):
        if not _const_expr(n.test):
            return "if-test that calls something"
        for b in n.body + n.orelse:
            w = _work(b, lazy_ann)
            if w:
                return w
        return None
    if isinstance(n, ast.Try):
        for h in n.handlers:
            if not _const_expr(h.type):
                return "except clause that calls something"
        for b in n.body + n.orelse + n.finalbody + [b for h in n.handlers for b in h.body]:
            w = _work(b, lazy_ann)
            if w:
                return w
        return None
    return type(n).__name__


def _harmless(n, lazy_ann=False):
    """True if a module-level statement only sets up (imports, definitions, constants)."""
    return _work(n, lazy_ann) is None


def _uses_argparse(tree):
    """From the syntax tree: argparse is imported AND some parse_args-style call exists."""
    imported = any((isinstance(n, ast.Import) and any(a.name == "argparse" for a in n.names))
                   or (isinstance(n, ast.ImportFrom) and n.module == "argparse" and n.level == 0)
                   for n in ast.walk(tree))
    parses = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("parse_args", "parse_known_args", "parse_intermixed_args")
                 for n in ast.walk(tree))
    return imported and parses


def load(strict=False):
    raw = list(settings.read_file(REGISTRY)["tools"])
    if LOCAL_REGISTRY.is_file():
        raw += settings.read_file(LOCAL_REGISTRY).get("tools") or []
    tools = settings.expand(raw, strict=strict)
    ids = [t["id"] for t in tools]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"duplicate tool ids in tools.yaml / local/tools.yaml: {sorted(dup)}")
    return tools


def probe_safety(path):
    """-> (safe: bool, reason: str) from static analysis of a .py file. Nothing is run."""
    try:
        src = Path(path).read_text(encoding="utf-8-sig")   # tolerate a BOM
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as e:
        return False, f"does not parse: {type(e).__name__}"
    lazy_ann = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                   and any(a.name == "annotations" for a in n.names) for n in tree.body)
    guard = any(_is_main_guard(n) for n in tree.body)
    work = []
    for n in tree.body:
        if _is_main_guard(n):
            w = next((x for x in (_work(b, lazy_ann) for b in n.orelse) if x), None)   # else: runs on import
        else:
            w = _work(n, lazy_ann)
        if w:
            work.append(f"line {n.lineno}: {w} at module level")
    if not guard and not work:
        return False, "library module: import it, do not run it"
    if work:
        return False, "RUNS ON LOAD - " + "; ".join(work[:3])
    if not guard:
        return False, "no __main__ guard"
    if not _uses_argparse(tree):
        return False, "no argparse: --help is not understood"
    return True, "main guard + argparse, no module-level work"


def check(run_tests=False, only=None, timeout=300):
    """Report per tool. Runs nothing except the listed tests, and only if asked."""
    out = []
    for t in load():
        if only and t["id"] not in only:
            continue
        row = {"id": t["id"], "harness": t.get("harness"), "platforms": t.get("platforms"),
               "changes": t.get("changes", True), "has_test": bool(t.get("test")), "retired": t.get("retired", False)}
        path = t.get("path")
        if not path:
            row["status"] = "remote" if t.get("repo") else "no-path"
            row["repo"] = t.get("repo")
            out.append(row)
            continue
        p = Path(path)
        row["exists"] = p.exists()
        if not p.exists():
            row["status"] = "missing"
            out.append(row)
            continue
        entry = t.get("entry") or []
        if entry[1:3] == ["-m", "fieldkit"]:
            row["probe_safe"], row["probe_reason"] = True, "reached through the fieldkit CLI (argparse)"
        elif p.suffix == ".py":
            row["probe_safe"], row["probe_reason"] = probe_safety(p)
        row["status"] = "present"
        if run_tests and t.get("test") and platform_ok(t.get("platforms")):
            cmd = [sys.executable if c in ("python", "python3") else c for c in t["test"]]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=p.parent if p.is_file() else p)
            row["test_ok"] = r.returncode == 0
            row["test_tail"] = (r.stdout + r.stderr).strip().splitlines()[-5:]
            from .cards import record_test          # trust level "tested" needs a pass on THIS file
            record_test(t["id"], t.get("path") if p.is_file() else None, row["test_ok"])
        out.append(row)
    return out
