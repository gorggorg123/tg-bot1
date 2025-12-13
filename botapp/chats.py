from __future__ import annotations

import logging
import math
import textwrap
from contextlib import suppress
from datetime import datetime, timezone
from typing import Dict

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from botapp.chats_ai import suggest_chat_reply
from botapp.keyboards import (
    ChatsCallbackData,
    MenuCallbackData,
    chat_actions_keyboard,
    chat_ai_confirm_keyboard,
    chats_list_keyboard,
    main_menu_keyboard,
)
from botapp.message_gc import (
    SECTION_ACCOUNT,
    SECTION_CHAT_HISTORY,
    SECTION_CHAT_PROMPT,
    SECTION_CHATS_LIST,
    SECTION_FBO,
    SECTION_FINANCE_TODAY,
    SECTION_MENU,
    SECTION_QUESTION_CARD,
    SECTION_QUESTION_PROMPT,
    SECTION_QUESTIONS_LIST,
    SECTION_REVIEW_CARD,
    SECTION_REVIEW_PROMPT,
    SECTION_REVIEWS_LIST,
    delete_section_message,
    send_section_message,
)
from botapp.ozon_client import (
    OzonAPIError,
    chat_history,
    chat_list,
    chat_read,
    chat_send_message,
    get_posting_products,
)
from botapp.utils import send_ephemeral_message

try:
    from botapp.states import ChatStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class ChatStates(StatesGroup):
        waiting_manual = State()
        waiting_ai_confirm = State()

logger = logging.getLogger(__name__)

router = Router()

CHAT_PAGE_SIZE = 7  # показываем чуть больше чатов на страницу
CHAT_LIST_LIMIT = 100  # верхняя граница одной выгрузки списка чатов


def _truncate_text(text: str, limit: int = 80) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _is_timestamp(value: str) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except Exception:
        return False


def _parse_ts(ts_value: str | None) -> datetime | None:
    if not ts_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts_value).replace("Z", "+00:00"))
        return parsed
    except Exception:
        return None


