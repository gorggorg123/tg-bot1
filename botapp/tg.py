# botapp/tg.py

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


def main_menu_kb() -> InlineKeyboardMarkup:
    """
    Главное меню (ИНЛАЙН).
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏦 Финансы за сегодня",
                    callback_data="fin_today",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Заказы за сегодня",
                    callback_data="orders_today",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Аккаунт Ozon",
                    callback_data="account_info",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Полная аналитика",
                    callback_data="full_analytics",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 FBO",
                    callback_data="fbo_menu",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Отзывы",
                    callback_data="reviews",
                )
            ],
        ]
    )
