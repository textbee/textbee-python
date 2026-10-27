from __future__ import annotations

from typing import Any


class TextbeeError(Exception):
    """Raised when the textbee API answers with an error, a redirect, or a response
    that is not the API's JSON (usually a wrong ``base_url``).

    Network failures are not wrapped: those raise whatever ``urllib`` raised,
    an ``OSError`` such as ``URLError`` or a socket timeout.
    """

    status: int
    """HTTP status code returned by the API."""

    message: str
    """Error text from the response body, or ``HTTP <status>``."""

    body: Any
    """Parsed response body, or the raw text when the body was not JSON."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body

    def __reduce__(self) -> tuple[Any, ...]:
        # Keeps the error intact across pickling (multiprocessing, Celery).
        return (type(self), (self.status, self.message, self.body))
