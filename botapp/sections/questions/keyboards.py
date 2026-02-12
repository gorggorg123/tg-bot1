from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.api.ozon_client import has_write_credentials
from botapp.keyboards import MenuCallbackData
from botapp.ui import build_list_keyboard
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
    rows: list[list[InlineKeyboardButton]] = []

    # ═══ Создание ответа ═══
    rows.append([
        InlineKeyboardButton(
            text="🤖 ИИ-ответ",
            callback_data=QuestionsCallbackData(
                action="ai",
                category=category,
                page=page,
                token=token,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="✏️ Вручную",
            callback_data=QuestionsCallbackData(
                action="manual",
                category=category,
                page=page,
                token=token,
            ).pack(),
        )
    ])
    
    # ═══ Редактирование ═══
    rows.append([
        InlineKeyboardButton(
            text="🔄 Пересобрать",
            callback_data=QuestionsCallbackData(
                action="reprompt",
                category=category,
                page=page,
                token=token,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🗑 Очистить",
            callback_data=QuestionsCallbackData(
                action="clear_draft",
                category=category,
                page=page,
                token=token,
            ).pack(),
        )
    ])

    # ═══ Отправка (акцентная кнопка) ═══
    if can_send and has_write_credentials():
        rows.append([
            InlineKeyboardButton(
                text="📤 Отправить на Ozon",
                callback_data=QuestionsCallbackData(
                    action="send",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ])

    # ═══ Удаление (опасное действие) ═══
    if has_answer and has_write_credentials():
        rows.append([
            InlineKeyboardButton(
                text="⚠️ Удалить ответ",
                callback_data=QuestionsCallbackData(
                    action="delete",
                    category=category,
                    page=page,
                    token=token,
                ).pack(),
            )
        ])

    # ═══ Навигация ═══
    rows.append([
        InlineKeyboardButton(
            text="↩️ К списку",
            callback_data=QuestionsCallbackData(
                action="list",
                category=category,
                page=page,
            ).pack(),
        ),
        InlineKeyboardButton(
            text="🏠 В меню",
            callback_data=MenuCallbackData(section="home", action="open").pack(),
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def questions_list_keyboard(
    *,
    user_id: int,
    category: str,
    page: int,
    total_pages: int,
    items: list[tuple[str, str, int]],
) -> InlineKeyboardMarkup:
    def _build_cb(action: str, cat: str, target_page: int, token: str | None) -> str:
        return QuestionsCallbackData(
            action=action,
            category=cat,
            token=token,
            page=target_page,
        ).pack()

    with_tokens: list[tuple[str, str, int]] = []
    for label, _unused_question_id, idx in items:
        token = register_question_token(user_id=user_id, category=category, index=idx)
        with_tokens.append((label, token, idx))

    return build_list_keyboard(
        items=with_tokens,
        category=category,
        page=page,
        total_pages=total_pages,
        build_callback_data=_build_cb,
        open_action="open",
        refresh_action="refresh",
        menu_callback_data=MenuCallbackData(section="home", action="open").pack(),
    )


__all__ = [
    "QuestionsCallbackData",
    "question_card_keyboard",
    "questions_list_keyboard",
]
