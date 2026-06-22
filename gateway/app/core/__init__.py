"""Gateway error types."""

from __future__ import annotations


class GatewayError(Exception):
    def __init__(self, message: str, *, status_code: int = 502, code: str = "gateway_error") -> None:
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(message)
