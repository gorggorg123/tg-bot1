from __future__ import annotations

import logging
from typing import Iterable

from botapp.ai_client import AIClientError, generate_chat_reply

logger = logging.getLogger(__name__)


def _split_messages_by_role(messages: Iterable[dict]) -> tuple[list[str], list[str]]:
    customer: list[str] = []
    seller: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = msg.get("text") or msg.get("message") or msg.get("content")
        if not text:
            continue

        role_block = msg.get("user") if isinstance(msg.get("user"), dict) else None
        author_block = msg.get("author") if isinstance(msg.get("author"), dict) else None
        role = (role_block.get("type") if role_block else None) or (
            author_block.get("role") if author_block else None
        )
        role_str = str(role or msg.get("from") or msg.get("direction") or "").lower()
        if "crm" in role_str or "support" in role_str:
            continue

        if any(key in role_str for key in ("seller", "operator", "store")):
            seller.append(str(text))
        else:
            customer.append(str(text))
    return customer, seller


async def build_chat_ai_context(
    chat_history: list[dict],
    product_name: str | None,
    extra_context: str | None = None,
) -> str:
    """Prepare compact textual context for chat AI prompts."""

    customer_msgs, seller_msgs = _split_messages_by_role(chat_history[-10:])
    parts: list[str] = []
    if product_name:
        parts.append(f"Товар: {product_name}")
    if extra_context:
        parts.append(extra_context.strip())
    if customer_msgs:
        parts.append("Последние сообщения клиента:\n" + "\n".join(customer_msgs[-5:]))
    if seller_msgs:
        parts.append("Последние ответы продавца:\n" + "\n".join(seller_msgs[-3:]))
    return "\n\n".join(parts)


async def suggest_chat_reply(
    chat_history: list[dict], product_name: str | None
) -> str | None:
    """Call AI client to generate draft reply based on chat history."""

    customer_msgs, seller_msgs = _split_messages_by_role(chat_history[-10:])
    try:
        return await generate_chat_reply(
            customer_messages=customer_msgs,
            seller_messages=seller_msgs,
            product_name=product_name,
        )
    except AIClientError as exc:
        logger.warning("AI client error while suggesting chat reply: %s", exc)
        return None
    except Exception:  # pragma: no cover - network/safety
        logger.exception("Unexpected error while generating chat reply")
        return None
