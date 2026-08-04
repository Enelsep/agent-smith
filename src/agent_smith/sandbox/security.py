"""Sandbox security primitives.

Everything that restricts what LLM-generated code may do lives here, so the
worker stays readable and the guards can be unit-tested in isolation.

"""

from __future__ import annotations

import ast
import builtins as _builtins
import contextlib
import os
import resource
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


class ImportBlockedError(ImportError):
    """Raised when sandboxed code imports something off the allowlist."""


class ImportGuard:
    """Callable replacement for __import__, checked against an allowlist.

    Entry forms:
        "math"    -> exactly the `math` module
        "math.*"  -> any descendant of `math` (math.foo, math.foo.bar)
    """

    def __init__(self, authorized_imports: Iterable[str]) -> None:
        self.exact: set[str] = set()
        self.prefixes: set[str] = set()
        for raw in authorized_imports:
            entry = raw.strip()
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
        raise ImportBlockedError(
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
            raise ImportBlockedError(
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


def scan_for_escapes(code: str) -> str | None:
    """Return a human-readable reason if `code` reaches for a blocked attribute.

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
    return None


class AttributeBlockedError(AttributeError):
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
        raise AttributeBlockedError(f"access to '{name}' is blocked by the sandbox")
    return getattr(obj, name, *default)


class PathBlockedError(PermissionError):
    """Raised when sandboxed code touches a path outside the allowlist."""


class NetworkBlockedError(PermissionError):
    """Raised when sandboxed code attempts any network operation."""


class ProcessBlockedError(PermissionError):
    """Raised when sandboxed code attempts to spawn or exec a process."""


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_WRITE_MODE_CHARS = frozenset("wax+")


def _interpreter_roots() -> tuple[str, ...]:
    """Directories the import machinery must be able to read.

    Importing a pure-Python stdlib module opens its source and its .pyc under
    the interpreter's library tree. Those reads are not a policy violation --
    denying them would break `import json` inside the sandbox -- so the tree is
    readable while remaining non-writable.
    """
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    roots.update(entry for entry in sys.path if entry)
    return tuple(sorted(os.path.realpath(r) for r in roots if r))


@dataclass(frozen=True)
class FilesystemPolicy:
    """Read/write containment rules, evaluated on fully resolved paths."""

    writable: tuple[str, ...]
    readable: tuple[str, ...]
    missing: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        allowed_directories: Iterable[str],
        scratch_dir: str | None = "/tmp/agent",
    ) -> FilesystemPolicy:
        """Resolve the configured directories, creating the scratch area.

        A configured directory that does not exist is kept in the policy rather
        than dropped: /testbed only exists once the SWE-bench container is
        mounted, and the sandbox must still start cleanly on a bare host.
        """
        requested = list(allowed_directories)
        if scratch_dir and scratch_dir not in requested:
            requested.append(scratch_dir)

        writable: list[str] = []
        missing: list[str] = []
        for entry in requested:
            resolved = os.path.realpath(entry)
            if not Path(resolved).is_dir():
                if scratch_dir and resolved == os.path.realpath(scratch_dir):
                    try:
                        Path(resolved).mkdir(parents=True, exist_ok=True)
                    except OSError:
                        missing.append(resolved)
                else:
                    missing.append(resolved)
            writable.append(resolved)

        return cls(
            writable=tuple(dict.fromkeys(writable)),
            readable=tuple(dict.fromkeys(writable + list(_interpreter_roots()))),
            missing=tuple(missing),
        )

    @staticmethod
    def _contained(path: str, roots: Iterable[str]) -> bool:
        resolved = os.path.realpath(path)
        return any(
            resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)
            for root in roots
        )

    def check(self, path: str, *, writing: bool) -> None:
        """Raise PathBlockedError unless `path` is inside the permitted roots."""
        roots = self.writable if writing else self.readable
        if self._contained(path, roots):
            return
        action = "write to" if writing else "read"
        allowed = ", ".join(self.writable) or "(none)"
        raise PathBlockedError(
            f"the sandbox is not permitted to {action} '{path}'. "
            f"Accessible directories: {allowed}"
        )


class _GuardState:
    """Whether the audit hook should currently enforce the policy.

    An audit hook can never be removed once installed and fires for every
    operation in the process -- including the worker's own pipe writes and
    pickling. Enforcement is therefore scoped to the exec() call itself.
    Sandboxed code cannot reach this object: `agent_smith` is not importable
    from inside the sandbox.
    """

    __slots__ = ("active", "policy")

    def __init__(self) -> None:
        self.active: bool = False
        self.policy: FilesystemPolicy | None = None


_STATE = _GuardState()

_PATH_EVENTS: dict[str, tuple[tuple[int, ...], bool]] = {
    "os.listdir": ((0,), False),
    "os.scandir": ((0,), False),
    "os.mkdir": ((0,), True),
    "os.rmdir": ((0,), True),
    "os.remove": ((0,), True),
    "os.unlink": ((0,), True),
    "os.rename": ((0, 1), True),
    "os.replace": ((0, 1), True),
    "os.chdir": ((0,), False),
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

_NETWORK_EVENTS = frozenset(
    {
        "socket.__new__",
        "socket.socket",
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "urllib.Request",
        "http.client.connect",
        "http.client.send",
        "ftplib.connect",
        "smtplib.connect",
        "smtplib.send",
    }
)

_PROCESS_EVENTS = frozenset(
    {
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "subprocess.Popen",
        "pty.spawn",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.call_function",
    }
)


def _is_write_open(args: tuple[Any, ...]) -> bool:
    """Infer write intent from the `open` audit event's mode/flags."""
    mode = args[1] if len(args) > 1 else None
    if isinstance(mode, str):
        return bool(_WRITE_MODE_CHARS & set(mode))
    flags = args[2] if len(args) > 2 else 0
    if isinstance(flags, int):
        return bool(flags & _WRITE_FLAGS)
    return True  # unknown shape: assume the stricter rule


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    """Deny filesystem, network and process operations for sandboxed code.

    Fires on the *operation*, not the name, so it holds even if a caller
    reached the underlying function by an unexpected route.
    """
    if not _STATE.active:
        return
    policy = _STATE.policy

    if event in _NETWORK_EVENTS:
        raise NetworkBlockedError(
            f"network access is disabled in the sandbox (blocked: {event})"
        )

    if event in _PROCESS_EVENTS:
        raise ProcessBlockedError(
            f"starting processes is disabled in the sandbox (blocked: {event})"
        )

    if policy is None:
        return

    if event == "open" and args:
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            policy.check(os.fsdecode(target), writing=_is_write_open(args))
        return

    spec = _PATH_EVENTS.get(event)
    if spec is not None:
        indices, writing = spec
        for index in indices:
            if len(args) > index and isinstance(args[index], (str, bytes, os.PathLike)):
                policy.check(os.fsdecode(args[index]), writing=writing)


def install_audit_hook(policy: FilesystemPolicy) -> None:
    """Install the process-wide hook. Idempotent; call once per worker."""
    _STATE.policy = policy
    if not getattr(install_audit_hook, "_installed", False):
        sys.addaudithook(_audit_hook)
        install_audit_hook._installed = True  # type: ignore[attr-defined]


@contextlib.contextmanager
def enforcement() -> Iterator[None]:
    """Enable policy enforcement for the duration of one exec()."""
    _STATE.active = True
    try:
        yield
    finally:
        _STATE.active = False


@contextlib.contextmanager
def suspended() -> Iterator[None]:
    """Pause enforcement while the worker itself works, not the model's code.

    Used around the MCP tool round trip: that pipe traffic is ours, and the
    hook would otherwise judge it by the rules written for sandboxed code.
    """
    previous = _STATE.active
    _STATE.active = False
    try:
        yield
    finally:
        _STATE.active = previous


def apply_memory_limit(max_memory_mb: int) -> None:
    """Cap the worker's address space. The kernel enforces this, not us.

    Raising the ceiling again requires privileges the worker does not have, so
    sandboxed code cannot undo it.
    """
    limit = max_memory_mb * 1024 * 1024
    for resource_id in (resource.RLIMIT_AS, resource.RLIMIT_DATA):
        try:
            _soft, hard = resource.getrlimit(resource_id)
            ceiling = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
            resource.setrlimit(resource_id, (ceiling, ceiling))
        except (ValueError, OSError):
            continue


_SAFE_BUILTINS = frozenset(
    {
        "abs",
        "aiter",
        "anext",
        "all",
        "any",
        "ascii",
        "bin",
        "bool",
        "bytearray",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "complex",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "format",
        "frozenset",
        "hasattr",
        "hash",
        "hex",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "map",
        "max",
        "min",
        "next",
        "object",
        "oct",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "zip",
        "True",
        "False",
        "None",
        "NotImplemented",
        "Ellipsis",
        "__build_class__",
        "__name__",
    }
)


def _guarded_open(
    file: Any,
    mode: str = "r",
    *args: Any,
    **kwargs: Any,
) -> IO[Any]:
    """open() restricted to the configured directories.

    The audit hook already denies out-of-policy opens, but reaching it means
    the model gets a message about an operation it did not know was checked.
    Checking here first produces the clearer error, and keeps the failure a
    PathBlockedError rather than whatever the hook interrupts.
    """
    policy = _STATE.policy
    if policy is not None and isinstance(file, (str, bytes, os.PathLike)):
        policy.check(os.fsdecode(file), writing=bool(_WRITE_MODE_CHARS & set(mode)))
    return _builtins.open(file, mode, *args, **kwargs)


def build_sandbox_builtins(guard: ImportGuard) -> dict[str, Any]:
    """Builtins dict handed to the sandbox namespace.

    Contains the vetted allowlist, every exception class (so user code can
    write `except ValueError`), and guarded replacements for the three
    capabilities that need policy rather than removal.
    """
    ns: dict[str, Any] = {
        name: getattr(_builtins, name)
        for name in _SAFE_BUILTINS
        if hasattr(_builtins, name)
    }

    for name in dir(_builtins):
        obj = getattr(_builtins, name)
        if isinstance(obj, type) and issubclass(obj, BaseException):
            ns[name] = obj

    ns["__import__"] = guard
    ns["getattr"] = _guarded_getattr
    ns["open"] = _guarded_open
    return ns
