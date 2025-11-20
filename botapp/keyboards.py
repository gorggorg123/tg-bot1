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
    period: Optional[str] = None
    index: Optional[int] = None


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Reply-клавиатура главного меню."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Финансы за сегодня")],
            [KeyboardButton(text="📦 FBO")],
            [KeyboardButton(text="⭐ Отзывы")],
            [KeyboardButton(text="👤 Аккаунт Ozon")],
        ],
        resize_keyboard=True,
    )


def back_home_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура с одной кнопкой возврата в главное меню."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏠 В меню",
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
                    text="🏠 В меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )


def reviews_periods_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню выбора периода отзывов."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сегодня",
                    callback_data=ReviewsCallbackData(action="period", period="today").pack(),
                ),
                InlineKeyboardButton(
                    text="7 дней",
                    callback_data=ReviewsCallbackData(action="period", period="week").pack(),
                ),
                InlineKeyboardButton(
                    text="Месяц",
                    callback_data=ReviewsCallbackData(action="period", period="month").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )


def reviews_navigation_keyboard(period: str, index: int, total: int) -> InlineKeyboardMarkup:
    """Клавиатура для просмотра отдельного отзыва."""

    has_prev = index > 0
    has_next = (index + 1) < total

    buttons = []
    nav_row = []
    nav_row.append(
        InlineKeyboardButton(
            text="⬅️ Предыдущий" if has_prev else "⏪ Начало",
            callback_data=ReviewsCallbackData(action="open", period=period, index=max(index - 1, 0)).pack(),
        )
    )
    nav_row.append(
        InlineKeyboardButton(
            text="Следующий ➡️" if has_next else "⏩ Конец",
            callback_data=ReviewsCallbackData(action="open", period=period, index=min(index + 1, total - 1)).pack(),
        )
    )
    buttons.append(nav_row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="✍️ Ответ ИИ",
                callback_data=ReviewsCallbackData(action="ai", period=period, index=index).pack(),
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="📅 Сменить период",
                callback_data=ReviewsCallbackData(action="change_period").pack(),
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🏠 В меню отзывов",
                callback_data=ReviewsCallbackData(action="back_menu").pack(),
            )
        ]
    )
    buttons.append(
        [
            InlineKeyboardButton(
                text="🔙 В меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def account_keyboard() -> InlineKeyboardMarkup:
    """Инлайн-меню для раздела аккаунта (пока только возврат в меню)."""

    return back_home_keyboard()


__all__ = [
    "MenuCallbackData",
    "ReviewsCallbackData",
    "main_menu_keyboard",
    "back_home_keyboard",
    "fbo_menu_keyboard",
    "reviews_periods_keyboard",
    "reviews_navigation_keyboard",
    "account_keyboard",
]
