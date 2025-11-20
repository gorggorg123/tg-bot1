# botapp/reviews_ai.py
from __future__ import annotations

import os
from typing import Dict


async def build_review_reply_draft(
    review_text: str, rating: int | None, style: str = "дружелюбный"
) -> str:
    """Сформировать черновик ответа (заглушка, место для OpenAI)."""

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    polite_prefix = "Благодарим за обратную связь!"

    if not api_key:
        # Без ключа возвращаем предсказуемый шаблон, чтобы бот не падал.
        base = "Спасибо, что поделились мнением."
        if rating and rating >= 4:
            base = "Спасибо за высокую оценку и доверие!"
        return f"{base} Мы ценим ваши отзывы. (Режим {style}, AI не настроен)"

    # TODO: здесь будет обращение к OpenAI API с промптом, когда появится ключ/квоты
    base = "Ваш отзыв принят, мы готовим персональный ответ." if not review_text else review_text[:120]
    return (
        f"{polite_prefix} Мы уже анализируем отзыв «{base}». "
        "(Ответ сгенерирован в демо-режиме, позже подключим OpenAI)"
    )


async def draft_reply(review: Dict[str, str | int]) -> str:
    """Совместимость со старыми вызовами — прокси к build_review_reply_draft."""

    text = (review.get("text") or review.get("comment") or "").strip()
    rating = review.get("rating") or review.get("grade")
    try:
        rating_val: int | None = int(rating) if rating is not None else None
    except Exception:
        rating_val = None

    return await build_review_reply_draft(text, rating_val)

