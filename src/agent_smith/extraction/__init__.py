"""Turning whatever the model wrote into Python the sandbox can run."""

from agent_smith.extraction.extract import extract_code
from agent_smith.extraction.result import ExtractionResult, Strategy

__all__ = [
    "ExtractionResult",
    "Strategy",
    "extract_code",
]
