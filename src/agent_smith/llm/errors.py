"""The single error type raised by the provider."""

from collections.abc import Mapping

BODY_EXCERPT_LIMIT = 500


class ProviderError(Exception):
    """The LLM call did not produce a usable response."""

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
