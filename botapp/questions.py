# botapp/questions.py

"""
Утилиты для работы с вопросами покупателей.

Этот модуль специально сделан максимально независимым:
- не делает сетевых запросов;
- не использует pydantic/ozonapi, чтобы не ломать импорт;
- предоставляет три функции, которые импортируются в других файлах:
    * register_question_token(question, *args, **kwargs) -> str
    * find_question(token, *args, **kwargs) -> Any | None
    * format_question_card_text(question) -> str
"""

from __future__ import annotations

from secrets import token_urlsafe
from typing import Any, Dict, Optional


# Внутреннее хранилище "токен -> объект вопроса".
# Живёт только в памяти процесса (как и у отзывов).
_QUESTION_TOKENS: Dict[str, Any] = {}


def _extract_question_from_args(*args: Any, **kwargs: Any) -> Any:
    """
    Пытаемся вытащить объект вопроса из произвольных аргументов.

    Поддерживает несколько вариантов:
    - register_question_token(question)
    - register_question_token(question=...)
    - register_question_token(obj=...) / item=...
    - register_question_token(data=...)
    """
    if args:
        # Самый частый сценарий: первый позиционный аргумент — это вопрос
        return args[0]

    # Попробуем по ключам
    for key in ("question", "obj", "item", "data"):
        if key in kwargs:
            return kwargs[key]

    return None


def register_question_token(*args: Any, **kwargs: Any) -> str:
    """
    Регистрирует вопрос в локальном хранилище и возвращает
    ОПАКНЫЙ токен (строку), который можно класть в callback_data.

    Сигнатура специально максимально гибкая, чтобы не ловить TypeError
    при разных вариантах вызова.

    Примеры возможных вызовов:
        token = register_question_token(question)
        token = register_question_token(question=question)
        token = register_question_token(question, message_id)
    """
    question = _extract_question_from_args(*args, **kwargs)
    if question is None:
        # Лучше тихо сгенерировать токен "пустого" вопроса,
        # чем уронить всё приложение исключением.
        question = {}

    token = token_urlsafe(8)
    _QUESTION_TOKENS[token] = question
    return token


def find_question(*args: Any, **kwargs: Any) -> Optional[Any]:
    """
    Находит ранее сохранённый вопрос по токену.

    Поддерживает формы:
        find_question(token)
        find_question(token=...)
        find_question(question_token=...)
        find_question(id=...)

    Возвращает:
        - исходный объект вопроса, переданный в register_question_token;
        - или None, если токен не найден.
    """
    token: Optional[str] = None

    if args:
        # Часто вызывают просто find_question(token)
        token = str(args[0])
    else:
        for key in ("token", "question_token", "id"):
            if key in kwargs and kwargs[key] is not None:
                token = str(kwargs[key])
                break

    if not token:
        return None

    return _QUESTION_TOKENS.get(token)


def _q_get(question: Any, field: str, default: Any = "") -> Any:
    """
    Унифицированный доступ к полям вопроса:
    - поддерживает dict;
    - поддерживает pydantic / объекты с атрибутами.
    """
    if isinstance(question, dict):
        return question.get(field, default)

    # pydantic-модель или любой объект с атрибутами
    return getattr(question, field, default)


def format_question_card_text(question: Any) -> str:
    """
    Формирует текст карточки вопроса для отправки в Telegram.

    Пытается аккуратно вытащить информацию из разных возможных полей
    (на случай, если модель вопроса менялась).
    """

    # Возможные имена полей в разных моделях
    created_at = (
        _q_get(question, "created_at")
        or _q_get(question, "creation_time")
        or _q_get(question, "created")
        or ""
    )

    customer_name = (
        _q_get(question, "author_name")
        or _q_get(question, "authorName")
        or _q_get(question, "customer_name")
        or _q_get(question, "customerName")
        or ""
    )

    sku = (
        _q_get(question, "sku")
        or _q_get(question, "sku_id")
        or _q_get(question, "offer_id")
        or _q_get(question, "product_id")
        or ""
    )

    product_title = (
        _q_get(question, "product_name")
        or _q_get(question, "name")
        or _q_get(question, "title")
        or ""
    )

    text = (
        _q_get(question, "text")
        or _q_get(question, "question_text")
        or _q_get(question, "question")
        or ""
    )

    lines = []

    # Заголовок карточки
    if product_title or sku:
        header = "❓ Вопрос по товару"
        if product_title:
            header += f": {product_title}"
        if sku:
            header += f"\nSKU: {sku}"
        lines.append(header)
    else:
        lines.append("❓ Вопрос от покупателя")

    # Покупатель
    if customer_name:
        lines.append(f"👤 Покупатель: {customer_name}")

    # Дата
    if created_at:
        lines.append(f"🕒 Дата: {created_at}")

    # Текст вопроса
    if text:
        lines.append("")  # пустая строка-разделитель
        lines.append(text)

    return "\n".join(lines)