def _ts(msg: dict) -> str:
    """Достать временную метку сообщения как строку."""

    if not isinstance(msg, dict):
        return ""
    for key in ("created_at", "send_time"):
        value = msg.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            except Exception:
                continue

    raw = msg.get("_raw")
    if isinstance(raw, dict):
        for key in ("created_at", "send_time"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
    return ""


def _extract_text(msg: dict) -> str | None:
    def _pick_from_dict(d: dict) -> str | None:
        PREFERRED_KEYS = (
            "text",
            "message",
            "content",
            "body",
            "title",
            "question",
            "description",
            "comment",
        )
        if not isinstance(d, dict):
            return None
        for key in PREFERRED_KEYS:
            value = d.get(key)
            if isinstance(value, str) and value.strip() and not _is_timestamp(value.strip()):
                value_str = value.strip()
                return value_str
            if isinstance(value, dict):
                for k2 in PREFERRED_KEYS:
                    inner = value.get(k2)
                    if isinstance(inner, str):
                        inner_str = inner.strip()
                        if inner_str and not _is_timestamp(inner_str):
                            return inner_str
        data_val = d.get("data")
        if isinstance(data_val, list):
            for item in data_val:
                if isinstance(item, str):
                    s = item.strip()
                    if s and not _is_timestamp(s):
                        return s
                if isinstance(item, dict):
                    nested = _pick_from_dict(item)
                    if nested:
                        return nested
        return None

    if not isinstance(msg, dict):
        return None

    direct = _pick_from_dict(msg)
    if direct:
        return direct

    root = msg.get("_raw")
    if not isinstance(root, (dict, list)):
        return None

    queue: list[object] = [root]
    seen: set[int] = set()

    while queue:
        cur = queue.pop(0)
        obj_id = id(cur)
        if obj_id in seen:
            continue
        seen.add(obj_id)

        if isinstance(cur, dict):
            candidate = _pick_from_dict(cur)
            if candidate:
                return candidate
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    queue.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    queue.append(v)

    return None


def _detect_message_role(msg: dict) -> str:
    """Вернуть роль автора сообщения (customer/seller/support/...)."""

    user_block = msg.get("user") if isinstance(msg.get("user"), dict) else None
    author_block = msg.get("author") if isinstance(msg.get("author"), dict) else None

    role = None
    if user_block:
        role = user_block.get("type") or user_block.get("role") or user_block.get("name")

    if not role and author_block:
        role = (
            author_block.get("role")
            or author_block.get("type")
            or author_block.get("name")
            or author_block.get("author_type")
        )

    if not role:
        role = (
            msg.get("author_type")
            or msg.get("from")
            or msg.get("sender")
            or msg.get("direction")
        )

    return str(role or "").lower()


def _safe_chat_id(chat: dict) -> str | None:
    if not isinstance(chat, dict):
        return None
    raw_id = chat.get("chat_id") or chat.get("id") or chat.get("chatId")
    return str(raw_id) if raw_id not in (None, "") else None


def _chat_posting(chat: dict) -> str | None:
    if not isinstance(chat, dict):
        return None
    for key in ("posting_number", "postingNumber", "order_id", "orderId"):
        val = chat.get(key)
        if val not in (None, ""):
            return str(val)
    return None


def _chat_buyer_name(chat: dict) -> str | None:
    if not isinstance(chat, dict):
        return None
    candidates = [
        chat.get("buyer_name"),
        chat.get("client_name"),
        chat.get("customer_name"),
    ]
    user_block = chat.get("user") if isinstance(chat.get("user"), dict) else None
    if user_block:
        candidates.extend(
            [
                user_block.get("name"),
                user_block.get("phone"),
                user_block.get("display_name"),
            ]
        )
    for candidate in candidates:
        if candidate not in (None, ""):
            return str(candidate)
    return None


def _chat_unread_count(chat: dict) -> int:
    if not isinstance(chat, dict):
        return 0
    for key in ("unread_count", "unreadCount"):
        value = chat.get(key)
        try:
            return int(value)
        except Exception:
            continue
    if chat.get("is_unread") or chat.get("has_unread"):
        return 1
    return 0


def is_buyer_chat(chat: dict) -> bool:
    """Отфильтровать служебные/системные чаты и оставить только покупательские."""

    if not isinstance(chat, dict):
        return False

    raw = chat.get("_raw") if isinstance(chat.get("_raw"), dict) else None
    merged: dict = {}
    if raw:
        merged.update(raw)
    merged.update(chat)

    def _match_any(value: str, needles: tuple[str, ...]) -> bool:
        val = value.lower()
        return any(word in val for word in needles)

    # Позитивные признаки
    buyer_candidate = _chat_buyer_name(merged)
    role_fields = [
        merged.get("role"),
        merged.get("type"),
        merged.get("chat_type"),
        merged.get("source"),
    ]
    if isinstance(merged.get("participants"), list):
        for p in merged["participants"]:
            if isinstance(p, dict):
                role_fields.extend([p.get("role"), p.get("type"), p.get("name")])

    positive_found = False
    for role in role_fields:
        if role and _match_any(str(role), ("buyer", "customer", "client", "user")):
            positive_found = True
            break
    if buyer_candidate:
        positive_found = True

    # Негативные признаки
    negative_fields = [
        merged.get("title"),
        merged.get("topic"),
        merged.get("chat_type"),
        merged.get("type"),
        merged.get("source"),
    ]
    for participant in merged.get("participants", []) if isinstance(merged.get("participants"), list) else []:
        if isinstance(participant, dict):
            negative_fields.extend(
                [participant.get("name"), participant.get("role"), participant.get("type")]
            )

    has_negative = False
    for value in negative_fields:
        if value and _match_any(
            str(value), ("support", "ozon", "system", "notification", "service", "crm")
        ):
            has_negative = True
            if not positive_found:
                return False

    # Если нет негативных признаков, но и нет явного покупателя — считаем чат допустимым,
    # чтобы не скрыть реального клиента из-за неполной схемы.
    if has_negative and not positive_found:
        return False
    return True if positive_found or not has_negative else False


def _chat_last_dt(chat: dict) -> datetime | None:
    if not isinstance(chat, dict):
        return None
    ts_value = (
        chat.get("last_message_time")
        or chat.get("updated_at")
        or chat.get("updatedAt")
    )
    if isinstance(ts_value, str):
        parsed = _parse_ts(ts_value)
        if parsed:
            return parsed

    last_block = chat.get("last_message") or chat.get("lastMessage")
    if isinstance(last_block, dict):
        ts_from_message = _parse_ts(_ts(last_block))
        if ts_from_message:
            return ts_from_message
    return None


def _chat_last_text(chat: dict) -> str | None:
    if not isinstance(chat, dict):
        return None
    last_block = chat.get("last_message") or chat.get("lastMessage")
    if isinstance(last_block, dict):
        text = _extract_text(last_block) or last_block.get("text") or last_block.get("message")
        if text:
            return str(text)
    text_field = chat.get("last_message_text") or chat.get("lastMessageText")
    if text_field:
        return str(text_field)
    return None


def _chat_message_count(chat: dict) -> int | None:
    if not isinstance(chat, dict):
        return None
    for key in ("messages_count", "message_count", "messagesCount", "messageCount"):
        value = chat.get(key)
        try:
            count = int(value)
            if count >= 0:
                return count
        except Exception:
            continue
    return None


def _chat_display(chat: dict) -> tuple[str | None, str, str, int, str, datetime | None]:
    """Построить информативное название и превью чата."""

    chat_id = _safe_chat_id(chat)
    posting = _chat_posting(chat)
    buyer = _chat_buyer_name(chat)
    unread_count = _chat_unread_count(chat)
    last_dt = _chat_last_dt(chat)
    last_label = last_dt.strftime("%d.%m %H:%M") if last_dt else ""
    last_text = _chat_last_text(chat)
    msg_count = _chat_message_count(chat)

    fallback_buyer = None
    if chat_id:
        tail = chat_id[-4:]
        fallback_buyer = f"Покупатель {tail}"

    if buyer:
        title = buyer
        if posting:
            title = f"{buyer} • заказ {posting}"
    elif posting:
        title = f"Заказ {posting}"
        if last_label:
            title = f"{title} • {last_label}"
    elif fallback_buyer:
        title = fallback_buyer
    else:
        if last_label and msg_count:
            title = f"Чат от {last_label} • {msg_count} сообщений"
        elif last_label:
            title = f"Чат от {last_label}"
        elif msg_count:
            title = f"Чат • {msg_count} сообщений"
        else:
            title = "Чат без названия"

    short_title = _truncate_text(title, limit=64)
    preview = _truncate_text(last_text, limit=60) if last_text else ""
    return chat_id, title, short_title, unread_count, preview, last_dt


def _chat_sort_key(chat: dict) -> tuple:
    last_dt = _chat_last_dt(chat)
    return (last_dt or datetime.min, chat.get("last_message_time") or "")


def _describe_attachments(msg: dict) -> list[str]:
    """Сформировать короткое описание вложений (фото/файлы) для истории."""

    if not isinstance(msg, dict):
        return []
    attachments = msg.get("attachments") or msg.get("files")
    lines: list[str] = []
    if isinstance(attachments, list):
        for item in attachments:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("file_name") or item.get("filename") or "файл"
            url = item.get("url") or item.get("link") or item.get("download_url")
            label = f"📎 {name}"
            if url:
                label = f"{label} ({url})"
            lines.append(label)
    return lines


def _collect_product_names(cache: Dict[str, list[str]], posting: str) -> list[str]:
    if posting in cache:
        return cache[posting]
    return []


def _render_products_line(product_names: list[str]) -> str | None:
    if not product_names:
        return None
    unique = []
    for name in product_names:
        if name and name not in unique:
            unique.append(name)
    if not unique:
        return None
    return "Товары: " + ", ".join(unique[:5])


async def _send_chats_list(
    *,
    user_id: int,
    state: FSMContext,
    page: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
    unread_only: bool | None = None,
    refresh: bool = False,
) -> None:
    data = await state.get_data()
    unread_flag = bool(unread_only if unread_only is not None else data.get("chats_unread_only"))
    show_service = bool(data.get("chats_show_service"))

    cached_list = data.get("chats_all") if isinstance(data.get("chats_all"), list) else None
    need_reload = refresh or not isinstance(cached_list, list) or not cached_list

    try:
        items_raw = (
            await chat_list(limit=CHAT_LIST_LIMIT, offset=0, include_service=True)
            if need_reload
            else cached_list or []
        )
    except OzonAPIError as exc:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                f"⚠️ Не удалось получить список чатов. Ошибка: {exc}",
                user_id=user_id,
            )
        logger.warning("Unable to load chats list: %s", exc)
        return
    except Exception:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "⚠️ Не удалось получить список чатов. Попробуйте позже.",
                user_id=user_id,
            )
        logger.exception("Unexpected error while loading chats list")
        return

    sorted_items = sorted(items_raw, key=_chat_sort_key, reverse=True)
    cache: dict[str, dict] = {}
    for chat in sorted_items:
        cid = _safe_chat_id(chat)
        if cid:
            cache[cid] = chat if isinstance(chat, dict) else {}

    buyer_items = [chat for chat in sorted_items if show_service or is_buyer_chat(chat)]
    filtered_items = [chat for chat in buyer_items if not unread_flag or _chat_unread_count(chat) > 0]
    total_count = len(buyer_items)
    unread_total = sum(1 for chat in buyer_items if _chat_unread_count(chat) > 0)
    total_pages = max(1, math.ceil(max(1, len(filtered_items)) / CHAT_PAGE_SIZE))
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * CHAT_PAGE_SIZE
    end = start + CHAT_PAGE_SIZE
    page_slice = filtered_items[start:end]

    display_rows: list[str] = []
    keyboard_items: list[tuple[str, str]] = []
    for idx, chat in enumerate(page_slice, start=start + 1):
        chat_id_val, title, short_title, unread_count, preview, last_dt = _chat_display(chat)
        if not chat_id_val:
            continue
        badge = "🔴" if unread_count > 0 else "⚪"
        ts_label = last_dt.strftime("%d.%m %H:%M") if last_dt else ""
        preview_label = f'"{preview}"' if preview else ""
        line_parts = [
            f"{idx}) {badge} {title}",
            ts_label,
            preview_label,
        ]
        display_rows.append(" | ".join([part for part in line_parts if part]))
        kb_caption = " | ".join(
            [
                f"{badge}{unread_count if unread_count else ''}",
                _truncate_text(title, limit=26),
                ts_label,
                preview_label,
            ]
        ).replace("||", "|")
        keyboard_items.append((chat_id_val, kb_caption))

    lines = ["💬 Чаты с покупателями"]
    lines.append(
        f"Всего: {total_count} | Непрочитано: {unread_total}" +
        (" | Фильтр: все" if show_service else " | Фильтр: только покупатели")
    )
    lines.append(f"Только непрочитанные: {'ON' if unread_flag else 'OFF'}")
    lines.append("")

    if not display_rows:
        lines.append("Нет активных диалогов" if not unread_flag else "Нет непрочитанных диалогов")
    else:
        lines.extend(display_rows)
    lines.append("")
    lines.append(f"Стр. {safe_page + 1}/{total_pages}")

    markup = chats_list_keyboard(
        items=keyboard_items,
        page=safe_page,
        total_pages=total_pages,
        unread_only=unread_flag,
        show_service=show_service,
    )
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    await state.update_data(
        chats_cache=cache,
        chats_page=safe_page,
        chats_unread_only=unread_flag,
        chats_all=sorted_items,
        chats_show_service=show_service,
    )
    sent = await send_section_message(
        SECTION_CHATS_LIST,
        text="\n".join(lines),
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
    )
    await delete_section_message(
        user_id,
        SECTION_CHAT_HISTORY,
        active_bot,
        preserve_message_id=sent.message_id if sent else None,
    )
    await delete_section_message(user_id, SECTION_CHAT_PROMPT, active_bot, force=True)


