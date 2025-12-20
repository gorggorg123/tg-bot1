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


class WarehouseCallbackData(CallbackData, prefix="warehouse"):
    action: str
    product_id: Optional[int] = None
    page: Optional[int] = None


# ---------------------------------------------------------------------------
# Главное меню
# ---------------------------------------------------------------------------


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню: держим основные разделы на виду."""

    kb: list[list[InlineKeyboardButton]] = [
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
        [
            InlineKeyboardButton(
                text="📦 ФБО",
                callback_data=MenuCallbackData(section="fbo", action="summary", extra="").pack(),
            ),
            InlineKeyboardButton(
                text="💰 Финансы",
                callback_data=MenuCallbackData(section="fin_today", action="open", extra="").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🏭 Склад",
                callback_data=MenuCallbackData(section="warehouse", action="open", extra="").pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Настройки",
                callback_data=MenuCallbackData(section="settings", action="open", extra="").pack(),
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


# ---------------------------------------------------------------------------
# Склад (warehouse)
# ---------------------------------------------------------------------------


def warehouse_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📥 Приёмка",
                    callback_data=WarehouseCallbackData(action="receive").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Отбор под заказ",
                    callback_data=WarehouseCallbackData(action="pick").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Инвентаризация",
                    callback_data=WarehouseCallbackData(action="inventory").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚠️ Риск остатков",
                    callback_data=WarehouseCallbackData(action="risk").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💡 Спросить ИИ",
                    callback_data=WarehouseCallbackData(action="ask_ai").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ В меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                ),
            ],
        ]
    )


def warehouse_receive_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📂 Выбрать из списка",
                    callback_data=WarehouseCallbackData(action="receive_list", page=0).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Найти по названию",
                    callback_data=WarehouseCallbackData(action="receive_search_name").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Найти по штрихкоду",
                    callback_data=WarehouseCallbackData(action="receive_search_barcode").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅ В меню",
                    callback_data=MenuCallbackData(section="warehouse", action="open").pack(),
                )
            ],
        ]
    )


def warehouse_catalog_keyboard(
    options: list[tuple[str, str]], page: int = 0, page_size: int = 10
) -> InlineKeyboardMarkup:
    start = max(0, int(page)) * page_size
    end = start + page_size
    page_items = options[start:end]

    buttons = [
        [InlineKeyboardButton(text=label, callback_data=WarehouseCallbackData(action="receive_pick", product_id=int(pid)).pack())]
        for label, pid in page_items
    ]

    total_pages = (len(options) + page_size - 1) // page_size
    nav_row = [
        InlineKeyboardButton(
            text="⏮️" if page > 0 else "◀️ Назад",
            callback_data=WarehouseCallbackData(action="receive_list", page=max(page - 1, 0)).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{max(total_pages,1)}",
            callback_data=WarehouseCallbackData(action="noop").pack(),
        ),
        InlineKeyboardButton(
            text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
            callback_data=WarehouseCallbackData(action="receive_list", page=min(page + 1, max(total_pages - 1, 0))).pack(),
        ),
    ]

    buttons.append(nav_row)
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅ В меню",
                callback_data=MenuCallbackData(section="warehouse", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def warehouse_results_keyboard(options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=WarehouseCallbackData(action="receive_pick", product_id=int(pid)).pack(),
                )
            ]
            for label, pid in options
        ]
    )


def warehouse_labels_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да", callback_data=WarehouseCallbackData(action="print_labels_yes").pack()),
                InlineKeyboardButton(text="Нет", callback_data=WarehouseCallbackData(action="print_labels_no").pack()),
            ]
        ]
    )


def warehouse_ai_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить", callback_data=WarehouseCallbackData(action="ai_send").pack()),
                InlineKeyboardButton(text="✏️ Подредактировать", callback_data=WarehouseCallbackData(action="ai_edit").pack()),
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


def fbo_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📦 Отбор под заказ",
                    callback_data=MenuCallbackData(section="fbo", action="pick_menu").pack(),
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
    "WarehouseCallbackData",
    "back_home_keyboard",
    "fbo_menu_keyboard",
    "finance_menu_keyboard",
    "main_menu_keyboard",
    "pick_plan_keyboard",
    "warehouse_ai_confirmation_keyboard",
    "warehouse_catalog_keyboard",
    "warehouse_labels_keyboard",
    "warehouse_menu_keyboard",
    "warehouse_receive_keyboard",
    "warehouse_results_keyboard",
]
