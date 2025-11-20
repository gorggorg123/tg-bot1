"""AI helper for drafting review replies using OpenAI Chat Completions."""
from __future__ import annotations

import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    if not _OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=_OPENAI_API_KEY)
    return _client


async def generate_review_reply(
    review_text: str,
    product_name: str | None,
    rating: int | None,
    language: str = "ru",
) -> str:
    """Return a short, friendly draft reply to a customer review.

    If OpenAI rejects the request (e.g. no access to the default model), we log a
    warning and fall back to a simple canned reply instead of bubbling the error
    to Telegram users.
    """

    if not _OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY is missing; cannot generate AI reply")
        raise RuntimeError("OPENAI_API_KEY missing")

    client = _get_client()
    system_prompt = (
        "Ты сотрудник бренда ITOM. Пиши кратко, дружелюбно, человеческим языком,"
        " без канцелярита. Отвечай на русском."
    )
    user_parts = [f"Отзыв: {review_text.strip()}"[:2000]]
    if product_name:
        user_parts.append(f"Товар: {product_name}")
    if rating:
        user_parts.append(f"Оценка: {rating}★")

    message = "\n".join(user_parts)
    # Разрешаем задать модель через окружение; по умолчанию используем более
    # доступную gpt-4o-mini, чтобы избежать 403 model_not_found, как на Render.
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ],
            max_tokens=300,
            temperature=0.6,
        )
    except Exception as exc:
        logger.warning("OpenAI call failed (%s); using canned reply", exc)
        return "Спасибо за ваш отзыв! Мы уже передали его команде."  # fallback

    choice = resp.choices[0].message.content if resp.choices else None
    return choice.strip() if choice else "Спасибо за ваш отзыв!"


__all__ = ["generate_review_reply"]
