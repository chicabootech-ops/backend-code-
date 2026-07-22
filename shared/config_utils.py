"""Shared configuration helpers for Chic A Boo services."""

from __future__ import annotations

from pathlib import Path


def load_pem(inline_value: str, file_path: str) -> str:
    """Return inline PEM content or read from file_path if set.

    Render/env UIs often store multiline PEMs with literal ``\\n`` escapes,
    surrounding quotes, or CRLF — normalize those for RS256.
    """
    if inline_value and inline_value.strip():
        value = inline_value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1].strip()
        # Always unescape literal \n / \r\n sequences from env UIs
        if "\\n" in value:
            value = value.replace("\\r\\n", "\n").replace("\\n", "\n")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        return value.strip()
    if not file_path:
        return ""
    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""
