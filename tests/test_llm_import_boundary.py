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
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = REPO_ROOT / "src"
ALLOWED_HTTP_MODULE = "src/agent_smith/llm/openai_compat.py"

# Named clients rather than a socket-level property, so the list is only ever as
# good as what it names. `mcp` and `fastmcp` are in it before a line of MCP code
# exists: MCP-1 introduces a second outbound transport (`streamablehttp_client`
# for `--mcp-server <URL>`), and this test failing is how that arrives as a
# decision instead of an oversight.
HTTP_CLIENTS = frozenset(
    {
        "httpx",
        "requests",
        "urllib3",
        "urllib.request",
        "http.client",
        "aiohttp",
        "mcp",
        "fastmcp",
    }
)


def _scanned_files() -> list[Path]:
    """Every Python file we ship: the package, plus the modules at the root.

    `main.py` and `mcp_tools_*.py` sit outside `src/` and are force-included in
    the wheel by `pyproject.toml`, so they are part of the reviewable surface
    even though they are not part of the package.
    """
    return sorted(SOURCE_ROOT.rglob("*.py")) + sorted(REPO_ROOT.glob("*.py"))


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
        path.relative_to(REPO_ROOT).as_posix(): sorted(_http_clients_in(path))
        for path in _scanned_files()
        if path.relative_to(REPO_ROOT).as_posix() != ALLOWED_HTTP_MODULE
        and _http_clients_in(path)
    }
    assert offenders == {}


def test_the_modules_at_the_repository_root_are_scanned_too() -> None:
    # Guards the test above against silently covering src/ only: main.py and
    # the two mcp_tools_*.py are shipped in the wheel and are the first files
    # a reviewer opens.
    scanned = {path.relative_to(REPO_ROOT).as_posix() for path in _scanned_files()}
    assert {"main.py", "mcp_tools_mbpp.py", "mcp_tools_swebench.py"} <= scanned


def test_the_provider_module_does_import_one() -> None:
    # Guards the test above against passing because the search broke.
    provider = REPO_ROOT / ALLOWED_HTTP_MODULE
    assert _http_clients_in(provider) == {"httpx"}


def test_importing_the_contract_does_not_load_an_http_client() -> None:
    # The AST scan above proves no module outside the provider names an HTTP
    # client; it says nothing about what a package import pulls in behind it.
    # `agent_smith.llm` re-exports the contract and stops there, so CORE-2 and
    # CORE-4 can import `LLMProvider` without loading `httpx`. Re-exporting the
    # provider from `__init__.py` would undo that silently, hence this test.
    # A subprocess is the only honest probe: by the time pytest collects this
    # file, `httpx` is long since in our own `sys.modules`.
    probe = "import sys, agent_smith.llm; print('httpx' in sys.modules)"
    loaded = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert loaded == "False"


def test_parsing_a_url_is_not_making_a_request() -> None:
    # config/keys.py imports urllib.parse to read a host out of the provider
    # URL. It matches a substring search for "urllib" and opens nothing.
    keys = SOURCE_ROOT / "agent_smith/config/keys.py"
    assert "urllib.parse" in _imported_modules(keys)
    assert _http_clients_in(keys) == set()
