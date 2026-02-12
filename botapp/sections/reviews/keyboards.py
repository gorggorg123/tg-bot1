from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.api.ozon_client import has_write_credentials
from botapp.keyboards import MenuCallbackData
from botapp.ui import build_list_keyboard


class ReviewsCallbackData(CallbackData, prefix="reviews"):
    action: str
    category: Optional[str] = None
    token: Optional[str] = None
    page: Optional[int] = None
    index: Optional[int] = None
    review_id: Optional[str] = None


def reviews_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить отзывы",
                    callback_data=ReviewsCallbackData(
                        action="list",
                        category="unanswered",
                        page=0,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В главное меню",
                    callback_data=MenuCallbackData(
                        section="home",
                        action="open",
                    ).pack(),
                )
            ],
        ]
    )


def reviews_navigation_keyboard(
    category: str, index: int, total: int, review_id: str | None
) -> InlineKeyboardMarkup:
    page = index
    total_pages = total
    safe_total_pages = max(total_pages, 1)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏮️" if page > 0 else "◀️ Назад",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        page=max(page - 1, 0),
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"Стр. {page + 1}/{safe_total_pages}",
                    callback_data=ReviewsCallbackData(
                        action="noop", category=category, page=page
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
                    callback_data=ReviewsCallbackData(
                        action="list_page",
                        category=category,
                        page=min(page + 1, max(total_pages - 1, 0)),
                    ).pack(),
                ),
            ],
        ]
    )


def review_card_keyboard(
    *,
    category: str,
    index: int,
    review_id: str | None,
    token: str | None = None,
    page: int = 0,
    can_send: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    # ═══ Создание ответа ═══
    rows.append([
        InlineKeyboardButton(
            text="🤖 ИИ-ответ",
            callback_data=ReviewsCallbackData(
                action="ai",
                category=category,
                index=index,
                token=token,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="✏️ Вручную",
            callback_data=ReviewsCallbackData(
                action="manual",
                category=category,
                index=index,
                token=token,
                page=page,
            ).pack(),
        )
    ])
    
    # ═══ Редактирование ═══
    rows.append([
        InlineKeyboardButton(
            text="🔄 Пересобрать",
            callback_data=ReviewsCallbackData(
                action="reprompt",
                category=category,
                index=index,
                token=token,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 Очистить",
            callback_data=ReviewsCallbackData(
                action="clear",
                category=category,
                index=index,
                token=token,
                page=page,
            ).pack(),
        )
    ])

    # ═══ Отправка (акцентная кнопка) ═══
    if can_send and has_write_credentials():
        rows.append([
            InlineKeyboardButton(
                text="📤 Отправить на Ozon",
                callback_data=ReviewsCallbackData(
                    action="send",
                    category=category,
                    index=index,
                    token=token,
                    page=page,
                ).pack(),
            )
        ])

    # ═══ Навигация ═══
    rows.append([
        InlineKeyboardButton(
            text="↩️ К списку",
            callback_data=ReviewsCallbackData(
                action="list",
                category=category,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 В меню",
            callback_data=MenuCallbackData(section="home", action="open").pack(),
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def reviews_list_keyboard(
    *,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    def _build_cb(action: str, cat: str, target_page: int, token: str | None) -> str:
        return ReviewsCallbackData(
            action=action,
            category=cat,
            token=token,
            page=target_page,
        ).pack()

    return build_list_keyboard(
        items=items,
        category=category,
        page=page,
        total_pages=total_pages,
        build_callback_data=_build_cb,
        open_action="open",
        refresh_action="refresh",
        menu_callback_data=MenuCallbackData(section="home", action="open").pack(),
    )


def review_draft_keyboard(
    category: str,
    index: int,
    review_id: str | None,
    token: str | None = None,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Отправить как есть",
                    callback_data=ReviewsCallbackData(
                        action="send",
                        category=category,
                        index=index,
                        token=token,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Подредактировать", callback_data="edit_review"
                )
            ],
        ]
    )


__all__ = [
    "ReviewsCallbackData",
    "review_card_keyboard",
    "review_draft_keyboard",
    "reviews_list_keyboard",
    "reviews_navigation_keyboard",
    "reviews_root_keyboard",
]
