# botapp/sections/cdek/keyboards.py
"""Клавиатуры для раздела CDEK."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.keyboards import MenuCallbackData


class CdekCallbackData(CallbackData, prefix="cdek", sep="|"):
    """Callback data для CDEK раздела."""
    action: str
    extra: str | None = None


def cdek_main_keyboard() -> InlineKeyboardMarkup:
    """Главная клавиатура раздела CDEK."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧠 Из переписки",
                    callback_data=CdekCallbackData(action="create").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Ручной ввод",
                    callback_data=CdekCallbackData(action="edit").pack(),
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


def cdek_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения данных отправки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=CdekCallbackData(action="confirm").pack(),
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data=CdekCallbackData(action="edit").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=CdekCallbackData(action="back").pack(),
                )
            ],
        ]
    )


__all__ = [
    "CdekCallbackData",
    "cdek_main_keyboard",
    "cdek_confirmation_keyboard",
]
