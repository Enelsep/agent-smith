"""Only one module in the project makes outbound HTTP requests.

A blanket text search for HTTP clients across submitted sources is a standard
anti-cheat signal, and our inference call cannot avoid one. Keeping the import
in a single, plainly named module is what makes the surface reviewable at a
glance; this test is what keeps it there as the project grows.

`urllib.parse` is allowed and `urllib.request` is not: one splits strings, the
other opens sockets. That is the distinction a reviewer makes by eye, so it is
the one worth encoding.
"""

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
ALLOWED_HTTP_MODULE = "agent_smith/llm/openai_compat.py"
HTTP_CLIENTS = frozenset(
    {"httpx", "requests", "urllib3", "urllib.request", "http.client"}
)


def _imported_modules(path: Path) -> set[str]:
    """Every module name the file imports, dotted paths included."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _http_clients_in(path: Path) -> set[str]:
    return {
        module
        for module in _imported_modules(path)
        if module in HTTP_CLIENTS or module.split(".")[0] in HTTP_CLIENTS
    }


def test_only_the_provider_module_imports_an_http_client() -> None:
    offenders = {
        path.relative_to(SOURCE_ROOT).as_posix(): sorted(_http_clients_in(path))
        for path in sorted(SOURCE_ROOT.rglob("*.py"))
        if path.relative_to(SOURCE_ROOT).as_posix() != ALLOWED_HTTP_MODULE
        and _http_clients_in(path)
    }
    assert offenders == {}


def test_the_provider_module_does_import_one() -> None:
    # Guards the test above against passing because the search broke.
    provider = SOURCE_ROOT / ALLOWED_HTTP_MODULE
    assert _http_clients_in(provider) == {"httpx"}


def test_parsing_a_url_is_not_making_a_request() -> None:
    # config/keys.py imports urllib.parse to read a host out of the provider
    # URL. It matches a substring search for "urllib" and opens nothing.
    keys = SOURCE_ROOT / "agent_smith/config/keys.py"
    assert "urllib.parse" in _imported_modules(keys)
    assert _http_clients_in(keys) == set()