def _format_chat_history_text(
    chat_meta: dict | None,
    messages: list[dict],
    *,
    products: list[str] | None = None,
    limit: int = 30,
) -> str:
    buyer = _chat_buyer_name(chat_meta or {}) if isinstance(chat_meta, dict) else None
    posting = _chat_posting(chat_meta or {}) if isinstance(chat_meta, dict) else None
    product = None
    if isinstance(chat_meta, dict):
        product = chat_meta.get("product_name") or chat_meta.get("product_title")

    lines = ["💬 Чат с покупателем"]
    if buyer:
        lines.append(f"Покупатель: {buyer}")
    if posting:
        lines.append(f"Заказ: {posting}")
    if product:
        lines.append(f"Товар: {product}")
    products_line = _render_products_line(products or [])
    if products_line:
        lines.append(products_line)

    unread_count = _chat_unread_count(chat_meta or {}) if isinstance(chat_meta, dict) else 0
    has_unread = bool(chat_meta.get("is_unread") or chat_meta.get("has_unread")) if isinstance(chat_meta, dict) else False
    if unread_count > 0:
        lines.append(f"🔴 Непрочитанных сообщений: {unread_count}")
    elif has_unread:
        lines.append("🔴 Непрочитанные сообщения есть")
    else:
        lines.append("Все сообщения прочитаны.")

    lines.append("")

    prepared: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = _extract_text(msg)
        attachments = _describe_attachments(msg)
        if not text and not attachments:
            continue

        role_lower = _detect_message_role(msg)
        if "crm" in role_lower or "support" in role_lower:
            continue

        if "seller" in role_lower or "operator" in role_lower or "store" in role_lower:
            author = "🧑‍🏭 Вы"
        elif "courier" in role_lower:
            author = "🚚 Курьер"
        else:
            author = "👤 Покупатель"

        ts_value = _ts(msg)
        ts_dt = _parse_ts(ts_value)
        ts_label = None
        if ts_dt:
            ts_safe = ts_dt.astimezone(timezone.utc) if ts_dt.tzinfo else ts_dt
            ts_label = ts_safe.strftime("%d.%m %H:%M")
        elif ts_value:
            ts_label = ts_value[:16]
        else:
            ts_label = ""

        wrapped: list[str] = []
        if text:
            for line in str(text).splitlines() or [""]:
                stripped = line.strip()
                if not stripped:
                    wrapped.append("")
                    continue
                wrapped.extend(textwrap.wrap(stripped, width=78) or [stripped])
        wrapped.extend(attachments)

        prepared.append(
            {
                "author": author,
                "text_lines": wrapped,
                "ts": ts_dt.astimezone(timezone.utc).replace(tzinfo=None) if ts_dt else None,
                "ts_raw": ts_value or "",
                "ts_label": ts_label or "",
            }
        )

    if not prepared:
        lines.append("История чата пуста или нет текстовых сообщений.")
    else:
        prepared.sort(key=lambda item: (item.get("ts") or datetime.min, item.get("ts_raw") or ""))
        trimmed = prepared[-max(1, limit) :]
        for item in trimmed:
            ts_label = item.get("ts_label") or ""
            author = item.get("author") or "👤 Покупатель"
            lines.append(f"[{ts_label}] {author}:")
            lines.extend(item.get("text_lines") or [])
            lines.append("")

    lines.append("─────────────")
    lines.append("Используйте кнопки ниже, чтобы ответить покупателю или обновить чат.")

    body = "\n".join(lines).strip()
    max_len = 3500
    if len(body) > max_len:
        body = "…\n" + body[-max_len:]
    return body


