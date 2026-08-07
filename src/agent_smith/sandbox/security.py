"""What the sandbox refuses, and how it says so.
Every refusal is one exception, `SandboxBlocked`. The worker turns it into
`Outcome.BLOCKED` and hands the message to the model, and it never needs to
know which of the guards objected.
"""

from __future__ import annotations

import ast
import builtins as _builtins
import contextlib
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


class SandboxBlocked(Exception):
    """The sandbox refused an import, an attribute, a path, or a syscall."""


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
"""Attributes that walk from any object back to the interpreter."""

SAFE_BUILTINS = frozenset(
    {
        "abs", "aiter", "anext", "all", "any", "ascii", "bin", "bool",
        "bytearray", "bytes", "callable", "chr", "classmethod", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format",
        "frozenset", "hasattr", "hash", "hex", "int", "isinstance",
        "issubclass", "iter", "len", "list", "map", "max", "min", "next",
        "object", "oct", "open", "ord", "pow", "print", "property", "range",
        "repr", "reversed", "round", "set", "setattr", "slice", "sorted",
        "staticmethod", "str", "sum", "super", "tuple", "type", "zip",
        "True", "False", "None", "NotImplemented", "Ellipsis",
        "__build_class__", "__name__",
    }
)  # fmt: skip
"""The allowlist. `eval`, `exec`, `compile`, `globals`, `vars`, `breakpoint`
and `input` are absent, so using one is already a NameError -- no scan needed.
`open` is present because the audit hook decides where it may point."""


_NETWORK_EVENTS = frozenset(
    {
        "socket.__new__", "socket.socket", "socket.connect", "socket.bind",
        "socket.sendto", "socket.getaddrinfo", "socket.gethostbyname",
        "socket.gethostbyaddr", "urllib.Request", "http.client.connect",
        "http.client.send", "ftplib.connect", "smtplib.connect",
        "smtplib.send",
    }
)  # fmt: skip

_PROCESS_EVENTS = frozenset(
    {
        "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
        "os.forkpty", "os.kill", "subprocess.Popen", "pty.spawn",
        "ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function",
    }
)  # fmt: skip

_PATH_EVENTS: dict[str, tuple[tuple[int, ...], bool]] = {
    "os.listdir": ((0,), False),
    "os.scandir": ((0,), False),
    "os.chdir": ((0,), False),
    "os.mkdir": ((0,), True),
    "os.rmdir": ((0,), True),
    "os.remove": ((0,), True),
    "os.unlink": ((0,), True),
    "os.rename": ((0, 1), True),
    "os.replace": ((0, 1), True),
    "os.chmod": ((0,), True),
    "os.truncate": ((0,), True),
    "os.symlink": ((0, 1), True),
    "os.link": ((0, 1), True),
    "os.utime": ((0,), True),
    "shutil.copyfile": ((0, 1), True),
    "shutil.copytree": ((0, 1), True),
    "shutil.rmtree": ((0,), True),
    "shutil.move": ((0, 1), True),
}

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_WRITE_MODES = frozenset("wax+")

SCRATCH_DIR = "/tmp/agent"
"""The writable working area, created on demand when it is configured."""

_INSTALLED: SandboxPolicy | None = None
"""The policy the process-wide audit hook consults.

A hook can never be removed once added, so it is added once and reads this
instead. Sandboxed code cannot reach it: `agent_smith` is not importable from
inside the sandbox.
"""


def _interpreter_roots() -> tuple[str, ...]:
    """Directories the import machinery must be able to read.

    Importing a pure-Python stdlib module opens its source and its .pyc.
    Denying that would break `import json` inside the sandbox, so the
    interpreter's own tree is readable while staying non-writable.
    """
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    roots.update(entry for entry in sys.path if entry)
    return tuple(sorted(os.path.realpath(root) for root in roots if root))


