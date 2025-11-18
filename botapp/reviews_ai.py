# botapp/reviews_ai.py
from __future__ import annotations

from typing import Dict


def draft_reply(review: Dict[str, str | int]) -> str:
    """
    Заглушка для AI-ответов на отзывы.

    Принимает словарь с полями отзыва и возвращает шаблонный черновик ответа,
    который позже можно заменить вызовом OpenAI API.
    """

    rating = review.get("rating") or review.get("grade")
    product = review.get("product_title") or review.get("product_name") or "товар"
    name = review.get("author") or "Покупатель"

    if rating and int(rating) >= 4:
        return (
            f"{name}, благодарим за высокую оценку! "
            f"Нам приятно, что {product} вам понравился."
        )

    return (
        f"{name}, спасибо за обратную связь. Нам важно, что вы поделились опытом по {product}. "
        "Мы уже передали информацию команде и постараемся исправить ситуацию."
    )

