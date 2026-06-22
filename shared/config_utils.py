"""Shared configuration helpers for Chic A Boo services."""

from __future__ import annotations

from pathlib import Path


def load_pem(inline_value: str, file_path: str) -> str:
    """Return inline PEM content or read from file_path if set."""
    if inline_value and inline_value.strip():
        return inline_value.strip()
    if not file_path:
        return ""
    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""
