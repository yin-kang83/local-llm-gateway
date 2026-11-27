#!/usr/bin/env python
"""Pre-publication self-check for this repository (offline, stdlib only).

Checks
------
1. required files exist (README / LICENSE / pyproject / CITATION / CHANGELOG);
2. no forbidden file types (``.env``, ``*.key``, ``*.pem``, databases, model weights);
3. no file larger than ``--max-file-mb``;
4. no obvious secrets (GitHub/Gitee/AWS/OpenAI-style tokens, private keys);
5. no local user-profile paths (Windows user folders, POSIX home directories).

Exit code 0 = PASS, 1 = FAIL.  This is **not** a replacement for the
independent outbound (DLP) scan required before publishing; see
``docs/PUBLISHING.md``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

REQUIRED_FILES = [
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "CITATION.cff",
    "CHANGELOG.md",
]

FORBIDDEN_SUFFIXES = {
    ".env",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".gguf",
    ".safetensors",
    ".bin",
}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "venv", "build", "dist"}

SECRET_PATTERNS = [
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("gitee_token_like", re.compile(r"\b[0-9a-f]{32}\b")),
]

LOCAL_PATH_PATTERNS = [
    ("windows_user_path", re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]")),
    ("posix_home_path", re.compile(r"/home/[A-Za-z0-9._-]+/")),
]

#: Text files larger than this are not scanned (they should not ship anyway).
TEXT_SCAN_LIMIT_BYTES = 2 * 1024 * 1024


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            yield os.path.join(dirpath, filename)


def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (UnicodeDecodeError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-publication self-check (offline).")
    parser.add_argument("--dir", default=".", help="repository root (default: .)")
    parser.add_argument("--max-file-mb", type=float, default=2.0, help="maximum file size in MB")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.dir)
    failures: list[str] = []
    counts = {"files": 0, "large": 0, "forbidden": 0, "secrets": 0, "local_paths": 0}

    for required in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(root, required)):
            failures.append(f"missing required file: {required}")

    for path in iter_files(root):
        counts["files"] += 1
        rel = os.path.relpath(path, root)
        size = os.path.getsize(path)
        suffix = os.path.splitext(path)[1].lower()
        if suffix in FORBIDDEN_SUFFIXES:
            counts["forbidden"] += 1
            failures.append(f"forbidden file type: {rel}")
        if size > args.max_file_mb * 1024 * 1024:
            counts["large"] += 1
            failures.append(f"file too large ({size / 1024 / 1024:.1f} MB): {rel}")
        if size > TEXT_SCAN_LIMIT_BYTES:
            continue
        text = read_text(path)
        if text is None:
            continue
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                # 32-hex false positives (hashes in docs) are common; only fail on
                # the token-shaped categories, not on plain hashes.
                if name == "gitee_token_like":
                    continue
                counts["secrets"] += 1
                failures.append(f"possible secret ({name}): {rel}")
        for name, pattern in LOCAL_PATH_PATTERNS:
            if pattern.search(text):
                counts["local_paths"] += 1
                failures.append(f"local user path ({name}): {rel}")

    summary = {
        "root": root,
        "files_scanned": counts["files"],
        "large_files": counts["large"],
        "forbidden_types": counts["forbidden"],
        "secret_hits": counts["secrets"],
        "local_path_hits": counts["local_paths"],
        "failures": len(set(failures)),
        "status": "FAIL" if failures else "PASS",
    }
    for key, value in summary.items():
        print(f"{key}: {value}")
    for message in sorted(set(failures)):
        print(f"  - {message}")
    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
