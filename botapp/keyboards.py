"""Набор клавиатур и фабрик callback_data для навигации бота."""

from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


class MenuCallbackData(CallbackData, prefix="menu"):
    """Универсальный callback для внутренних меню.

    section: название раздела (reviews, fbo, account, home)
    action: действие внутри раздела (period/nav/summary/etc)
    extra: дополнительный параметр (период, индекс и т.д.)
    """

    section: str
    action: str
    extra: Optional[str] = None


class ReviewsCallbackData(CallbackData, prefix="reviews"):
    """Callback для раздела отзывов."""

    action: str
    category: Optional[str] = None
    index: Optional[int] = None
    review_id: Optional[str] = None


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Reply-клавиатура главного меню."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Финансы сегодня")],
            [KeyboardButton(text="📦 FBO за сегодня")],
            [KeyboardButton(text="⭐ Отзывы")],
            [KeyboardButton(text="⚙️ Аккаунт Ozon")],
        ],
        resize_keyboard=True,
    )


def back_home_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой возврата в главное меню."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ]
        ]
    )


def fbo_menu_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню раздела FBO."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Сводка",
                    callback_data=MenuCallbackData(section="fbo", action="summary").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Месяц",
                    callback_data=MenuCallbackData(section="fbo", action="month").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Фильтр",
                    callback_data=MenuCallbackData(section="fbo", action="filter").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )


def reviews_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить отзывы",
                    callback_data=ReviewsCallbackData(action="open_list", category="unanswered").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )


def reviews_navigation_keyboard(
    category: str, index: int, total: int, review_id: str | None
) -> InlineKeyboardMarkup:
    """Клавиатура для просмотра отдельного отзыва."""

    has_prev = index > 0
    has_next = (index + 1) < total

    nav_row = [
        InlineKeyboardButton(
            text="⬅️ Предыдущий" if has_prev else "⏮️ Начало",
            callback_data=ReviewsCallbackData(action="nav", category=category, index=max(index - 1, 0), review_id=review_id).pack(),
        ),
        InlineKeyboardButton(
            text="Следующий ➡️" if has_next else "⏭️ Конец",
            callback_data=ReviewsCallbackData(action="nav", category=category, index=min(index + 1, total - 1), review_id=review_id).pack(),
        ),
    ]

    switch_category = "answered" if category != "answered" else "unanswered"
    switch_label = "Показать отвеченные" if switch_category == "answered" else "Назад к неотвеченным"

    buttons = [
        nav_row,
        [
            InlineKeyboardButton(
                text="✏️ Ответить через ИИ",
                callback_data=ReviewsCallbackData(action="ai", category=category, index=index, review_id=review_id).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Пометить как отвеченный",
                callback_data=ReviewsCallbackData(action="mark", category=category, index=index, review_id=review_id).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text=switch_label,
                callback_data=ReviewsCallbackData(action="switch", category=switch_category, index=0).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def review_draft_keyboard(category: str, index: int, review_id: str | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Отправить как есть",
                    callback_data=ReviewsCallbackData(action="send", category=category, index=index, review_id=review_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="♻️ Сгенерировать ещё",
                    callback_data=ReviewsCallbackData(action="regen", category=category, index=index, review_id=review_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Отредактировать",
                    callback_data=ReviewsCallbackData(action="edit", category=category, index=index, review_id=review_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к отзыву",
                    callback_data=ReviewsCallbackData(action="nav", category=category, index=index, review_id=review_id).pack(),
                )
            ],
        ]
    )


def account_keyboard() -> InlineKeyboardMarkup:
    return back_home_keyboard()


__all__ = [
    "MenuCallbackData",
    "ReviewsCallbackData",
    "main_menu_keyboard",
    "back_home_keyboard",
    "fbo_menu_keyboard",
    "reviews_root_keyboard",
    "reviews_navigation_keyboard",
    "review_draft_keyboard",
    "account_keyboard",
]
