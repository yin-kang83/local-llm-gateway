"""local-llm-gateway -- local-first, OpenAI-compatible, fail-closed LLM access.

The package intentionally has **zero third-party dependencies**: it talks to
any OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp server, a cloud
provider) over ``urllib`` and refuses to guess when something is wrong.

Public API::

    from local_llm_gateway import Gateway, GatewayConfig

    gw = Gateway(GatewayConfig(base_url="http://127.0.0.1:11434/v1",
                               model="qwen2.5:1.5b"))
    completion = gw.complete("只输出 JSON：{\"a\": 1}")
    data, _ = gw.complete_json("把金额抽成 JSON：...")
"""

from __future__ import annotations

from .errors import (
    BackendTimeout,
    BackendUnreachable,
    ConfigError,
    EmptyResponse,
    GatewayError,
    HTTPStatusError,
    JSONExtractionError,
    PromptTooLarge,
    ProtocolError,
    ResponseTooLarge,
)
from .extraction import extract_json
from .gateway import Completion, Gateway, GatewayConfig

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "Gateway",
    "GatewayConfig",
    "Completion",
    "extract_json",
    "GatewayError",
    "ConfigError",
    "PromptTooLarge",
    "BackendUnreachable",
    "BackendTimeout",
    "HTTPStatusError",
    "ProtocolError",
    "ResponseTooLarge",
    "EmptyResponse",
    "JSONExtractionError",
]