async def _load_posting_products(state: FSMContext, chat_meta: dict | None) -> list[str]:
    posting = _chat_posting(chat_meta or {}) if isinstance(chat_meta, dict) else None
    if not posting:
        return []

    data = await state.get_data()
    cache = data.get("posting_products") if isinstance(data.get("posting_products"), dict) else {}
    cached = _collect_product_names(cache, posting)
    if cached:
        return cached

    try:
        names = await get_posting_products(posting)
    except Exception as exc:  # pragma: no cover - сеть/схема
        logger.warning("Failed to fetch posting %s products: %s", posting, exc)
        names = []

    if not isinstance(cache, dict):
        cache = {}
    cache[posting] = names
    await state.update_data(posting_products=cache)
    return names


async def _open_chat_history(
    *,
    user_id: int,
    chat_id: str,
    state: FSMContext,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id_override: int | None = None,
) -> None:
    data = await state.get_data()
    chat_meta = None
    cache = data.get("chats_cache") if isinstance(data.get("chats_cache"), dict) else {}
    if isinstance(cache, dict):
        chat_meta = cache.get(chat_id)

    try:
        messages = await chat_history(chat_id, limit=30)
    except OzonAPIError as exc:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                f"⚠️ Не удалось получить историю чата. Ошибка: {exc}",
                user_id=user_id,
            )
        logger.warning("Unable to load chat history for %s: %s", chat_id, exc)
        return
    except Exception:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "⚠️ Не удалось загрузить историю чата. Попробуйте позже.",
                user_id=user_id,
            )
        logger.exception("Unexpected error while loading chat %s", chat_id)
        return

    with suppress(Exception):
        await chat_read(chat_id, messages)

    products = await _load_posting_products(state, chat_meta)
    history_text = _format_chat_history_text(chat_meta, messages, products=products)
    markup = chat_actions_keyboard(chat_id)
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id_override or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    await state.update_data(chat_history=messages, current_chat_id=chat_id, chats_cache=cache)
    await send_section_message(
        SECTION_CHAT_HISTORY,
        text=history_text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id_override,
        user_id=user_id,
    )
    await delete_section_message(user_id, SECTION_CHAT_PROMPT, active_bot, force=True)


