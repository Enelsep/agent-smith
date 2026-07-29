"""The single error type raised by the provider."""

from collections.abc import Mapping

# Long enough to hold a provider's JSON error object, short enough to print.
BODY_EXCERPT_LIMIT = 500


class ProviderError(Exception):
    """The LLM call did not produce a usable response.

    One type, so the orchestrator catches one thing and can guarantee it never
    crashes. Populated fields, so the retry layer can still tell a rate limit
    from a server fault without importing a transport of its own.

    `headers` are the *response* headers: that is where `retry-after` and the
    rate-limit resets live, and it means a request's `Authorization` header
    cannot travel inside this exception. No API key is stored here.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        headers: Mapping[str, str] | None = None,
        body: str = "",
        is_timeout: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers: Mapping[str, str] = dict(headers or {})
        self.body_excerpt = body[:BODY_EXCERPT_LIMIT]
        self.is_timeout = is_timeout
