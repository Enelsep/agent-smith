# agent-smith

## Outbound network access

The agent makes one kind of outbound HTTP request: the inference call to the
LLM endpoint given by `--provider-url`. It lives in
`src/agent_smith/llm/openai_compat.py`, the only module that imports an HTTP
client.

`tests/test_llm_import_boundary.py` enforces that. It parses the imports of
every Python file we ship — the package under `src/`, plus `main.py` and the
two `mcp_tools_*.py` at the root — and fails the build if any module other
than the provider imports a client we know of. The check is a named list, so
it is worth stating what it does not do: it catches an imported client, not a
hand-rolled socket.

`src/agent_smith/config/keys.py` imports `urllib.parse` to read the provider
host out of that URL. It parses a string; it opens no connection.

The MCP transport reached through `--mcp-server <URL>` will be a second
outbound path when it lands. The MCP SDK is already in the list above, so that
ticket will fail this test and update this section, rather than quietly
outgrow it.

What the agent never does, at any point and by any route: fetch task data,
repositories, or reference solutions.

## Reading what the model wrote

Model replies are parsed by `agent_smith.extraction`, which accepts fenced Python,
Anthropic-style `<invoke>` XML, Hermes `<tool_call>` JSON and ReAct `Action:` pairs, and
rewrites everything that is not already Python into a Python call string. It performs no
network access and knows nothing about which tools exist: an unknown tool name is
normalised like any other and fails in the sandbox, where the model reads the error and
tries again.
