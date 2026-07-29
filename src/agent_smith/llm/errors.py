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

    Header names are lowercased on the way in. HTTP header lookup is
    case-insensitive, but this is a plain `dict` and would not be: rather than
    leave CORE-2 to guess which casing the endpoint sent, the keys are
    normalised once here and `headers["retry-after"]` always works.
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
        self.headers: Mapping[str, str] = {
            name.lower(): value for name, value in (headers or {}).items()
        }
        self.body_excerpt = body[:BODY_EXCERPT_LIMIT]
        self.is_timeout = is_timeout
