from __future__ import annotations

from typing import Optional

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from botapp.keyboards import MenuCallbackData


class ChatsCallbackData(CallbackData, prefix="chats"):
    action: str
    chat_id: Optional[str] = None
    page: Optional[int] = None
    token: Optional[str] = None


ChatCallbackData = ChatsCallbackData


def chats_list_keyboard(
    *,
    items: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in items:
        token = item.get("token") or item.get("chat_id")
        caption = item.get("title") or item.get("caption") or "(чат)"
        if not token:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=caption,
                callback_data=ChatsCallbackData(action="open", token=token, chat_id=token).pack(),
            )
        ]
    )

    safe_total = max(total_pages, 1)
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️" if page > 0 else "⏮️",
                callback_data=ChatsCallbackData(action="page", page=max(page - 1, 0)).pack(),
            ),
            InlineKeyboardButton(
                text=f"Стр. {page + 1}/{safe_total}",
                callback_data=ChatsCallbackData(action="noop", page=page).pack(),
            ),
            InlineKeyboardButton(
                text="➡️" if page + 1 < total_pages else "⏭️",
                callback_data=ChatsCallbackData(
                    action="page", page=min(page + 1, max(total_pages - 1, 0))
                ).pack(),
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ В меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_header_keyboard(token: str, page: int | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=ChatsCallbackData(action="refresh_thread", token=token).pack(),
                ),
                InlineKeyboardButton(
                    text="⬅️ Еще раньше",
                    callback_data=ChatsCallbackData(action="older", token=token).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Сгенерировать ответ",
                    callback_data=ChatsCallbackData(action="ai", token=token).pack(),
                ),
                InlineKeyboardButton(
                    text="✏️ Править вручную",
                    callback_data=ChatsCallbackData(action="edit_ai", token=token).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🧹 Очистить",
                    callback_data=ChatsCallbackData(action="clear", token=token).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку чатов",
                    callback_data=ChatsCallbackData(action="list", token=token, page=page).pack(),
                ),
                InlineKeyboardButton(
                    text="⬅️ В меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
                )
            ],
        ]
    )


def chat_actions_keyboard(
    chat_id: str,
    *,
    attachments_total: int = 0,
    photo_count: int = 0,
    file_count: int = 0,
    oversized: bool = False,
    attachment_tokens: list[tuple[str, str, str | None]] | None = None,
    has_draft: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if attachments_total:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"📷 Фото ({photo_count})"
                        if oversized and photo_count
                        else f"📎 Вложения ({attachments_total})"
                    ),
                    callback_data=ChatsCallbackData(
                        action="media_photos" if oversized and photo_count else "media_all",
                        chat_id=chat_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"📄 Файлы ({file_count})",
                    callback_data=ChatsCallbackData(action="media_files", chat_id=chat_id).pack(),
                ),
            ]
        )
        if oversized:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⬇️ Скачать всё",
                        callback_data=ChatsCallbackData(action="media_all", chat_id=chat_id).pack(),
                    )
                ]
            )

    if attachment_tokens:
        for token, label, _kind in attachment_tokens:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=label,
                        callback_data=ChatsCallbackData(
                            action="file",
                            chat_id=chat_id,
                            token=token,
                        ).pack(),
                    )
                ]
            )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✏️ Ввести вручную",
                    callback_data=ChatsCallbackData(action="manual", chat_id=chat_id).pack(),
                ),
                InlineKeyboardButton(
                    text="🤖 Ответ ИИ",
                    callback_data=ChatsCallbackData(action="ai", chat_id=chat_id).pack(),
                ),
            ]
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад к списку",
                callback_data=ChatsCallbackData(action="exit", chat_id=chat_id).pack(),
            )
        ]
    )

    if has_draft:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧹 Очистить черновик",
                    callback_data=ChatsCallbackData(action="clear", chat_id=chat_id).pack(),
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_ai_confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return chat_draft_keyboard(token)


def chat_ai_draft_keyboard(token: str) -> InlineKeyboardMarkup:
    return chat_draft_keyboard(token)


def chat_draft_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Отправить в Ozon",
                    callback_data=ChatsCallbackData(action="send_ai", token=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔁 Перегенерировать",
                    callback_data=ChatsCallbackData(action="ai", token=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧩 Пересобрать по моему промту",
                    callback_data=ChatsCallbackData(action="ai_my_prompt", token=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✍️ Мой промт",
                    callback_data=ChatsCallbackData(action="set_my_prompt", token=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Править вручную",
                    callback_data=ChatsCallbackData(action="edit_ai", token=chat_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=ChatsCallbackData(action="refresh_thread", token=chat_id).pack(),
                )
            ],
        ]
    )


__all__ = [
    "ChatCallbackData",
    "ChatsCallbackData",
    "chat_actions_keyboard",
    "chat_ai_confirm_keyboard",
    "chat_ai_draft_keyboard",
    "chat_header_keyboard",
    "chat_draft_keyboard",
    "chats_list_keyboard",
]
