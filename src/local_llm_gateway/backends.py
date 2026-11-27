"""OpenAI-compatible HTTP backend (``/v1/chat/completions``).

Zero third-party dependencies: this module only uses ``urllib`` from the
standard library.  It is deliberately small and boring -- the interesting
guarantees live in the explicit error handling:

* connection refused / DNS / TLS  -> ``BackendUnreachable``
* socket timeout                  -> ``BackendTimeout``
* HTTP 4xx (except 408/429)       -> ``HTTPStatusError`` (no retry)
* HTTP 408/429/5xx                -> bounded retry, then ``HTTPStatusError``
* oversized request / response    -> ``PromptTooLarge`` / ``ResponseTooLarge``
* malformed response body         -> ``ProtocolError``
* empty completion                -> ``EmptyResponse``

Retries are bounded (``max_retries``), exponentially backed off and never
applied to non-idempotent client errors.  API keys are only ever placed in
the request header; they never appear in exception messages or logs.
"""

from __future__ import annotations

import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .errors import (
    BackendTimeout,
    BackendUnreachable,
    ConfigError,
    EmptyResponse,
    HTTPStatusError,
    PromptTooLarge,
    ProtocolError,
    ResponseTooLarge,
)

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen2.5:1.5b"
USER_AGENT = "local-llm-gateway/0.1.0"

#: Statuses worth retrying: the request may succeed on a later attempt.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Upper bound for the excerpt kept from an HTTP error body.
MAX_ERROR_EXCERPT = 300

#: Upper bound for the number of logical messages accepted per request.
MAX_MESSAGES = 64


@dataclass(frozen=True)
class RawCompletion:
    """Backend response after contract validation (still plain text)."""

    text: str
    model: str
    latency_s: float
    attempts: int
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None


