from __future__ import annotations

from dataclasses import dataclass  # ,field <- for truncated: bool later
from enum import Enum


class Outcome(str, Enum):
    """How a single execution ended"""

    OK = "ok"
    ERROR = "error"
    SOFT_TIMEOUT = "soft_timeout"
    HARD_TIMEOUT = "hard_timeout"
    FINAL_ANSWER = "final_answer"
    SHUTDOWN = "shutdown"


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
    error: str | None = None
    final_answer: str | None = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


class FinalAnswerSignal(Exception):
    """Raised by the injected final_answer() to unwind out of user code.

    Private to the sandbox: user code has no reference to this class, so it
    cannot deliberately catch it.
    """

    def __init__(self, value: object) -> None:
        super().__init__("final_answer called")
        self.value = value
