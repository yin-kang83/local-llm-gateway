"""Typed, stable errors for the local LLM gateway.

Design rule: a failure is *always* explicit.  The gateway never degrades into
a guessed or fabricated answer.  Callers branch on the class / ``code`` and
treat every :class:`GatewayError` as a hard stop.

Each error carries:

* ``code``      -- stable machine-readable string (safe to log / switch on);
* ``exit_code`` -- suggested CLI exit code;
* ``detail``    -- optional structured context that never contains secrets.

Error messages must never include API keys, full prompts or full model
responses.  Only bounded, sanitised excerpts are allowed.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for every gateway failure."""

    code = "gateway_error"
    exit_code = 1

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = dict(detail or {})

    def to_dict(self) -> dict:
        return {"error": self.code, "message": str(self), "detail": self.detail}


class ConfigError(GatewayError):
    """Invalid or missing configuration (bad URL, bad limits, ...)."""

    code = "config_error"
    exit_code = 2


class PromptTooLarge(GatewayError):
    """The prompt exceeds the configured byte budget (checked client-side)."""

    code = "prompt_too_large"
    exit_code = 2


class BackendUnreachable(GatewayError):
    """The backend could not be reached (connection refused / DNS / TLS)."""

    code = "backend_unreachable"
    exit_code = 3


class BackendTimeout(GatewayError):
    """The backend did not answer within the configured timeout."""

    code = "backend_timeout"
    exit_code = 3


class HTTPStatusError(GatewayError):
    """The backend returned a non-retryable (or exhausted) HTTP status."""

    code = "http_status_error"
    exit_code = 3


class ProtocolError(GatewayError):
    """The response does not follow the OpenAI-compatible chat contract."""

    code = "protocol_error"
    exit_code = 4


class ResponseTooLarge(GatewayError):
    """The response exceeded the configured byte budget."""

    code = "response_too_large"
    exit_code = 4


class EmptyResponse(GatewayError):
    """The backend returned an empty / whitespace-only completion."""

    code = "empty_response"
    exit_code = 4


class JSONExtractionError(GatewayError):
    """No parseable JSON object/array could be extracted from the response."""

    code = "json_extraction_error"
    exit_code = 5