def normalise_base_url(base_url: str) -> str:
    """Validate and normalise an OpenAI-compatible base URL.

    Accepts ``http(s)://host:port`` (``/v1`` is appended when the path is
    empty) or any explicit path such as ``.../v1``.  Trailing slashes are
    removed so endpoint joining is deterministic.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError("base_url must be a non-empty string")
    url = base_url.strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ConfigError("base_url must start with http:// or https://")
    if not parts.netloc:
        raise ConfigError("base_url is missing a host")
    if not parts.path:
        url = url + "/v1"
    return url


class OpenAICompatibleBackend:
    """Minimal, fail-closed client for OpenAI-compatible chat endpoints."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        *,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        backoff_s: float = 0.5,
        max_backoff_s: float = 8.0,
        max_prompt_bytes: int = 32 * 1024,
        max_response_bytes: int = 64 * 1024,
        temperature: float = 0.0,
        seed: int | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ConfigError("model must be a non-empty string")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            raise ConfigError("timeout_s must be a positive number")
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 10:
            raise ConfigError("max_retries must be an integer in [0, 10]")
        if not isinstance(max_prompt_bytes, int) or max_prompt_bytes <= 0:
            raise ConfigError("max_prompt_bytes must be a positive integer")
        if not isinstance(max_response_bytes, int) or max_response_bytes <= 0:
            raise ConfigError("max_response_bytes must be a positive integer")
        if not 0.0 <= float(temperature) <= 2.0:
            raise ConfigError("temperature must be in [0.0, 2.0]")

        self.base_url = normalise_base_url(base_url)
        self.model = model.strip()
        self._api_key = api_key.strip() if api_key else None
        self.timeout_s = float(timeout_s)
        self.max_retries = max_retries
        self.backoff_s = float(backoff_s)
        self.max_backoff_s = float(max_backoff_s)
        self.max_prompt_bytes = max_prompt_bytes
        self.max_response_bytes = max_response_bytes
        self.temperature = float(temperature)
        self.seed = seed
        self._user_agent = user_agent
        self._host = urllib.parse.urlsplit(self.base_url).netloc

    # ------------------------------------------------------------------ API

    @property
    def host(self) -> str:
        """Backend host (safe to log; never contains credentials)."""
        return self._host

    def complete(self, messages: list[dict]) -> RawCompletion:
        """Send chat messages and return the validated completion."""
        if not isinstance(messages, list) or not messages:
            raise ConfigError("messages must be a non-empty list")
        if len(messages) > MAX_MESSAGES:
            raise ConfigError(f"messages must contain at most {MAX_MESSAGES} items")

        prompt_bytes = 0
        for message in messages:
            if not isinstance(message, dict) or "role" not in message or "content" not in message:
                raise ConfigError("each message must be a dict with role/content")
            if not isinstance(message["content"], str):
                raise ConfigError("message content must be a string")
            prompt_bytes += len(message["content"].encode("utf-8"))
        if prompt_bytes > self.max_prompt_bytes:
            raise PromptTooLarge(
                f"prompt is {prompt_bytes} bytes, limit is {self.max_prompt_bytes} bytes",
                detail={"prompt_bytes": prompt_bytes, "limit": self.max_prompt_bytes},
            )

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if self.seed is not None:
            payload["seed"] = self.seed

        parsed, latency_s, attempts = self._request_json("POST", "/chat/completions", payload)
        return self._parse_completion(parsed, latency_s, attempts)

    def list_models(self) -> list[str]:
        """Return the model ids advertised by ``GET /models``."""
        parsed, _latency_s, _attempts = self._request_json("GET", "/models", None)
        data = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(data, list):
            raise ProtocolError("GET /models response has no 'data' list")
        models = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                models.append(item["id"])
        if not models:
            raise ProtocolError("GET /models returned no model ids")
        return models

    # ------------------------------------------------------------- internals

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request_json(self, method: str, path: str, payload: dict | None):
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(body) > self.max_prompt_bytes * 2:
                raise PromptTooLarge(
                    "serialised request body exceeds the configured guard",
                    detail={"body_bytes": len(body)},
                )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self._user_agent,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.perf_counter()
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                request = urllib.request.Request(
                    self._endpoint(path), data=body, headers=headers, method=method
                )
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise ResponseTooLarge(
                        f"response exceeds {self.max_response_bytes} bytes",
                        detail={"limit": self.max_response_bytes},
                    )
                latency_s = time.perf_counter() - started
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    raise ProtocolError(
                        "backend response is not valid JSON",
                        detail={"bytes": len(raw)},
                    ) from None
                if not isinstance(parsed, dict):
                    raise ProtocolError("backend response JSON is not an object")
                return parsed, latency_s, attempt

            except urllib.error.HTTPError as exc:
                status = exc.code
                excerpt = self._error_excerpt(exc)
                if status in RETRYABLE_STATUS and attempt < attempts:
                    self._backoff(attempt)
                    continue
                raise HTTPStatusError(
                    f"backend returned HTTP {status}",
                    detail={"status": status, "path": path, "body_excerpt": excerpt},
                ) from None

            except (socket.timeout, TimeoutError):
                if attempt < attempts:
                    self._backoff(attempt)
                    continue
                raise BackendTimeout(
                    f"backend did not answer within {self.timeout_s}s",
                    detail={"attempts": attempt, "timeout_s": self.timeout_s},
                ) from None

            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                if attempt < attempts:
                    self._backoff(attempt)
                    continue
                reason = getattr(exc, "reason", exc)
                raise BackendUnreachable(
                    "backend unreachable",
                    detail={"attempts": attempt, "reason": type(reason).__name__},
                ) from None

        # Unreachable: the loop either returns or raises on the last attempt.
        raise AssertionError("retry loop exited without result")  # pragma: no cover

    @staticmethod
    def _error_excerpt(exc: urllib.error.HTTPError) -> str:
        try:
            text = exc.read(MAX_ERROR_EXCERPT + 1).decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            return ""
        text = " ".join(text.split())
        return text[:MAX_ERROR_EXCERPT]

    def _backoff(self, attempt: int) -> None:
        delay = min(self.backoff_s * (2 ** (attempt - 1)), self.max_backoff_s)
        delay += random.uniform(0.0, delay * 0.1)
        time.sleep(delay)

    def _parse_completion(self, parsed: dict, latency_s: float, attempts: int) -> RawCompletion:
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProtocolError("response has no 'choices' list")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise ProtocolError("response 'choices[0]' is not an object")

        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if content is None:
            raise ProtocolError("response 'choices[0].message.content' is missing")
        if isinstance(content, list):  # content parts (non-streaming) form
            parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            content = "".join(parts)
        if not isinstance(content, str):
            raise ProtocolError("completion content is not a string")
        if not content.strip():
            raise EmptyResponse("backend returned an empty completion")

        usage = parsed.get("usage")
        usage = dict(usage) if isinstance(usage, dict) else {}
        model = parsed.get("model")
        finish_reason = choice.get("finish_reason")
        return RawCompletion(
            text=content,
            model=model if isinstance(model, str) and model else self.model,
            latency_s=latency_s,
            attempts=attempts,
            usage=usage,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        )
