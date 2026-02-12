"""Набор общих клавиатур и фабрик callback_data для навигации бота."""
from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class MenuCallbackData(CallbackData, prefix="menu"):
    """Универсальный callback для внутренних меню."""

    section: str
    action: str
    extra: Optional[str] = None


# ---------------------------------------------------------------------------
# Главное меню
# ---------------------------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню: современный дизайн с группировкой."""

    kb: list[list[InlineKeyboardButton]] = [
        # ═══ Коммуникации ═══
        [
            InlineKeyboardButton(
                text="📝 Отзывы",
                callback_data=MenuCallbackData(section="reviews", action="open", extra="").pack(),
            ),
            InlineKeyboardButton(
                text="❓ Вопросы",
                callback_data=MenuCallbackData(section="questions", action="open", extra="").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="💬 Чаты",
                callback_data=MenuCallbackData(section="chats", action="open", extra="").pack(),
            ),
        ],
        # ═══ Операции ═══
        [
            InlineKeyboardButton(
                text="📦 Отправки",
                callback_data=MenuCallbackData(section="fbo", action="summary", extra="").pack(),
            ),
            InlineKeyboardButton(
                text="💵 Финансы",
                callback_data=MenuCallbackData(section="fin_today", action="open", extra="").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🚚 СДЭК",
                callback_data=MenuCallbackData(section="cdek", action="open", extra="").pack(),
            ),
        ],
        # ═══ Настройки ═══
        [
            InlineKeyboardButton(
                text="🔔 Уведомления",
                callback_data=MenuCallbackData(section="notifications", action="open", extra="").pack(),
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


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


def pick_plan_keyboard(posting_number: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Собрать заказ",
                    callback_data=MenuCallbackData(section="fbo", action="pick", extra=posting_number).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=MenuCallbackData(section="fbo", action="summary").pack(),
                )
            ],
        ]
    )


# ---------------------------------------------------------------------------
# ФБО / Финансы
# ---------------------------------------------------------------------------


def fbo_menu_keyboard(mode: str = "all") -> InlineKeyboardMarkup:
    """Клавиатура раздела отправок FBO/FBS.
    
    Args:
        mode: Текущий режим отображения ("all", "fbo", "fbs")
    """
    # Кнопки переключения режима
    mode_buttons = []
    if mode != "all":
        mode_buttons.append(
            InlineKeyboardButton(
                text="📊 Все (FBO+FBS)",
                callback_data=MenuCallbackData(section="fbo", action="mode_all").pack(),
            )
        )
    if mode != "fbo":
        mode_buttons.append(
            InlineKeyboardButton(
                text="📦 Только FBO",
                callback_data=MenuCallbackData(section="fbo", action="mode_fbo").pack(),
            )
        )
    if mode != "fbs":
        mode_buttons.append(
            InlineKeyboardButton(
                text="🏭 Только FBS",
                callback_data=MenuCallbackData(section="fbo", action="mode_fbs").pack(),
            )
        )
    
    keyboard = []
    
    # Кнопки режима (по 2 в ряд)
    if mode_buttons:
        for i in range(0, len(mode_buttons), 2):
            keyboard.append(mode_buttons[i:i+2])
    
    # Кнопки периода
    keyboard.append([
        InlineKeyboardButton(
            text="🗓 Сегодня",
            callback_data=MenuCallbackData(section="fbo", action="summary").pack(),
        ),
        InlineKeyboardButton(
            text="📅 Месяц",
            callback_data=MenuCallbackData(section="fbo", action="month").pack(),
        ),
    ])
    
    # Кнопка обновления
    keyboard.append([
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=MenuCallbackData(section="fbo", action="refresh").pack(),
        ),
    ])
    
    # Возврат в меню
    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data=MenuCallbackData(section="home", action="open").pack(),
        ),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def finance_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧾 Сегодня",
                    callback_data=MenuCallbackData(section="fin_today", action="open").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗓 Месяц",
                    callback_data=MenuCallbackData(section="fin_today", action="month").pack(),
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


__all__ = [
    "MenuCallbackData",
    "back_home_keyboard",
    "fbo_menu_keyboard",
    "finance_menu_keyboard",
    "main_menu_keyboard",
    "pick_plan_keyboard",
]
