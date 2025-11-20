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
    """Callback для раздела отзывов.

    action: действие (page/toggle/menu)
    mode: режим отзывов (answered/unanswered)
    page: номер страницы списка
    """

    action: str
    mode: Optional[str] = None
    page: Optional[int] = None


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
                    text="📋 Показать неотвеченные",
                    callback_data=ReviewsCallbackData(action="toggle", mode="unanswered").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📋 Показать отвеченные",
                    callback_data=ReviewsCallbackData(action="toggle", mode="answered").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 В меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                ),
            ],
        ]
    )


def reviews_navigation_keyboard(mode: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Клавиатура для пагинации и переключения режима отзывов."""

    has_prev = page > 0
    has_next = (page + 1) < total_pages

    prev_btn = InlineKeyboardButton(
        text="⬅️ Назад" if has_prev else "⏮",
        callback_data=ReviewsCallbackData(action="page", mode=mode, page=max(page - 1, 0)).pack(),
    )
    next_btn = InlineKeyboardButton(
        text="Вперёд ➡️" if has_next else "⏭",
        callback_data=ReviewsCallbackData(action="page", mode=mode, page=min(page + 1, max(total_pages - 1, 0))).pack(),
    )

    toggle_target = "answered" if mode == "unanswered" else "unanswered"
    toggle_btn = InlineKeyboardButton(
        text="📋 Показать отвеченные" if toggle_target == "answered" else "📋 Показать неотвеченные",
        callback_data=ReviewsCallbackData(action="toggle", mode=toggle_target).pack(),
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [prev_btn, next_btn],
            [toggle_btn],
            [
                InlineKeyboardButton(
                    text="🏠 В меню", callback_data=MenuCallbackData(section="home", action="open").pack()
                )
            ],
        ]
    )


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
