from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    """How a single execution ended"""

    OK = "ok"
    ERROR = "error"
    SOFT_TIMEOUT = "soft_timeout"
    HARD_TIMEOUT = "hard_timeout"
    CRASHED = "crashed"
    FINAL_ANSWER = "final_answer"
    SHUTDOWN = "shutdown"
    BLOCKED = "blocked"


@dataclass
class ExecRequest:
    """process --> worker communication pipeline."""

    code: str


@dataclass
class ExecResult:
    """Worker --> process communication pipeline."""

    outcome: Outcome
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    error: str | None = None
    final_answer: str | None = None
    duration_ms: float = 0.0
    namespace_reset: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


class FinalAnswerSignal(BaseException):
    """Raised by the injected final_answer() to unwind out of user code.

    Inherits BaseException, not Exception, so a broad ``except Exception`` in
    user code cannot swallow it. The executed code is LLM-written and routinely
    wraps its own work in try/except Exception; catching only Exception here
    would silently lose the final answer. The worker catches this class
    explicitly, before its own ``except Exception``.
    """

    def __init__(self, value: object) -> None:
        super().__init__("final_answer called")
        self.value = value
