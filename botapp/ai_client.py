"""AI helper for drafting review replies using OpenAI Chat Completions."""
from __future__ import annotations

import logging
import os
from typing import Optional

from openai import AsyncOpenAI
from openai import APIStatusError, NotFoundError, PermissionDeniedError

logger = logging.getLogger(__name__)

_client: Optional[AsyncOpenAI] = None


class AIClientError(RuntimeError):
    """Пользовательская ошибка работы с OpenAI."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


def _get_client() -> AsyncOpenAI:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise AIClientError("OPENAI_API_KEY is not set")

    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=api_key)
    return _client


async def _call_openai(system_prompt: str, user_message: str, *, max_tokens: int = 300) -> str | None:
    client = _get_client()
    model = "gpt-4o-mini"
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
            temperature=0.6,
        )
    except (PermissionDeniedError, NotFoundError) as exc:  # 403/model not found
        logger.warning("OpenAI model error: %s", exc)
        return None
    except APIStatusError as exc:
        logger.warning("OpenAI API status error: %s", exc)
        return None
    except Exception as exc:  # pragma: no cover - защитный слой
        logger.exception("OpenAI unexpected error: %s", exc)
        return None

    choice = resp.choices[0].message.content if resp.choices else None
    return choice.strip() if choice else "Спасибо за ваш отзыв!"


async def generate_review_reply(
    *,
    review_text: str,
    product_name: str | None,
    rating: int | None,
    user_prompt: str | None = None,
    previous_answer: str | None = None,
    language: str = "ru",
) -> str | None:
    """Return a short, friendly draft reply to a customer review."""

    system_prompt = (
        "Ты — продавец на Ozon. Пиши кратко, вежливо, по-человечески,"
        " без канцелярита и токсичности. Отвечай на русском."
    )

    user_parts = [f"Отзыв: {review_text.strip()}"[:2000]]
    if product_name:
        user_parts.append(f"Товар: {product_name}")
    if rating:
        user_parts.append(f"Оценка: {rating}★")
    if previous_answer:
        user_parts.append(f"Предыдущий вариант ответа: {previous_answer[:1000]}")
    if user_prompt:
        user_parts.append(f"Пожелание к ответу: {user_prompt.strip()[:800]}")

    message = "\n".join(user_parts)
    return await _call_openai(system_prompt, message)


async def generate_answer_for_question(
    question_text: str,
    product_name: str | None = None,
    existing_answer: str | None = None,
    user_prompt: str | None = None,
) -> str:
    """Generate short polite answer for customer question."""

    system_prompt = (
        "Ты отвечаешь покупателю на Ozon от имени бренда ИТОМ. Пиши дружелюбно,"
        " по делу и кратко. Избегай воды и лишних деталей."
    )
    parts = [f"Вопрос: {question_text.strip()}"[:2000]]
    if product_name:
        parts.append(f"Товар: {product_name}")
    if existing_answer:
        parts.append(f"Предыдущий вариант ответа: {existing_answer[:1000]}")
    if user_prompt:
        parts.append(f"Пожелания: {user_prompt.strip()[:800]}")

    user_message = "\n".join(parts)
    draft = await _call_openai(system_prompt, user_message)
    return draft or "Спасибо за вопрос!"


__all__ = ["generate_review_reply", "generate_answer_for_question", "AIClientError"]
