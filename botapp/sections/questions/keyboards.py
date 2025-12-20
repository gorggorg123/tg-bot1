from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.api.ozon_client import has_write_credentials
from botapp.keyboards import MenuCallbackData
from botapp.sections.questions.logic import register_question_token


class QuestionsCallbackData(CallbackData, prefix="questions"):
    action: str
    category: Optional[str] = None
    token: Optional[str] = None
    page: Optional[int] = None


def question_card_keyboard(
    *,
    category: str,
    page: int,
    token: str | None = None,
    can_send: bool = True,
    has_answer: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✉️ Ответ через ИИ",
                callback_data=QuestionsCallbackData(
                    action="ai",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Ввести ответ вручную",
                callback_data=QuestionsCallbackData(
                    action="manual",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="🔁 Пересобрать по моему промту",
                callback_data=QuestionsCallbackData(
                    action="reprompt",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ],
    ]

    rows.append(
        [
            InlineKeyboardButton(
                text="🗑 Очистить черновик",
                callback_data=QuestionsCallbackData(
                    action="clear_draft",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ]
    )

    if can_send and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Отправить на Ozon",
                    callback_data=QuestionsCallbackData(
                        action="send",
                        category=category,
                        page=page,
                        token=token,
                    ).pack(),
                )
            ]
        )

    if has_answer and has_write_credentials():
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Удалить ответ",
                    callback_data=QuestionsCallbackData(
                        action="delete",
                        category=category,
                        page=page,
                        token=token,
                    ).pack(),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="⬅️ Назад к списку",
                    callback_data=QuestionsCallbackData(
                        action="page",
                        category=category,
                        page=page,
                    ).pack(),
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def questions_list_keyboard(
    *,
    user_id: int,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for label, _unused_question_id, idx in items:
        token = register_question_token(user_id=user_id, category=category, index=idx)
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=QuestionsCallbackData(
                        action="open",
                        category=category,
                        token=token,
                        page=page,
                    ).pack(),
                )
            ]
        )

    filter_row = [
        InlineKeyboardButton(
            text="Все",
            callback_data=QuestionsCallbackData(action="list", category="all", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="Без ответа",
            callback_data=QuestionsCallbackData(action="list", category="unanswered", page=0).pack(),
        ),
        InlineKeyboardButton(
            text="С ответом",
            callback_data=QuestionsCallbackData(action="list", category="answered", page=0).pack(),
        ),
    ]

    safe_total_pages = max(total_pages, 1)
    nav_row = [
        InlineKeyboardButton(
            text="⏮️" if page > 0 else "◀️ Назад",
            callback_data=QuestionsCallbackData(
                action="page",
                category=category,
                page=max(page - 1, 0),
            ).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{safe_total_pages}",
            callback_data=QuestionsCallbackData(action="noop", category=category, page=page).pack(),
        ),
        InlineKeyboardButton(
            text="Вперёд ▶️" if page + 1 < total_pages else "⏭️",
            callback_data=QuestionsCallbackData(
                action="page",
                category=category,
                page=min(page + 1, max(total_pages - 1, 0)),
            ).pack(),
        ),
    ]

    rows.append(filter_row)
    rows.append(nav_row)
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=QuestionsCallbackData(action="refresh", category=category, page=page).pack(),
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В главное меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


__all__ = [
    "QuestionsCallbackData",
    "question_card_keyboard",
    "questions_list_keyboard",
]
