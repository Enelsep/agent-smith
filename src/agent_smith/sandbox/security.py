"""Sandbox security primitives.

Everything that restricts what LLM-generated code may do lives here, so the
worker stays readable and the guards can be unit-tested in isolation.

"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Sequence
from types import ModuleType


class ImportBlocked(ImportError):
    """Raised when sandboxed code imports something off the allowlist."""


class ImportGuard:
    """Callable replacement for __import__, checked against an allowlist.

    Entry forms:
        "math"    -> exactly the `math` module
        "math.*"  -> any descendant of `math` (math.foo, math.foo.bar)
    """

    def __init__(self, authorized_imports: Iterable[str]):
        self.exact: set[str] = set()
        self.prefixes: set[str] = set()
        for entry in authorized_imports:
            entry = entry.strip()
            if entry.endswith(".*"):
                self.prefixes.add(entry[:-2])
            elif entry == "*":
                self.prefixes.add("")
            else:
                self.exact.add(entry)

    def is_allowed(self, module_name: str) -> bool:
        if module_name in self.exact:
            return True
        for prefix in self.prefixes:
            if prefix == "" or module_name.startswith(prefix + "."):
                return True
        return False

    def _reject(self, name: str) -> None:
        allowed = ", ".join(sorted(self.exact)) or "(none)"
        raise ImportBlocked(
            f"import of '{name}' is blocked by the sandbox. "
            f"Authorized modules: {allowed}"
        )

    def __call__(
        self,
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        if level != 0:
            raise ImportBlocked(
                "relative imports are not available in the sandbox; "
                "use an absolute module name"
            )

        if not self.is_allowed(name):
            self._reject(name)

        module = __import__(name, globals, locals, fromlist, level)
        for item in fromlist or ():
            attr = getattr(module, item, None)
            if isinstance(attr, ModuleType):
                full = getattr(attr, "__name__", f"{name}.{item}")
                if not self.is_allowed(full):
                    self._reject(full)

        return module


BLOCKED_ATTRIBUTES = frozenset(
    {
        "__subclasses__",
        "__bases__",
        "__base__",
        "__mro__",
        "__globals__",
        "__code__",
        "__closure__",
        "__func__",
        "__builtins__",
        "__import__",
        "__loader__",
        "__spec__",
        "__getattribute__",
        "__reduce__",
        "__reduce_ex__",
        "__init_subclass__",
        "__subclasshook__",
    }
)

BLOCKED_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "vars",
        "breakpoint",
        "memoryview",
    }
)


def scan_for_escapes(code: str) -> str | None:
    """Return a human-readable reason if `code` contains a known escape shape.

    Runs before exec, so the code never gets a chance to execute. This is a
    blunt instrument -- it matches on names, so it can produce false positives
    on unusual but legitimate code. That trade is acceptable: the message tells
    the model exactly what to avoid, and it can rewrite.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRIBUTES:
            return (
                f"access to '{node.attr}' is blocked by the sandbox "
                f"(introspection attributes can be used to escape the restricted "
                f"environment)"
            )

        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            arg = node.args[1]
            if isinstance(arg, ast.Constant) and arg.value in BLOCKED_ATTRIBUTES:
                return f"getattr access to '{arg.value}' is blocked by the sandbox"

        if (
            isinstance(node, ast.Name)
            and node.id in BLOCKED_NAMES
            and isinstance(node.ctx, ast.Load)
        ):
            return f"use of '{node.id}' is blocked by the sandbox"
    return None


_WORKER_ESSENTIALS = frozenset(
    {
        "builtins",
        "sys",
        "io",
        "signal",
        "time",
        "traceback",
        "contextlib",
        "types",
        "ast",
        "enum",
        "dataclasses",
        "multiprocessing",
        "pickle",
        "abc",
        "os",
        "posixpath",
        "collections",
        "functools",
        "operator",
        "agent_smith",
    }
)


def purge_sys_modules(guard: ImportGuard, keep: Sequence[str] = ()) -> list[str]:
    """Drop non-allowlisted modules from the import cache.

    Defence in depth: if code ever reaches `sys.modules` by some other route,
    it should not find `subprocess` sitting there pre-loaded. This does not
    unload anything -- it only clears the cache, so the worker's own live
    references keep working.
    """
    protected = _WORKER_ESSENTIALS | set(keep)
    purged = []
    for name in list(sys.modules):
        root = name.split(".")[0]
        if root in protected or name in protected:
            continue
        if guard.is_allowed(name):
            continue
        del sys.modules[name]
        purged.append(name)
    return purged


class AttributeBlocked(AttributeError):
    """Raised when sandboxed code reaches for an introspection attribute."""


def _guarded_getattr(obj: object, name: str, *default: object) -> object:
    """Runtime companion to `scan_for_escapes`.

    The static scan is name-based, so it cannot see through a computed string:
        m = '__subcl' + 'asses__'
        getattr(cls, m)
    This check runs at call time, where the name is fully resolved, and closes
    that gap.
    """
    if isinstance(name, str) and name in BLOCKED_ATTRIBUTES:
        raise AttributeBlocked(f"access to '{name}' is blocked by the sandbox")
    return getattr(obj, name, *default)


def build_sandbox_builtins(guard: ImportGuard) -> dict:
    """Builtins dict handed to the sandbox namespace.

    SBX-4 narrows this to an allowlist and strips the remaining dangerous
    callables. For now: guarded __import__, guarded getattr, and the obvious
    offenders removed.
    """
    import builtins as _builtins

    ns = dict(_builtins.__dict__)
    ns["__import__"] = guard
    ns["getattr"] = _guarded_getattr
    for name in ("eval", "exec", "compile", "breakpoint", "input", "help"):
        ns.pop(name, None)
    return ns
