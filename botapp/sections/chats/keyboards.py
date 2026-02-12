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


def _trim_button_text(value: str, limit: int = 48) -> str:
    text = " ".join((value or "").split()).strip()
    if not text:
        return "(чат)"
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def chats_list_keyboard(
    *,
    items: list[dict],
    page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    
    # ═══ Список чатов ═══
    for item in items:
        token = item.get("token") or item.get("chat_id")
        chat_id = item.get("chat_id") or token
        caption = _trim_button_text(item.get("title") or item.get("caption") or "(чат)")
        unread = int(item.get("unread_count") or item.get("unread") or 0)
        prefix = "🔴" if unread > 0 else "💬"
        suffix = f" ({unread})" if unread > 0 else ""
        if not token:
            continue
        rows.append([
            InlineKeyboardButton(
                text=f"{prefix} {caption}{suffix}",
                callback_data=ChatsCallbackData(action="open", token=token, chat_id=chat_id).pack(),
            )
        ])

    # ═══ Пагинация ═══
    safe_total = max(total_pages, 1)
    rows.append([
        InlineKeyboardButton(
            text="◀️ Назад" if page > 0 else "⏮",
            callback_data=ChatsCallbackData(action="page", page=max(page - 1, 0)).pack(),
        ),
        InlineKeyboardButton(
            text=f"Стр. {page + 1}/{safe_total}",
            callback_data=ChatsCallbackData(action="noop", page=page).pack(),
        ),
        InlineKeyboardButton(
            text="Вперёд ▶️" if page + 1 < total_pages else "⏭",
            callback_data=ChatsCallbackData(
                action="page", page=min(page + 1, max(total_pages - 1, 0))
            ).pack(),
        ),
    ])
    
    # ═══ Действия ═══
    rows.append([
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=ChatsCallbackData(action="refresh", page=page).pack(),
        ),
    ])
    
    # ═══ Навигация ═══
    rows.append([
        InlineKeyboardButton(
            text="🏠 В главное меню",
            callback_data=MenuCallbackData(section="home", action="open").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def chat_header_keyboard(
    token: str,
    *,
    chat_id: str | None = None,
    page: int | None = None,
) -> InlineKeyboardMarkup:
    chat_ref = (chat_id or token or "").strip()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            # ═══ Создание ответа ═══
            [
                InlineKeyboardButton(
                    text="🤖 ИИ черновик",
                    callback_data=ChatsCallbackData(action="ai", chat_id=chat_ref).pack(),
                ),
                InlineKeyboardButton(
                    text="✍️ Свой ответ",
                    callback_data=ChatsCallbackData(action="edit_ai", chat_id=chat_ref).pack(),
                ),
            ],
            # ═══ СДЭК отправка ═══
            [
                InlineKeyboardButton(
                    text="🚚 СДЭК из чата",
                    callback_data=ChatsCallbackData(action="create_cdek", chat_id=chat_ref).pack(),
                ),
            ],
            # ═══ История ═══
            [
                InlineKeyboardButton(
                    text="🔄 Обновить",
                    callback_data=ChatsCallbackData(action="refresh_thread", chat_id=chat_ref).pack(),
                ),
                InlineKeyboardButton(
                    text="⏮ Ранее",
                    callback_data=ChatsCallbackData(action="older", chat_id=chat_ref).pack(),
                ),
                InlineKeyboardButton(
                    text="🧹 Очистить",
                    callback_data=ChatsCallbackData(action="clear", chat_id=chat_ref).pack(),
                ),
            ],
            # ═══ Навигация ═══
            [
                InlineKeyboardButton(
                    text="↩️ К списку",
                    callback_data=ChatsCallbackData(action="list", chat_id=chat_ref, page=page).pack(),
                ),
                InlineKeyboardButton(
                    text="🏠 В меню",
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
                text="📋 Назад к списку",
                callback_data=ChatsCallbackData(action="list", chat_id=chat_id).pack(),
            ),
            InlineKeyboardButton(
                text="🏠 В меню",
                callback_data=MenuCallbackData(section="home", action="open").pack(),
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
            # ═══ Отправка (главное действие) ═══
            [
                InlineKeyboardButton(
                    text="📤 Отправить в Ozon",
                    callback_data=ChatsCallbackData(action="send_ai", token=chat_id).pack(),
                )
            ],
            # ═══ Редактирование ═══
            [
                InlineKeyboardButton(
                    text="🔄 Перегенерировать",
                    callback_data=ChatsCallbackData(action="ai", token=chat_id).pack(),
                ),
                InlineKeyboardButton(
                    text="✏️ Править",
                    callback_data=ChatsCallbackData(action="edit_ai", token=chat_id).pack(),
                )
            ],
            # ═══ Промт ═══
            [
                InlineKeyboardButton(
                    text="🎯 По моему промту",
                    callback_data=ChatsCallbackData(action="ai_my_prompt", token=chat_id).pack(),
                ),
                InlineKeyboardButton(
                    text="⚙️ Задать промт",
                    callback_data=ChatsCallbackData(action="set_my_prompt", token=chat_id).pack(),
                )
            ],
            # ═══ Навигация ═══
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=ChatsCallbackData(action="refresh_thread", token=chat_id).pack(),
                ),
                InlineKeyboardButton(
                    text="↩️ К списку",
                    callback_data=ChatsCallbackData(action="list", token=chat_id).pack(),
                ),
                InlineKeyboardButton(
                    text="🏠 Меню",
                    callback_data=MenuCallbackData(section="home", action="open").pack(),
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