class SandboxPolicy:
    """The complete set of restrictions applied to one worker."""

    def __init__(
        self,
        authorized_imports: Iterable[str] = (),
        allowed_directories: Iterable[str] = (),
    ) -> None:
        self.exact: set[str] = set()
        self.prefixes: set[str] = set()
        for raw in authorized_imports:
            entry = raw.strip()
            if entry == "*":
                self.prefixes.add("")
            elif entry.endswith(".*"):
                self.prefixes.add(entry[:-2])
            else:
                self.exact.add(entry)

        directories = list(allowed_directories)
        if SCRATCH_DIR in directories:
            with contextlib.suppress(OSError):
                Path(SCRATCH_DIR).mkdir(parents=True, exist_ok=True)

        self.writable = tuple(
            dict.fromkeys(os.path.realpath(entry) for entry in directories)
        )
        self.readable = self.writable + _interpreter_roots()
        self._active = False

    def allows_import(self, module: str) -> bool:
        return module in self.exact or any(
            prefix == "" or module.startswith(prefix + ".") for prefix in self.prefixes
        )

    def _import(
        self,
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> ModuleType:
        """The sandbox's `__import__`."""
        if level != 0:
            raise SandboxBlocked(
                "relative imports are not available in the sandbox; "
                "use an absolute module name"
            )
        if not self.allows_import(name):
            self._refuse_import(name)

        module = __import__(name, globals, locals, fromlist, level)
        for item in fromlist or ():
            attribute = getattr(module, item, None)
            if isinstance(attribute, ModuleType):
                full = getattr(attribute, "__name__", f"{name}.{item}")
                if not self.allows_import(full):
                    self._refuse_import(full)
        return module

    def _refuse_import(self, name: str) -> None:
        allowed = ", ".join(sorted(self.exact)) or "(none)"
        raise SandboxBlocked(
            f"import of '{name}' is blocked by the sandbox. "
            f"Authorized modules: {allowed}"
        )

    @staticmethod
    def check_code(code: str) -> str | None:
        """Why `code` may not run, or None. Checked before it is executed.

        Name-based, so it cannot see through a computed string -- that gap is
        closed at call time by the guarded `getattr` below. It runs first
        anyway because refusing before execution means no side effect happened
        at all, and because it can point at the exact attribute.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in BLOCKED_ATTRIBUTES:
                return (
                    f"access to '{node.attr}' is blocked by the sandbox "
                    f"(introspection attributes can be used to escape the "
                    f"restricted environment)"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
            ):
                wanted = node.args[1]
                if (
                    isinstance(wanted, ast.Constant)
                    and wanted.value in BLOCKED_ATTRIBUTES
                ):
                    return (
                        f"getattr access to '{wanted.value}' is blocked by the sandbox"
                    )
        return None

    @staticmethod
    def _getattr(obj: object, name: str, *default: object) -> object:
        """The sandbox's `getattr`, resolving computed names before they land."""
        if isinstance(name, str) and name in BLOCKED_ATTRIBUTES:
            raise SandboxBlocked(f"access to '{name}' is blocked by the sandbox")
        return getattr(obj, name, *default)

    def check_path(self, path: str, *, writing: bool) -> None:
        """Raise unless `path` resolves inside the permitted roots."""
        roots = self.writable if writing else self.readable
        resolved = os.path.realpath(path)
        if any(
            resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)
            for root in roots
        ):
            return
        raise SandboxBlocked(
            f"the sandbox is not permitted to {'write to' if writing else 'read'} "
            f"'{path}'. Accessible directories: {', '.join(self.writable) or '(none)'}"
        )

    def builtins(self) -> dict[str, Any]:
        """The `__builtins__` for the sandbox namespace.

        The allowlist, every exception class so user code can write
        `except ValueError`, and the two callables that need policy rather
        than removal.
        """
        namespace: dict[str, Any] = {
            name: getattr(_builtins, name)
            for name in SAFE_BUILTINS
            if hasattr(_builtins, name)
        }
        for name in dir(_builtins):
            value = getattr(_builtins, name)
            if isinstance(value, type) and issubclass(value, BaseException):
                namespace[name] = value

        namespace["__import__"] = self._import
        namespace["getattr"] = self._getattr
        return namespace

    def install(self) -> None:
        """Make this the policy the audit hook enforces."""
        global _INSTALLED
        _INSTALLED = self
        if not getattr(install_hook, "done", False):
            sys.addaudithook(install_hook)
            install_hook.done = True  # type: ignore[attr-defined]

    @contextlib.contextmanager
    def enforcing(self) -> Iterator[None]:
        """Enforce for the duration of one exec()."""
        self._active = True
        try:
            yield
        finally:
            self._active = False

    @contextlib.contextmanager
    def suspended(self) -> Iterator[None]:
        """Stop enforcing while the worker itself works, not the model's code.

        Used around the MCP tool round trip: that pipe traffic is ours, and
        the hook would otherwise judge it by the rules written for the model.
        """
        was_active, self._active = self._active, False
        try:
            yield
        finally:
            self._active = was_active

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        """Deny network, process and out-of-policy filesystem operations.

        Fires on the operation rather than the name, so it holds however the
        caller got there.
        """
        if not self._active:
            return

        if event in _NETWORK_EVENTS:
            raise SandboxBlocked(
                f"network access is disabled in the sandbox (blocked: {event})"
            )
        if event in _PROCESS_EVENTS:
            raise SandboxBlocked(
                f"starting processes is disabled in the sandbox (blocked: {event})"
            )

        if event == "open" and args:
            target = args[0]
            if isinstance(target, (str, bytes, os.PathLike)):
                self.check_path(os.fsdecode(target), writing=_opens_for_write(args))
            return

        indices, writing = _PATH_EVENTS.get(event, ((), False))
        for index in indices:
            if len(args) > index and isinstance(args[index], (str, bytes, os.PathLike)):
                self.check_path(os.fsdecode(args[index]), writing=writing)


def install_hook(event: str, args: tuple[Any, ...]) -> None:
    """The single process-wide hook, dispatching to whatever is installed."""
    if _INSTALLED is not None:
        _INSTALLED.audit(event, args)


def _opens_for_write(args: tuple[Any, ...]) -> bool:
    """Write intent from the `open` audit event's mode or flags."""
    mode = args[1] if len(args) > 1 else None
    if isinstance(mode, str):
        return bool(_WRITE_MODES & set(mode))
    flags = args[2] if len(args) > 2 else 0
    if isinstance(flags, int):
        return bool(flags & _WRITE_FLAGS)
    return True  # unknown shape: assume the stricter rule
