"""Helpers to load local ITOM product knowledge base."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

BASE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_KB_FILE = BASE_DATA_DIR / "itom_qna_digest.txt"


@lru_cache(maxsize=1)
def _load_corpus() -> str:
    """Read the Q&A digest from disk once."""

    if not _DEFAULT_KB_FILE.exists():
        return ""
    try:
        return _DEFAULT_KB_FILE.read_text(encoding="utf-8")
    except Exception:
        return ""


def build_product_context(sku: int | str | None, *, extra_chunks: Iterable[str] | None = None) -> str:
    """Return textual context for a given SKU.

    Currently we ship a consolidated digest; SKU is reflected in the header to
    help the LLM focus on the requested item, and optional extra chunks can be
    appended by callers.
    """

    corpus = _load_corpus()
    parts = []
    if sku is not None:
        parts.append(f"Контекст по товару SKU {sku} (ITOM):")
    if corpus:
        parts.append(corpus)
    if extra_chunks:
        parts.extend(chunk for chunk in extra_chunks if chunk)
    return "\n\n".join(parts)


__all__ = ["build_product_context"]
