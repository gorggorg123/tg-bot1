from __future__ import annotations

import logging
from typing import Iterable, List

from .store import get_memory_store
from .schemas import MemoryRecord

logger = logging.getLogger(__name__)


def fetch_examples(*, kind: str, input_text: str, sku: str | None = None, limit: int = 5) -> List[MemoryRecord]:
    store = get_memory_store()
    examples = store.query_similar(kind=kind, input_text=input_text, sku=sku, limit=limit)
    if examples:
        hashes = ", ".join([e.hash or "" for e in examples])
        logger.info(
            "AI memory retrieval: kind=%s sku=%s examples=%d hashes=[%s]",
            kind,
            sku,
            len(examples),
            hashes,
        )
    return examples


def format_examples_block(examples: Iterable[MemoryRecord]) -> str:
    lines: list[str] = []
    for ex in examples:
        lines.append("- CASE: " + (ex.input_text or "").strip())
        lines.append("  APPROVED_REPLY: " + (ex.output_text or "").strip())
    if not lines:
        return ""
    return (
        "Примеры ранее утверждённых ответов продавца (реально отправленные в Ozon). "
        "Используй стиль, структуру и тон, но не копируй дословно:\n" + "\n".join(lines)
    )


__all__ = ["fetch_examples", "format_examples_block"]
