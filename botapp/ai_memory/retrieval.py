from __future__ import annotations

import logging
from typing import Iterable, List

from .store import get_approved_memory_store
from .schemas import ApprovedAnswer

logger = logging.getLogger(__name__)


def fetch_examples(
    *, kind: str, input_text: str, product_id: str | int | None = None, limit: int = 5
) -> List[ApprovedAnswer]:
    store = get_approved_memory_store()
    examples = store.query_similar(kind=kind, input_text=input_text, product_id=product_id, limit=limit)
    if examples:
        hashes = ", ".join([e.hash or "" for e in examples])
        logger.info(
            "AI memory retrieval: kind=%s product_id=%s examples=%d hashes=[%s]",
            kind,
            product_id,
            len(examples),
            hashes,
        )
    return examples


def format_examples_block(examples: Iterable[ApprovedAnswer]) -> str:
    lines: list[str] = []
    for ex in examples:
        lines.append("- CASE: " + (ex.input_text or "").strip())
        lines.append("  APPROVED_REPLY: " + (getattr(ex, "answer_text", None) or "").strip())
    if not lines:
        return ""
    return (
        "Примеры ранее утверждённых ответов продавца (реально отправленные в Ozon). "
        "Используй стиль, структуру и тон, но не копируй дословно:\n" + "\n".join(lines)
    )


__all__ = ["fetch_examples", "format_examples_block"]
