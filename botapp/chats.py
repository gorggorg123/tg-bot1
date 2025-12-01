"""Ozon chat (v3) listing and AI-assisted replies."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from botapp.ai_client import generate_chat_reply
from botapp.keyboards import ChatsCallbackData, chat_card_keyboard, chats_list_keyboard
from botapp.message_gc import (
    SECTION_CHAT_CARD,
    SECTION_CHAT_LIST,
    SECTION_CHAT_PROMPT,
    delete_section_message,
    send_section_message,
)
from botapp.ozon_client import (
    ChatHistoryMessage,
    get_chat_history,
    get_chat_list,
    mark_chat_read,
    send_chat_message,
)
from botapp.product_context import build_product_context

logger = logging.getLogger(__name__)

router = Router()


class ChatState(StatesGroup):
    list = State()
    view = State()
    edit_answer = State()


@dataclass
class ChatScreen:
    chat_id: str
    page: int = 0
    title: str | None = None
    history: list[ChatHistoryMessage] | None = None


async def _format_chat_title(item, idx: int) -> tuple[str, str]:
    name = item.participant_name or item.order_id or item.chat_id
    status = f" ({item.status})" if getattr(item, "status", None) else ""
    preview = (item.last_message_text or "").strip()
    if len(preview) > 40:
        preview = preview[:37] + "..."
    title = f"#{idx}: {name}{status} — {preview}" if preview else f"#{idx}: {name}{status}"
    return item.chat_id, title


async def show_chat_list(callback: CallbackQuery, state: FSMContext, page: int = 0) -> None:
    await state.set_state(ChatState.list)
    limit = 10
    offset = page * limit
    try:
        items = await get_chat_list(limit=limit + 1, offset=offset)
    except Exception as exc:
        logger.exception("Failed to load chat list: %s", exc)
        await send_section_message(
            SECTION_CHAT_LIST,
            callback=callback,
            text="Не удалось загрузить список чатов, попробуйте позже",
        )
        return

    has_next = len(items) > limit
    items = items[:limit]
    rows = [await _format_chat_title(item, idx + 1 + offset) for idx, item in enumerate(items)]
    markup = chats_list_keyboard(rows, page=page, has_prev=page > 0, has_next=has_next)
    await send_section_message(
        SECTION_CHAT_LIST,
        callback=callback,
        text="💬 Активные чаты Ozon",
        reply_markup=markup,
    )


async def _render_history(history: list[ChatHistoryMessage]) -> str:
    lines: list[str] = []
    for msg in history:
        ts = msg.created_at
        ts_str = ""
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%d.%m %H:%M")
        author = "Покупатель" if (msg.author_type or "").lower() == "buyer" else "Продавец"
        text = msg.text or "[пустое сообщение]"
        lines.append(f"[{ts_str}] {author}: {text}")
    return "\n".join(lines[-30:]) or "Нет сообщений"


async def show_chat(callback: CallbackQuery, state: FSMContext, chat_id: str, page: int = 0) -> None:
    try:
        history = await get_chat_history(chat_id=chat_id, limit=30, offset=0)
    except Exception as exc:
        logger.exception("Failed to load chat history: %s", exc)
        await send_section_message(
            SECTION_CHAT_CARD,
            callback=callback,
            text="Не удалось загрузить историю чата",
        )
        return

    await state.update_data(chat=ChatScreen(chat_id=chat_id, page=page, history=history))
    body = await _render_history(history)
    await send_section_message(
        SECTION_CHAT_CARD,
        callback=callback,
        text=f"История чата {chat_id}:\n\n{body}",
        reply_markup=chat_card_keyboard(chat_id, page=page),
    )


async def ask_manual_answer(callback: CallbackQuery, state: FSMContext, chat_id: str, page: int = 0) -> None:
    await state.set_state(ChatState.edit_answer)
    await state.update_data(chat=ChatScreen(chat_id=chat_id, page=page))
    await send_section_message(
        SECTION_CHAT_PROMPT,
        callback=callback,
        text="Введите свой вариант ответа для покупателя…",
        persistent=True,
    )


async def handle_manual_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    chat: ChatScreen | None = data.get("chat") if isinstance(data, dict) else None
    if not chat:
        await message.answer("Чат не выбран")
        return

    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите текст ответа")
        return

    try:
        await send_chat_message(chat_id=chat.chat_id, text=text)
        await mark_chat_read(chat_id=chat.chat_id)
    except Exception as exc:
        logger.exception("Failed to send chat reply: %s", exc)
        await message.answer("Не удалось отправить ответ в Ozon")
        return

    await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
    await state.clear()
    await message.answer("Ответ отправлен")


async def generate_ai_answer(callback: CallbackQuery, state: FSMContext, chat_id: str, page: int = 0) -> None:
    data = await state.get_data()
    history: list[ChatHistoryMessage] = []
    if isinstance(data, dict) and isinstance(data.get("chat"), ChatScreen):
        history = data["chat"].history or []
    if not history:
        history = await get_chat_history(chat_id=chat_id, limit=10, offset=0)

    chat_messages = []
    for msg in history[-10:]:
        role = "assistant" if (msg.author_type or "").lower() != "buyer" else "user"
        chat_messages.append({"role": role, "content": msg.text or ""})

    context = build_product_context(sku=None)
    draft = await generate_chat_reply(chat_messages=chat_messages, product_context=context)
    await send_section_message(
        SECTION_CHAT_PROMPT,
        callback=callback,
        text=f"Вариант ответа ИИ:\n\n{draft}",
        reply_markup=chat_card_keyboard(chat_id, page=page),
        persistent=True,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "list"))
async def cb_chat_list(callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext) -> None:
    await show_chat_list(callback, state, page=callback_data.page or 0)


@router.callback_query(ChatsCallbackData.filter(F.action == "open"))
async def cb_chat_open(callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext) -> None:
    if not callback_data.chat_id:
        await callback.answer()
        return
    await show_chat(callback, state, chat_id=callback_data.chat_id, page=callback_data.page or 0)


@router.callback_query(ChatsCallbackData.filter(F.action == "refresh"))
async def cb_chat_refresh(callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext) -> None:
    if not callback_data.chat_id:
        await callback.answer()
        return
    await show_chat(callback, state, chat_id=callback_data.chat_id, page=callback_data.page or 0)


@router.callback_query(ChatsCallbackData.filter(F.action == "ai"))
async def cb_chat_ai(callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext) -> None:
    if not callback_data.chat_id:
        await callback.answer()
        return
    await generate_ai_answer(callback, state, chat_id=callback_data.chat_id, page=callback_data.page or 0)


@router.callback_query(ChatsCallbackData.filter(F.action == "manual"))
async def cb_chat_manual(callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext) -> None:
    if not callback_data.chat_id:
        await callback.answer()
        return
    await ask_manual_answer(callback, state, chat_id=callback_data.chat_id, page=callback_data.page or 0)


@router.message(ChatState.edit_answer)
async def on_manual_answer(message: Message, state: FSMContext) -> None:
    await handle_manual_answer(message, state)
