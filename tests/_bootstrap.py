"""Shared test bootstrap: import the package from ``src`` without installing."""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (_ROOT, os.path.join(_ROOT, "src"), _TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)
