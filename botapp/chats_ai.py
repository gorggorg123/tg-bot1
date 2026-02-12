"""
Вспомогательные функции для AI-генерации ответов в чатах Ozon.

Модуль предоставляет:
- build_chat_ai_context: формирование текстового контекста из истории чата
- suggest_chat_reply: генерация черновика ответа через AI
- format_chat_history: форматирование истории для промпта
"""
from __future__ import annotations

import logging
from typing import Iterable

from botapp.ai_client import AIClientError, generate_chat_reply

logger = logging.getLogger(__name__)


def _split_messages_by_role(messages: Iterable[dict]) -> tuple[list[str], list[str]]:
    """Разделяет сообщения на клиентские и продавца."""
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


def format_chat_history(
    customer_msgs: list[str],
    seller_msgs: list[str],
    product_name: str | None = None,
) -> str:
    """Форматирует историю чата в текст для AI промпта.
    
    Формат:
    === ЧАТ С ПОКУПАТЕЛЕМ НА OZON ===
    BUYER: сообщение покупателя
    SELLER: ответ продавца
    
    Явно указывает, что это чат, а не отзыв или вопрос под товаром.
    """
    lines: list[str] = []
    
    # Явное указание контекста
    lines.append("=== ЧАТ С ПОКУПАТЕЛЕМ НА OZON ===")
    lines.append("BUYER — сообщения покупателя, SELLER — ответы продавца в чате.")
    lines.append("")
    
    if product_name:
        lines.append(f"Товар: {product_name}")
        lines.append("")
    
    # Чередуем сообщения (упрощённо - последние сообщения)
    max_msgs = max(len(customer_msgs), len(seller_msgs))
    for i in range(max_msgs):
        if i < len(customer_msgs):
            lines.append(f"BUYER: {customer_msgs[i]}")
        if i < len(seller_msgs):
            lines.append(f"SELLER: {seller_msgs[i]}")
    
    return "\n".join(lines)


async def build_chat_ai_context(
    chat_history: list[dict],
    product_name: str | None = None,
    extra_context: str | None = None,
) -> str:
    """Формирует текстовый контекст для AI промптов из истории чата.
    
    Args:
        chat_history: Список сообщений чата (словари с полями text, user, author и т.д.)
        product_name: Название товара (опционально)
        extra_context: Дополнительный контекст/инструкции (опционально)
    
    Returns:
        Форматированный текст для передачи в AI
    """
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
    chat_history: list[dict],
    product_name: str | None = None,
    *,
    user_prompt: str | None = None,
) -> str | None:
    """Генерирует черновик ответа на основе истории чата.
    
    Args:
        chat_history: Список сообщений чата
        product_name: Название товара (для контекста)
        user_prompt: Дополнительные пожелания пользователя к ответу
    
    Returns:
        Сгенерированный черновик ответа или None при ошибке
    """
    customer_msgs, seller_msgs = _split_messages_by_role(chat_history[-10:])
    
    # Формируем текстовый контекст для AI
    messages_text = format_chat_history(
        customer_msgs=customer_msgs[-5:],
        seller_msgs=seller_msgs[-3:],
        product_name=product_name,
    )
    
    if not messages_text.strip():
        messages_text = "История пуста. Ответь вежливо и уточни детали заказа при необходимости."
    
    try:
        return await generate_chat_reply(
            messages_text=messages_text,
            user_prompt=user_prompt,
        )
    except AIClientError as exc:
        logger.warning("AI client error while suggesting chat reply: %s", exc)
        return None
    except Exception:  # pragma: no cover - network/safety
        logger.exception("Unexpected error while generating chat reply")
        return None


__all__ = [
    "build_chat_ai_context",
    "suggest_chat_reply",
    "format_chat_history",
]
