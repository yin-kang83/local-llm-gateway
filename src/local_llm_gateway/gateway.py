"""Gateway facade: configuration, completions, JSON contract, evidence log.

A :class:`Gateway` is a thin, explicit object on purpose.  It owns exactly
one backend and answers three questions:

* "can I reach the model, and is the model I configured really there?" ->
  :meth:`Gateway.check`
* "give me a completion, or fail loudly" -> :meth:`Gateway.complete`
* "give me a JSON object, or fail loudly" -> :meth:`Gateway.complete_json`

Every successful call can be written to a JSONL evidence log that stores the
SHA-256 of prompt and response (plus latency / attempts / usage) but **not**
the prompt text.  That makes the call auditable without turning the evidence
file into a copy of client data.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .backends import DEFAULT_BASE_URL, DEFAULT_MODEL, OpenAICompatibleBackend
from .errors import ConfigError, JSONExtractionError
from .extraction import extract_json

EVIDENCE_COMPLETION = "completion"
EVIDENCE_JSON_FAILURE = "json_extraction_failure"


@dataclass(frozen=True)
class GatewayConfig:
    """Immutable gateway configuration.

    Prefer :meth:`from_env` in deployment: environment variables keep API
    keys out of shell history and out of the repository.
    """

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str | None = None
    timeout_s: float = 60.0
    max_retries: int = 2
    temperature: float = 0.0
    seed: int | None = None
    max_prompt_bytes: int = 32 * 1024
    max_response_bytes: int = 64 * 1024
    evidence_path: str | None = None
    evidence_store_text: bool = False

    @classmethod
    def from_env(cls, env: dict | None = None) -> "GatewayConfig":
        """Build a config from ``LLM_GATEWAY_*`` environment variables."""
        source = os.environ if env is None else env

        def _get(name: str, default=None):
            value = source.get(name)
            return value if value not in (None, "") else default

        def _as_int(name: str, default: int) -> int:
            raw = _get(name)
            if raw is None:
                return default
            try:
                return int(str(raw).strip())
            except ValueError:
                raise ConfigError(f"{name} must be an integer") from None

        def _as_float(name: str, default: float) -> float:
            raw = _get(name)
            if raw is None:
                return default
            try:
                return float(str(raw).strip())
            except ValueError:
                raise ConfigError(f"{name} must be a number") from None

        return cls(
            base_url=str(_get("LLM_GATEWAY_BASE_URL", DEFAULT_BASE_URL)),
            model=str(_get("LLM_GATEWAY_MODEL", DEFAULT_MODEL)),
            api_key=_get("LLM_GATEWAY_API_KEY"),
            timeout_s=_as_float("LLM_GATEWAY_TIMEOUT_S", 60.0),
            max_retries=_as_int("LLM_GATEWAY_MAX_RETRIES", 2),
            temperature=_as_float("LLM_GATEWAY_TEMPERATURE", 0.0),
            seed=_as_int("LLM_GATEWAY_SEED", None) if _get("LLM_GATEWAY_SEED") is not None else None,
            max_prompt_bytes=_as_int("LLM_GATEWAY_MAX_PROMPT_BYTES", 32 * 1024),
            max_response_bytes=_as_int("LLM_GATEWAY_MAX_RESPONSE_BYTES", 64 * 1024),
            evidence_path=_get("LLM_GATEWAY_EVIDENCE_PATH"),
            evidence_store_text=str(_get("LLM_GATEWAY_EVIDENCE_STORE_TEXT", "0")).lower()
            in {"1", "true", "yes"},
        )


@dataclass(frozen=True)
class Completion:
    """Validated completion plus audit metadata (never contains the API key)."""

    text: str
    model: str
    latency_s: float
    attempts: int
    prompt_sha256: str
    response_sha256: str
    usage: dict = field(default_factory=dict)
    finish_reason: str | None = None

    def meta(self) -> dict:
        return {
            "model": self.model,
            "latency_s": round(self.latency_s, 6),
            "attempts": self.attempts,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "prompt_sha256": self.prompt_sha256,
            "response_sha256": self.response_sha256,
        }


class Gateway:
    """Fail-closed facade over an OpenAI-compatible backend."""

    def __init__(self, config: GatewayConfig | None = None) -> None:
        self.config = config or GatewayConfig.from_env()
        self.backend = OpenAICompatibleBackend(
            base_url=self.config.base_url,
            model=self.config.model,
            api_key=self.config.api_key,
            timeout_s=self.config.timeout_s,
            max_retries=self.config.max_retries,
            max_prompt_bytes=self.config.max_prompt_bytes,
            max_response_bytes=self.config.max_response_bytes,
            temperature=self.config.temperature,
            seed=self.config.seed,
        )

    # ---------------------------------------------------------------- public

    @property
    def host(self) -> str:
        return self.backend.host

    def complete(self, prompt: str, system: str | None = None) -> Completion:
        """Return one completion or raise a :class:`GatewayError`."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConfigError("prompt must be a non-empty string")
        messages: list[dict] = []
        if system is not None:
            if not isinstance(system, str) or not system.strip():
                raise ConfigError("system prompt must be a non-empty string when given")
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        raw = self.backend.complete(messages)
        completion = Completion(
            text=raw.text,
            model=raw.model,
            latency_s=raw.latency_s,
            attempts=raw.attempts,
            prompt_sha256=_sha256_text(json.dumps(messages, ensure_ascii=False, sort_keys=True)),
            response_sha256=_sha256_text(raw.text),
            usage=raw.usage,
            finish_reason=raw.finish_reason,
        )
        self._record(
            {
                "kind": EVIDENCE_COMPLETION,
                "host": self.host,
                "model": completion.model,
                "latency_s": round(completion.latency_s, 6),
                "attempts": completion.attempts,
                "usage": completion.usage,
                "finish_reason": completion.finish_reason,
                "prompt_sha256": completion.prompt_sha256,
                "response_sha256": completion.response_sha256,
                "response_bytes": len(completion.text.encode("utf-8")),
                **({"response_text": completion.text} if self.config.evidence_store_text else {}),
            }
        )
        return completion

    def complete_json(
        self,
        prompt: str,
        system: str | None = None,
        *,
        allow_prose: bool = False,
    ) -> tuple[dict | list, Completion]:
        """Return ``(parsed_json, completion)`` or raise ``JSONExtractionError``."""
        completion = self.complete(prompt, system=system)
        try:
            data = extract_json(completion.text, allow_prose=allow_prose)
        except JSONExtractionError as exc:
            self._record(
                {
                    "kind": EVIDENCE_JSON_FAILURE,
                    "host": self.host,
                    "model": completion.model,
                    "prompt_sha256": completion.prompt_sha256,
                    "response_sha256": completion.response_sha256,
                    "detail": exc.detail,
                }
            )
            raise
        return data, completion

    def check(self) -> dict:
        """Probe the backend and report whether the configured model exists."""
        started = time.perf_counter()
        models = self.backend.list_models()
        latency_s = time.perf_counter() - started
        return {
            "ok": True,
            "host": self.host,
            "base_url": self.config.base_url,
            "model": self.config.model,
            "model_available": self.config.model in models,
            "models": models,
            "latency_s": round(latency_s, 4),
        }

    def benchmark(self, *, n: int = 3, warmup: int = 1, prompt: str | None = None) -> dict:
        """Measure latency and (when reported) output tokens/second."""
        from .bench import run_benchmark

        return run_benchmark(self, n=n, warmup=warmup, prompt=prompt)

    # --------------------------------------------------------------- private

    def _record(self, record: dict) -> None:
        path = self.config.evidence_path
        if not path:
            return
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
