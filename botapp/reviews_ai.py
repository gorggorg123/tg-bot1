"""
Вспомогательные функции для AI-генерации ответов на отзывы Ozon.

Модуль предоставляет:
- build_review_reply_draft: формирование черновика ответа на отзыв
- draft_reply: совместимость со старым API
"""
# botapp/reviews_ai.py
from __future__ import annotations

import logging
import os
from typing import Dict

from botapp.ai_client import AIClientError, generate_review_reply

logger = logging.getLogger(__name__)


async def build_review_reply_draft(
    review_text: str,
    rating: int | None = None,
    *,
    product_name: str | None = None,
    sku: str | None = None,
    previous_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    """Сформировать черновик ответа на отзыв через AI.
    
    Args:
        review_text: Текст отзыва покупателя
        rating: Оценка (1-5)
        product_name: Название товара
        sku: Артикул товара (для контекста из памяти)
        previous_answer: Предыдущий черновик (если нужно переписать)
        user_prompt: Дополнительные пожелания к ответу
    
    Returns:
        Сгенерированный черновик ответа
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()

    if not api_key:
        # Без ключа возвращаем шаблонный ответ
        logger.warning("OPENAI_API_KEY не задан — возвращаем шаблон")
        base = "Спасибо, что поделились мнением."
        if rating and rating >= 4:
            base = "Спасибо за высокую оценку и доверие!"
        elif rating and rating <= 2:
            base = "Благодарим за обратную связь. Нам очень важно ваше мнение."
        return f"{base} Мы ценим ваши отзывы."

    try:
        return await generate_review_reply(
            review_text=review_text,
            product_name=product_name,
            sku=sku,
            rating=rating,
            previous_answer=previous_answer,
            user_prompt=user_prompt,
        )
    except AIClientError as exc:
        logger.warning("AI client error while generating review reply: %s", exc)
        # Fallback на шаблон при ошибке AI
        if rating and rating >= 4:
            return "Спасибо за высокую оценку! Мы рады, что товар вам понравился."
        return "Благодарим за отзыв! Мы ценим вашу обратную связь."
    except Exception:
        logger.exception("Unexpected error while generating review reply")
        return "Спасибо за отзыв! Мы учтём ваше мнение."


async def draft_reply(review: Dict[str, str | int | None]) -> str:
    """Совместимость со старыми вызовами — прокси к build_review_reply_draft.
    
    Args:
        review: Словарь с данными отзыва (text/comment, rating/grade, product_name, sku)
    
    Returns:
        Сгенерированный черновик ответа
    """
    text = (review.get("text") or review.get("comment") or "").strip()
    rating = review.get("rating") or review.get("grade")
    product_name = review.get("product_name")
    sku = review.get("sku") or review.get("product_id")
    
    try:
        rating_val: int | None = int(rating) if rating is not None else None
    except (ValueError, TypeError):
        rating_val = None

    return await build_review_reply_draft(
        review_text=text,
        rating=rating_val,
        product_name=str(product_name) if product_name else None,
        sku=str(sku) if sku else None,
    )


__all__ = [
    "build_review_reply_draft",
    "draft_reply",
]

