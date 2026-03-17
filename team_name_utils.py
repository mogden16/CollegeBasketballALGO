"""Shared team-name normalization helpers."""

from __future__ import annotations

import re


def normalize_team_name(name: str) -> str:
    """Normalize team names for matching across data providers.

    - trims whitespace
    - strips leading/trailing numeric annotations
    - removes apostrophes and periods
    - converts ampersands to spaces
    - collapses repeated whitespace
    - lowercases for stable matching
    """
    cleaned = (name or "").strip()
    cleaned = re.sub(r"^\d+\s*", "", cleaned)
    cleaned = re.sub(r"\s*\d+$", "", cleaned)
    cleaned = re.sub(r"[’']", "", cleaned)
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace("&", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower().strip()
