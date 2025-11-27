from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from aiogram import Router
from aiogram.types import Message, CallbackQuery

# Роутер для раздела "Вопросы"
router = Router(name="questions")

# Внутреннее хранилище токенов вопросов:
# token -> произвольный объект (question, question_id и т.п.)
_question_token_storage: Dict[str, Any] = {}


def register_question_token(*args: Any, **kwargs: Any) -> str:
    """
    Регистрация токена для вопроса.

    Сделано максимально универсально:
    - принимает любые аргументы (*args, **kwargs), чтобы не падать
      при разных вариантах вызова из других модулей;
    - сохраняет первый позиционный аргумент (если он есть) как "данные вопроса";
    - если аргументов нет, просто регистрирует пустой объект.

    Возвращает сгенерированный токен-строку.
    """
    if args:
        payload: Any = args[0]
    else:
        # Если ничего не передали — храним просто заглушку,
        # чтобы код всё равно не падал.
        payload = {"info": "empty_question"}

    token: str = secrets.token_urlsafe(8)
    _question_token_storage[token] = payload
    return token


def find_question(token: str) -> Optional[Any]:
    """
    Поиск вопроса по токену.

    Предполагается, что где-то в коде:
    - сначала вызывают register_question_token(question),
    - токен кладут в callback_data,
    - затем по токену вытаскивают объект вопроса.

    Здесь мы просто возвращаем то, что сохранили в _question_token_storage
    (или None, если токен не найден).
    """
    return _question_token_storage.get(token)


# --- Ниже можно держать простые заглушки для страниц вопросов ---


async def get_questions_page(*args: Any, **kwargs: Any) -> str:
    """
    Заглушка для функции, которая должна формировать текст/страницу
    со списком вопросов.

    Сделана async, чтобы не ломать существующий код,
    если где-то её вызывают через `await get_questions_page(...)`.
    """
    # Здесь можно потом реализовать реальную логику,
    # сейчас — безопасная заглушка.
    return "Раздел «Вопросы покупателя» временно недоступен."


async def get_question_page(*args: Any, **kwargs: Any) -> str:
    """
    Заглушка для функции, которая должна формировать текст/страницу
    с конкретным вопросом.

    Также async для совместимости с возможными `await`-вызовами.
    """
    return "Карточка вопроса временно недоступна."


# --- Простейшие хендлеры-заглушки, чтобы раздел не выглядел "мертвым" ---


@router.message()
async def questions_fallback_handler(message: Message) -> None:
    """
    Фолбэк-хендлер для любых сообщений, которые попадут в этот роутер.
    В реальном коде сюда обычно вообще не доходят, но на всякий случай
    пусть отвечает вежливой заглушкой.
    """
    await message.answer(
        "Раздел «Вопросы покупателя» сейчас в разработке. "
        "Основные функции бота (заказы, финансы, отзывы и т.п.) продолжают работать."
    )


@router.callback_query()
async def questions_callback_fallback(callback: CallbackQuery) -> None:
    """
    Фолбэк-хендлер для callback-запросов, связанных с вопросами.
    Если вдруг прилетит callback с токеном вопроса, но пока нет
    нормальной логики обработки — ответим заглушкой.
    """
    await callback.answer("Раздел вопросов временно недоступен.", show_alert=True)


