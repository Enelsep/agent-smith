"""The prompt texts, kept as files so they can be edited without touching code."""

from __future__ import annotations

from importlib.resources import files

SUFFIX = ".md"


def load_prompt(name: str) -> str:
    """Read one prompt out of this package.

    `name` is the stem: `load_prompt("mbpp")` reads `mbpp.md`. Reading through
    `importlib.resources` rather than `__file__` is what keeps this working
    from an installed wheel as well as from a source checkout.
    """
    resource = files(__package__).joinpath(f"{name}{SUFFIX}")
    if not resource.is_file():
        raise FileNotFoundError(f"no prompt named {name!r} in {__package__}")
    return resource.read_text(encoding="utf-8")
