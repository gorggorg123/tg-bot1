"""Small text helpers to safely coerce optional/typed values to strings."""

from __future__ import annotations


def safe_str(value) -> str:
    """Convert value to string; None becomes empty string."""

    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def safe_strip(value) -> str:
    """Stringify and strip whitespace safely."""

    return safe_str(value).strip()


__all__ = ["safe_str", "safe_strip"]
