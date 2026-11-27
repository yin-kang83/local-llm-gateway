"""Small, honest benchmark helper.

The benchmark answers one practical question: *how fast is this model on this
machine, for the JSON-extraction prompt shape I actually use?*  It therefore:

* runs one warm-up call (model load / page cache) that is **not** measured;
* measures ``n`` sequential calls and reports the distribution, not just mean;
* reports tokens/second only when the backend returns ``usage`` numbers --
  otherwise it reports ``None`` instead of inventing a value;
* fails loudly if any measured call fails (no averaging over errors).

Hardware information is collected from the Python standard library only and
never contains host names, user names or paths.
"""

from __future__ import annotations

import os
import platform
import statistics
import sys

from .errors import ConfigError

#: Default probe prompt: the shape the gateway is designed for (strict JSON).
DEFAULT_BENCH_PROMPT = (
    '只输出一行 JSON，不要解释，不要 Markdown：\n'
    '{"task": "ping", "ok": true}'
)


def hardware_info() -> dict:
    """Return generic hardware/environment facts (no identifiers)."""
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "processor": platform.processor() or None,
        "python": sys.version.split()[0],
    }


def run_benchmark(gateway, *, n: int = 3, warmup: int = 1, prompt: str | None = None) -> dict:
    """Run ``warmup + n`` completions and return a measurement report."""
    if not isinstance(n, int) or n < 1:
        raise ConfigError("n must be a positive integer")
    if not isinstance(warmup, int) or warmup < 0:
        raise ConfigError("warmup must be a non-negative integer")
    probe = prompt if prompt is not None else DEFAULT_BENCH_PROMPT

    for _ in range(warmup):
        gateway.complete(probe)  # warm-up failures must surface, not be swallowed

    latencies: list[float] = []
    completion_tokens = 0
    usage_reported = False
    attempts_total = 0
    for _ in range(n):
        completion = gateway.complete(probe)
        latencies.append(completion.latency_s)
        attempts_total += completion.attempts
        tokens = completion.usage.get("completion_tokens")
        if isinstance(tokens, int) and tokens >= 0:
            usage_reported = True
            completion_tokens += tokens

    ordered = sorted(latencies)
    total_s = sum(latencies)
    tokens_per_second = round(completion_tokens / total_s, 2) if usage_reported and total_s > 0 else None
    return {
        "ok": True,
        "host": gateway.host,
        "model": gateway.config.model,
        "probe_prompt_bytes": len(probe.encode("utf-8")),
        "n": n,
        "warmup": warmup,
        "latency_s": {
            "min": round(ordered[0], 4),
            "median": round(statistics.median(ordered), 4),
            "max": round(ordered[-1], 4),
            "total": round(total_s, 4),
        },
        "attempts_total": attempts_total,
        "completion_tokens_total": completion_tokens if usage_reported else None,
        "tokens_per_second": tokens_per_second,
        "hardware": hardware_info(),
    }
