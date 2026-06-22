"""Resolve monorepo root containing shared/."""

from __future__ import annotations

from pathlib import Path


def monorepo_root(start: Path | None = None) -> Path:
    here = start or Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "shared").is_dir():
            return parent
    return here.parents[1]
