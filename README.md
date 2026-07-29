# agent-smith

## Outbound network access

The agent makes exactly one kind of outbound HTTP request: the inference call
to the LLM endpoint given by `--provider-url`. It lives in
`src/agent_smith/llm/openai_compat.py`, the only module that imports an HTTP
client, and `tests/test_llm_import_boundary.py` fails the build if a second one
appears.

`src/agent_smith/config/keys.py` imports `urllib.parse` to read the provider
host out of that URL. It parses a string; it opens no connection.

Nothing else in the project reaches the network. In particular, the agent never
fetches task data, repositories or reference solutions.
