"""Strict JSON extraction from model output.

The extraction is deliberately conservative.  It accepts exactly two shapes:

1. the whole (stripped) response is a JSON object/array;
2. the response contains a Markdown fenced block such as ```json ... ```.

Anything else fails with :class:`JSONExtractionError`.  There is no "scan for
the first brace" fallback by default, because that is precisely how a model
hallucination leaks into a downstream deterministic pipeline.  Callers that
know their model is chatty can opt in with ``allow_prose=True``; even then
only a single balanced ``{...}`` / ``[...]`` block is considered.
"""

from __future__ import annotations

import json
import re

from .errors import JSONExtractionError

_FENCED = re.compile(r"```(?:json|JSON)?\s*(.+?)```", re.DOTALL)
_PROSE_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_PROSE_ARRAY = re.compile(r"\[.*\]", re.DOTALL)


def _loads_object_or_array(candidate: str):
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def extract_json(text: str, *, allow_prose: bool = False):
    """Return a ``dict``/``list`` extracted from ``text`` or raise.

    Parameters
    ----------
    text:
        Raw completion text.
    allow_prose:
        When ``True``, a single greedy ``{...}`` or ``[...]`` span is also
        attempted after the strict candidates fail.  Default ``False``.
    """
    if not isinstance(text, str):
        raise JSONExtractionError("completion text is not a string")
    stripped = text.strip()
    if not stripped:
        raise JSONExtractionError("completion text is empty")

    candidates = [stripped]
    candidates.extend(match.group(1).strip() for match in _FENCED.finditer(stripped))

    for candidate in candidates:
        parsed = _loads_object_or_array(candidate)
        if parsed is not None:
            return parsed

    if allow_prose:
        for pattern in (_PROSE_OBJECT, _PROSE_ARRAY):
            match = pattern.search(stripped)
            if match:
                parsed = _loads_object_or_array(match.group(0))
                if parsed is not None:
                    return parsed

    raise JSONExtractionError(
        "no JSON object/array could be extracted from the completion",
        detail={
            "candidates_tried": len(candidates),
            "allow_prose": bool(allow_prose),
            "text_bytes": len(stripped.encode("utf-8")),
        },
    )