async def _clear_chat_sections(bot: Bot, user_id: int) -> None:
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_FBO, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_FINANCE_TODAY, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_ACCOUNT, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_REVIEWS_LIST, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_REVIEW_CARD, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_QUESTIONS_LIST, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_QUESTION_CARD, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_REVIEW_PROMPT, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_QUESTION_PROMPT, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_CHATS_LIST, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_CHAT_HISTORY, force=True)
    await delete_section_message(bot=bot, user_id=user_id, section=SECTION_CHAT_PROMPT, force=True)


@router.callback_query(MenuCallbackData.filter(F.section == "chats"))
async def cb_chats_menu(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    await state.clear()
    await _clear_chat_sections(callback.message.bot, user_id)
    await _send_chats_list(user_id=user_id, state=state, page=0, callback=callback)


@router.callback_query(ChatsCallbackData.filter(F.action == "list"))
async def cb_chats_list(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    page = int(callback_data.page or 0)
    data = await state.get_data()
    unread_only = bool(data.get("chats_unread_only"))
    await _send_chats_list(
        user_id=user_id,
        state=state,
        page=page,
        callback=callback,
        unread_only=unread_only,
        refresh=True,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "filter"))
async def cb_chats_filter(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    data = await state.get_data()
    current_flag = bool(data.get("chats_unread_only"))
    page = int(callback_data.page or data.get("chats_page") or 0)
    await _send_chats_list(
        user_id=callback.from_user.id,
        state=state,
        page=int(page),
        callback=callback,
        unread_only=not current_flag,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "service"))
async def cb_chats_service_toggle(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    data = await state.get_data()
    current_flag = bool(data.get("chats_show_service"))
    page = int(callback_data.page or data.get("chats_page") or 0)
    await state.update_data(chats_show_service=not current_flag)
    await _send_chats_list(
        user_id=callback.from_user.id,
        state=state,
        page=int(page),
        callback=callback,
        unread_only=bool(data.get("chats_unread_only")),
        refresh=False,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "open"))
async def cb_open_chat(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    chat_id = callback_data.chat_id
    if not chat_id:
        await send_ephemeral_message(
            callback.bot,
            callback.message.chat.id,
            "Не удалось определить чат.",
            user_id=user_id,
        )
        return

    await _open_chat_history(
        user_id=user_id,
        chat_id=chat_id,
        state=state,
        callback=callback,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "refresh"))
async def cb_chat_refresh(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    chat_id = callback_data.chat_id
    if not chat_id:
        await callback.answer("Не удалось определить чат", show_alert=True)
        return

    await callback.answer()

    await _open_chat_history(
        user_id=callback.from_user.id,
        chat_id=chat_id,
        state=state,
        callback=callback,
        bot=callback.bot,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "back"))
async def cb_chat_back(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    data = await state.get_data()
    page = int(data.get("chats_page") or 0)
    unread_only = bool(data.get("chats_unread_only"))
    await _send_chats_list(
        user_id=callback.from_user.id,
        state=state,
        page=page,
        callback=callback,
        unread_only=unread_only,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "reply"))
async def cb_chat_reply(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    chat_id = callback_data.chat_id
    if not chat_id:
        return
    await state.update_data(chat_id=chat_id)
    await state.set_state(ChatStates.waiting_manual)
    await send_section_message(
        SECTION_CHAT_PROMPT,
        text="✍️ Введите ответ для покупателя в чате",
        callback=callback,
        user_id=callback.from_user.id,
        persistent=True,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "ai"))
async def cb_chat_ai(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    chat_id = callback_data.chat_id
    if not chat_id:
        return

    data = await state.get_data()
    messages = data.get("chat_history") if isinstance(data.get("chat_history"), list) else []
    if not messages:
        try:
            messages = await chat_history(chat_id, limit=20)
        except Exception as exc:  # pragma: no cover - сеть/формат
            await send_ephemeral_message(
                callback.bot,
                callback.message.chat.id,
                f"Не удалось загрузить историю чата: {exc}",
                user_id=user_id,
            )
            return

    cache = data.get("chats_cache") if isinstance(data.get("chats_cache"), dict) else {}
    meta = cache.get(chat_id) if isinstance(cache, dict) else None
    product_name = meta.get("product_name") if isinstance(meta, dict) else None

    draft = await suggest_chat_reply(messages, product_name)
    if not draft:
        await send_ephemeral_message(
            callback.bot,
            callback.message.chat.id,
            "🤖 Не удалось сгенерировать черновик. Попробуйте позже.",
            user_id=user_id,
        )
        return

    await state.update_data(chat_id=chat_id, chat_history=messages, ai_draft=draft)
    await state.set_state(ChatStates.waiting_ai_confirm)
    await send_section_message(
        SECTION_CHAT_PROMPT,
        text=f"🤖 Черновик ответа:\n\n{draft}",
        reply_markup=chat_ai_confirm_keyboard(chat_id),
        callback=callback,
        user_id=user_id,
        persistent=True,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "ai_send"))
async def cb_chat_ai_send(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    data = await state.get_data()
    chat_id = callback_data.chat_id or data.get("chat_id")
    draft = data.get("ai_draft")
    if not chat_id or not draft:
        await send_ephemeral_message(
            callback.bot,
            callback.message.chat.id,
            "Черновик ответа не найден",
            user_id=user_id,
        )
        return
    try:
        await chat_send_message(chat_id, draft)
    except Exception as exc:
        await send_ephemeral_message(
            callback.bot,
            callback.message.chat.id,
            f"Не удалось отправить сообщение: {exc}",
            user_id=user_id,
        )
        return

    await delete_section_message(user_id, SECTION_CHAT_PROMPT, callback.bot, force=True)
    await state.clear()
    await _open_chat_history(
        user_id=user_id, chat_id=chat_id, state=state, callback=callback
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "ai_edit"))
async def cb_chat_ai_edit(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    chat_id = callback_data.chat_id
    if not chat_id:
        return
    await state.update_data(chat_id=chat_id, ai_draft=None)
    await state.set_state(ChatStates.waiting_ai_confirm)
    await send_section_message(
        SECTION_CHAT_PROMPT,
        text="✏️ Отправьте отредактированный текст ответа",
        callback=callback,
        user_id=callback.from_user.id,
        persistent=True,
    )


@router.callback_query(ChatsCallbackData.filter(F.action == "ai_cancel"))
async def cb_chat_ai_cancel(
    callback: CallbackQuery, callback_data: ChatsCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    await delete_section_message(callback.from_user.id, SECTION_CHAT_PROMPT, callback.bot, force=True)
    await state.clear()


@router.message(ChatStates.waiting_manual)
async def chat_manual_message(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
        return

    data = await state.get_data()
    chat_id = data.get("chat_id")
    if not chat_id:
        await send_ephemeral_message(
            message.bot, message.chat.id, "Чат не выбран", user_id=message.from_user.id
        )
        await state.clear()
        await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
        return
    try:
        await chat_send_message(chat_id, text)
    except Exception as exc:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            f"Не удалось отправить сообщение: {exc}",
            user_id=message.from_user.id,
        )
        return

    await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
    await state.clear()
    await _open_chat_history(
        user_id=message.from_user.id,
        chat_id=chat_id,
        state=state,
        message=message,
    )


@router.message(ChatStates.waiting_ai_confirm)
async def chat_ai_message(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await state.clear()
        await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
        return

    data = await state.get_data()
    chat_id = data.get("chat_id")
    if not chat_id:
        await send_ephemeral_message(
            message.bot, message.chat.id, "Чат не выбран", user_id=message.from_user.id
        )
        await state.clear()
        await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
        return
    try:
        await chat_send_message(chat_id, text)
    except Exception as exc:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            f"Не удалось отправить сообщение: {exc}",
            user_id=message.from_user.id,
        )
        return

    await delete_section_message(message.from_user.id, SECTION_CHAT_PROMPT, message.bot, force=True)
    await state.clear()
    await _open_chat_history(
        user_id=message.from_user.id,
        chat_id=chat_id,
        state=state,
        message=message,
    )
