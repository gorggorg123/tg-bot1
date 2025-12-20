"""Reusable helpers for list headers and inline keyboards."""

from __future__ import annotations

from typing import Callable, Iterable, Sequence, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .listing import format_period_header

ListItem = Tuple[str, str, int]
CallbackBuilder = Callable[[str, str, int, str | None], str]


def build_list_header(title: str, pretty_period: str, page: int, total_pages: int) -> str:
    """Build a compact header for paginated lists."""

    return format_period_header(title, pretty_period, page, total_pages)


def build_items_keyboard(
    items: Sequence[ListItem] | Iterable[ListItem],
    *,
    category: str,
    page: int,
    open_action: str,
    build_callback_data: CallbackBuilder,
) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    for label, token, _idx in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=build_callback_data(open_action, category, page, token),
                )
            ]
        )
    return rows


def build_pager_controls(
    *,
    category: str,
    page: int,
    total_pages: int,
    build_callback_data: CallbackBuilder,
    refresh_action: str,
    menu_callback_data: str,
    page_action: str = "page",
    filter_action: str = "list",
) -> list[list[InlineKeyboardButton]]:
    """Build navigation, filters, refresh, and menu rows."""

    safe_total_pages = max(total_pages, 1)

    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="⏮️" if page > 0 else "◀️ Назад",
                callback_data=build_callback_data(page_action, category, max(page - 1, 0), None),
            ),
            InlineKeyboardButton(
                text=f"Стр. {page + 1}/{safe_total_pages}",
                callback_data=build_callback_data("noop", category, page, None),
            ),
            InlineKeyboardButton(
                text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
                callback_data=build_callback_data(
                    page_action,
                    category,
                    min(page + 1, max(total_pages - 1, 0)),
                    None,
                ),
            ),
        ]
    ]

    filter_row = [
        InlineKeyboardButton(
            text="Все", callback_data=build_callback_data(filter_action, "all", 0, None)
        ),
        InlineKeyboardButton(
            text="Без ответа",
            callback_data=build_callback_data(filter_action, "unanswered", 0, None),
        ),
        InlineKeyboardButton(
            text="С ответом",
            callback_data=build_callback_data(filter_action, "answered", 0, None),
        ),
    ]

    rows.append(filter_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=build_callback_data(refresh_action, category, page, None),
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ В главное меню", callback_data=menu_callback_data)])

    return rows


def build_list_keyboard(
    *,
    items: Sequence[ListItem] | Iterable[ListItem],
    category: str,
    page: int,
    total_pages: int,
    build_callback_data: CallbackBuilder,
    open_action: str,
    refresh_action: str,
    menu_callback_data: str,
    page_action: str = "page",
    filter_action: str = "list",
) -> InlineKeyboardMarkup:
    rows = build_items_keyboard(
        items,
        category=category,
        page=page,
        open_action=open_action,
        build_callback_data=build_callback_data,
    )
    rows.extend(
        build_pager_controls(
            category=category,
            page=page,
            total_pages=total_pages,
            build_callback_data=build_callback_data,
            refresh_action=refresh_action,
            menu_callback_data=menu_callback_data,
            page_action=page_action,
            filter_action=filter_action,
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "build_items_keyboard",
    "build_list_header",
    "build_list_keyboard",
    "build_pager_controls",
]
