"""Small text helpers to safely coerce optional/typed values to strings."""

from __future__ import annotations


def safe_str(value, max_len: int | None = None) -> str:
    """Convert value to string; None becomes empty string.

    Optionally trim the result to ``max_len`` characters and add an ellipsis.
    """

    if value is None:
        result = ""
    else:
        try:
            result = str(value)
        except Exception:
            result = ""

    if max_len is not None and len(result) > max_len:
        return result[:max_len] + "…"
    return result


def safe_strip(value) -> str:
    """Stringify and strip whitespace safely."""

    return safe_str(value).strip()


__all__ = ["safe_str", "safe_strip"]
