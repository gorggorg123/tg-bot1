import asyncio
import logging
import math
import os
import re
import textwrap
from contextlib import suppress
from datetime import datetime, timezone
from typing import Dict, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from fastapi import FastAPI
from dotenv import load_dotenv

from botapp.account import get_account_info_text
from botapp.finance import get_finance_today_text
from botapp.keyboards import (
    ChatsCallbackData,
    MenuCallbackData,
    QuestionsCallbackData,
    ReviewsCallbackData,
    account_keyboard,
    chat_actions_keyboard,
    chat_ai_confirm_keyboard,
    chats_list_keyboard,
    fbo_menu_keyboard,
    main_menu_keyboard,
    question_card_keyboard,
    questions_list_keyboard,
    review_card_keyboard,
    reviews_list_keyboard,
)
from botapp.orders import get_orders_today_text
from botapp.ozon_client import (
    OzonAPIError,
    chat_history,
    chat_list,
    chat_read,
    chat_send_message,
    get_client,
    get_write_client,
    has_write_credentials,
    get_question_by_id as api_get_question_by_id,
    get_questions_list,
    list_question_answers,
    delete_question_answer,
    send_question_answer,
)
from botapp.ai_client import (
    generate_chat_reply,
    generate_review_reply,
    generate_answer_for_question,
)
from botapp.reviews import (
    ReviewCard,
    format_review_card_text,
    get_ai_reply_for_review,
    get_review_and_card,
    get_review_by_id,
    get_review_by_index,
    get_review_view,
    get_reviews_table,
    mark_review_answered,
    refresh_review_from_api,
    encode_review_id,
    resolve_review_id,
    refresh_reviews,
    trim_for_telegram,
    build_reviews_preview,
)
from botapp.questions import (
    find_question,
    format_question_card_text,
    get_question_by_index,
    get_question_index,
    get_questions_table,
    ensure_question_answer_text,
    refresh_questions,
    register_question_token,
    resolve_question_id,
    resolve_question_token,
)
from botapp.storage import append_question_record, upsert_question_answer
from botapp.message_gc import (
    SECTION_ACCOUNT,
    SECTION_FBO,
    SECTION_FINANCE_TODAY,
    SECTION_MENU,
    SECTION_QUESTION_CARD,
    SECTION_QUESTION_PROMPT,
    SECTION_QUESTIONS_LIST,
    SECTION_REVIEW_CARD,
    SECTION_REVIEW_PROMPT,
    SECTION_REVIEWS_LIST,
    SECTION_CHAT_HISTORY,
    SECTION_CHATS_LIST,
    SECTION_CHAT_PROMPT,
    delete_message_safe,
    delete_section_message,
    send_section_message,
)
from botapp.questions import (
    format_question_card_text,
    get_question_by_index,
    get_questions_table,
    refresh_questions,
    resolve_question_id,
)

try:
    from botapp.states import QuestionAnswerStates, ChatStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

    class ChatStates(StatesGroup):
        waiting_manual = State()
        waiting_ai_confirm = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

try:
    from botapp.states import QuestionAnswerStates
except Exception:  # pragma: no cover - fallback for import issues during deploy
    class QuestionAnswerStates(StatesGroup):
        manual = State()
        reprompt = State()

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
ENABLE_TG_POLLING = os.getenv("ENABLE_TG_POLLING", "1") == "1"

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN is not set")
router = Router()
_polling_task: asyncio.Task | None = None
_polling_lock = asyncio.Lock()
_ephemeral_messages: Dict[int, Tuple[int, int, asyncio.Task]] = {}
_local_answers: Dict[Tuple[int, str], str] = {}
_local_answer_status: Dict[Tuple[int, str], str] = {}
_question_answers: Dict[Tuple[int, str], str] = {}
_question_answer_status: Dict[Tuple[int, str], str] = {}


def get_last_answer(user_id: int, review_id: str | None) -> str | None:
    """Вернуть последний сохранённый ответ (final или draft)."""

    if not review_id:
        return None
    try:
        return _local_answers.get((user_id, review_id))
    except Exception as exc:  # pragma: no cover - аварийная защита от порчи стейта
        logger.warning("Failed to read local answer for %s: %s", review_id, exc)
        return None


def get_last_question_answer(user_id: int, question_id: str | None) -> str | None:
    if not question_id:
        return None
    try:
        return _question_answers.get((user_id, question_id))
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to read local question answer for %s: %s", question_id, exc)
        return None


class ReviewAnswerStates(StatesGroup):
    reprompt = State()
    manual = State()


async def _delete_later(
    bot: Bot,
    chat_id: int,
    message_id: int,
    delay: int,
    user_id: int | None = None,
) -> None:
    try:
        await asyncio.sleep(delay)
        await delete_message_safe(bot, chat_id, message_id)
    finally:
        if user_id is not None:
            tracked = _ephemeral_messages.get(user_id)
            if tracked and tracked[1] == message_id:
                _ephemeral_messages.pop(user_id, None)


async def send_ephemeral_message(
    bot: Bot,
    chat_id: int,
    text: str,
    delay: int = 15,
    user_id: int | None = None,
    **kwargs,
    ) -> Message:
    """Отправляет служебное сообщение и удаляет его через ``delay`` секунд."""

    if user_id is not None:
        prev = _ephemeral_messages.pop(user_id, None)
        if prev:
            prev_chat_id, prev_msg_id, prev_task = prev
            if prev_task and not prev_task.done():
                prev_task.cancel()
            with suppress(Exception):
                await delete_message_safe(bot, prev_chat_id, prev_msg_id)

    msg = await bot.send_message(chat_id, text, **kwargs)
    delete_task = asyncio.create_task(
        _delete_later(bot, chat_id, msg.message_id, delay, user_id)
    )
    if user_id is not None:
        _ephemeral_messages[user_id] = (chat_id, msg.message_id, delete_task)
    return msg

    draft = answer_override or get_last_question_answer(user_id, question.id)
    text = format_question_card_text(question, answer_override=draft)
    markup = question_card_keyboard(
        category=category, page=page, question_id=question.id, can_send=True
    )

async def _clear_sections(
    bot: Bot, user_id: int, sections: list[str], *, force: bool = False
) -> None:
    for section in sections:
        await delete_section_message(user_id, section, bot, force=force)


def _remember_question_answer(user_id: int, question_id: str, text: str, status: str = "draft") -> None:
    _question_answers[(user_id, question_id)] = text
    _question_answer_status[(user_id, question_id)] = status


def _forget_question_answer(user_id: int, question_id: str) -> None:
    _question_answers.pop((user_id, question_id), None)
    _question_answer_status.pop((user_id, question_id), None)


CHAT_PAGE_SIZE = 7  # показываем чуть больше чатов на страницу
CHAT_LIST_LIMIT = 100  # верхняя граница одной выгрузки списка чатов


def _truncate_text(text: str, limit: int = 80) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _ts(msg: dict) -> str:
    """Достать временную метку сообщения как строку."""

    if not isinstance(msg, dict):
        return ""
    for key in ("created_at", "send_time"):
        value = msg.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, (int, float)):
            # В некоторых payload timestamp приходит числом
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


def _parse_ts(ts_value: str | None) -> datetime | None:
    if not ts_value:
        return None
    try:
        normalized = ts_value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except Exception:
        return None


def _extract_text(msg: dict) -> str | None:
    """Достать текст сообщения из разных вложенных структур Ozon."""

    PREFERRED_KEYS = (
        "text",
        "message",
        "content",
        "body",
        "value",
        "text_html",
        "textHtml",
    )

    def _is_timestamp(s: str) -> bool:
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:", s.strip()))

    def _pick_from_dict(d: dict) -> str | None:
        for key in PREFERRED_KEYS:
            if key not in d:
                continue
            value = d.get(key)
            if isinstance(value, str):
                value_str = value.strip()
                if value_str and not _is_timestamp(value_str):
                    return value_str
            if isinstance(value, dict):
                for k2 in PREFERRED_KEYS:
                    inner = value.get(k2)
                    if isinstance(inner, str):
                        inner_str = inner.strip()
                        if inner_str and not _is_timestamp(inner_str):
                            return inner_str
        # Спец-обработка сообщений Ozon, где текст лежит в массиве "data"
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

    if buyer:
        title = buyer
        if posting:
            title = f"{buyer} • заказ {posting}"
    elif posting:
        title = f"Заказ {posting}"
        if last_label:
            title = f"{title} • {last_label}"
    else:
        if last_label and msg_count:
            title = f"Чат от {last_label} • {msg_count} сообщений"
        elif last_label:
            title = f"Чат от {last_label}"
        elif msg_count:
            title = f"Чат • {msg_count} сообщений"
        else:
            title = "Чат без названия"

    short_title = _truncate_text(title, limit=24)
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

    cached_list = data.get("chats_all") if isinstance(data.get("chats_all"), list) else None
    need_reload = refresh or not isinstance(cached_list, list) or not cached_list

    try:
        items_raw = await chat_list(limit=CHAT_LIST_LIMIT, offset=0) if need_reload else cached_list or []
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

    filtered_items = [chat for chat in sorted_items if not unread_flag or _chat_unread_count(chat) > 0]
    total_count = len(sorted_items)
    unread_total = sum(1 for chat in sorted_items if _chat_unread_count(chat) > 0)
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
        line_parts = [f"{idx}) {title}"]
        if unread_count > 0:
            line_parts.append(f"🔴 {unread_count} непрочитанных")
        if last_dt:
            line_parts.append(last_dt.strftime("%d.%m %H:%M"))
        if preview:
            line_parts.append(f"\"{preview}\"")
        display_rows.append(" • ".join(line_parts))
        keyboard_items.append((chat_id_val, f"{idx}. {short_title}"))

    lines = [
        "🗨️ Активные чаты с покупателями",
    ]
    lines.append(f"Всего чатов: {total_count}, непрочитанных: {unread_total}.")
    lines.append(
        "Показываю только диалоги с покупателями. Служебные уведомления и чаты с поддержкой Ozon скрыты."
    )
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


def _format_chat_history_text(chat_meta: dict | None, messages: list[dict], *, limit: int = 30) -> str:
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
            # Сервисные сообщения не показываем, чтобы не шуметь
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

    history_text = _format_chat_history_text(chat_meta, messages)
    markup = chat_actions_keyboard(chat_id)
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id_override or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    await state.update_data(chat_history=messages, current_chat_id=chat_id)
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


async def _send_reviews_list(
    *,
    user_id: int,
    category: str,
    page: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> None:
    text, items, safe_page, total_pages = await get_reviews_table(
        user_id=user_id, category=category, page=page
    )
    markup = reviews_list_keyboard(
        category=category, page=safe_page, total_pages=total_pages, items=items
    )
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    sent = await send_section_message(
        SECTION_REVIEWS_LIST,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
    )
    await delete_section_message(
        user_id,
        SECTION_REVIEW_CARD,
        active_bot,
        preserve_message_id=sent.message_id if sent else None,
    )


async def _send_questions_list(
    *,
    user_id: int,
    category: str,
    page: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    bot: Bot | None = None,
    chat_id: int | None = None,
) -> None:
    try:
        text, items, safe_page, total_pages = await get_questions_table(
            user_id=user_id, category=category, page=page
        )
    except OzonAPIError as exc:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                f"⚠️ Не удалось получить список вопросов. Ошибка: {exc}",
                user_id=user_id,
            )
        logger.warning("Unable to load questions list: %s", exc)
        return
    except Exception:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "⚠️ Не удалось получить список вопросов. Попробуйте позже.",
                user_id=user_id,
            )
        logger.exception("Unexpected error while loading questions list")
        return
    markup = questions_list_keyboard(
        user_id=user_id,
        category=category,
        page=safe_page,
        total_pages=total_pages,
        items=items,
    )
    target = callback.message if callback else message
    active_bot = bot or (target.bot if target else None)
    active_chat = chat_id or (target.chat.id if target else None)
    if not active_bot or active_chat is None:
        return

    sent = await send_section_message(
        SECTION_QUESTIONS_LIST,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=bot,
        chat_id=chat_id,
        user_id=user_id,
    )
    await delete_section_message(
        user_id,
        SECTION_QUESTION_CARD,
        active_bot,
        preserve_message_id=sent.message_id if sent else None,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    text = (
        "Привет! Я помогу быстро смотреть финансы, заказы и отзывы Ozon.\n"
        "Выберите раздел через кнопки ниже."
    )
    await state.clear()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_MENU,
        text=text,
        reply_markup=main_menu_keyboard(),
        message=message,
    )


@router.message(Command("fin_today"))
async def cmd_fin_today(message: Message, state: FSMContext) -> None:
    text = await get_finance_today_text()
    await state.clear()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_FINANCE_TODAY,
        text=text,
        reply_markup=main_menu_keyboard(),
        message=message,
    )


@router.message(Command("account"))
async def cmd_account(message: Message, state: FSMContext) -> None:
    text = await get_account_info_text()
    await state.clear()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_ACCOUNT,
        text=text,
        reply_markup=account_keyboard(),
        message=message,
    )


@router.message(Command("fbo"))
async def cmd_fbo(message: Message, state: FSMContext) -> None:
    text = await get_orders_today_text()
    await state.clear()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_FBO,
        text=text,
        reply_markup=fbo_menu_keyboard(),
        message=message,
    )


@router.message(Command("reviews"))
async def cmd_reviews(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await state.clear()
    await _clear_sections(
        message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await refresh_reviews(user_id)
    await _send_reviews_list(
        user_id=user_id,
        category="all",
        page=0,
        message=message,
        bot=message.bot,
        chat_id=message.chat.id,
    )


@router.message(Command("questions"))
async def cmd_questions(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    await state.clear()
    await _clear_sections(
        message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await refresh_questions(user_id)
    await _send_questions_list(
        user_id=user_id,
        category="unanswered",
        page=0,
        message=message,
        bot=message.bot,
        chat_id=message.chat.id,
    )


async def _send_review_card(
    *,
    user_id: int,
    category: str,
    index: int = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    review_id: str | None = None,
    page: int = 0,
    answer_override: str | None = None,
) -> None:
    view, card = await get_review_and_card(user_id, category, index, review_id=review_id)
    if view.total == 0 or not card:
        text = trim_for_telegram(view.text)
        markup = main_menu_keyboard()
    else:
        client = get_client()
        if client:
            await refresh_review_from_api(card, client)
        current_answer = answer_override or await _get_local_answer(user_id, card.id)
        text = format_review_card_text(
            card=card,
            index=view.index,
            total=view.total,
            period_title=view.period,
            user_id=user_id,
            current_answer=current_answer,
        )
        markup = review_card_keyboard(
            category=category,
            index=view.index,
            review_id=encode_review_id(user_id, card.id),
            can_send=has_write_credentials(),
            page=page,
        )

    target = callback.message if callback else message

    active_bot = None
    active_chat = None
    if target:
        active_bot = target.bot
        active_chat = target.chat.id
    elif callback and callback.message:
        active_bot = callback.message.bot
        active_chat = callback.message.chat.id

    if not active_bot or active_chat is None:
        return

    await send_section_message(
        SECTION_REVIEW_CARD,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        bot=active_bot,
        chat_id=active_chat,
        user_id=user_id,
    )


async def _get_local_answer(user_id: int, review_id: str | None) -> str | None:
    if not review_id:
        return None
    return get_last_answer(user_id, review_id)


async def _remember_local_answer(user_id: int, review_id: str | None, text: str) -> None:
    if not review_id:
        return
    _local_answers[(user_id, review_id)] = text
    _local_answer_status[(user_id, review_id)] = "draft"


async def _handle_ai_reply(
    *,
    callback: CallbackQuery | Message,
    category: str,
    page: int,
    review: ReviewCard | None,
    index: int = 0,
    user_prompt: str | None = None,
) -> None:
    if not review:
        target = callback.message if isinstance(callback, CallbackQuery) else callback
        await target.answer("Свежих отзывов нет.")
        return

    user_id = callback.from_user.id if isinstance(callback, CallbackQuery) else callback.from_user.id
    target = callback.message if isinstance(callback, CallbackQuery) else callback

    current_answer = await _get_local_answer(user_id, review.id)
    draft = await generate_review_reply(
        review_text=review.text,
        product_name=review.product_name,
        rating=review.rating,
        previous_answer=current_answer,
        user_prompt=user_prompt,
    )

    if not draft:
        await target.answer("⚠️ Не удалось получить ответ от ИИ")
        return

    final_answer = draft
    await _remember_local_answer(user_id, review.id, final_answer)
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=index,
        callback=callback if isinstance(callback, CallbackQuery) else None,
        message=target if isinstance(target, Message) else None,
        review_id=review.id,
        page=page,
        answer_override=final_answer,
    )


async def _send_question_card(
    *,
    user_id: int,
    category: str,
    index: int | None = 0,
    message: Message | None = None,
    callback: CallbackQuery | None = None,
    page: int = 0,
    token: str | None = None,
    question=None,
    answer_override: str | None = None,
) -> None:
    resolved_question = question
    if resolved_question is None:
        if token:
            resolved_question = resolve_question_token(user_id, token)
        if resolved_question is None and index is not None:
            resolved_question = get_question_by_index(user_id, category, index)

    if resolved_question is None:
        target = callback.message if callback else message
        if target:
            await send_ephemeral_message(
                target.bot,
                target.chat.id,
                "Не удалось найти этот вопрос. Обновите список и попробуйте ещё раз.",
                user_id=user_id,
            )
        return

    effective_token = token
    if not effective_token:
        idx = get_question_index(user_id, category, resolved_question.id)
        if idx is None:
            idx = get_question_index(user_id, "all", resolved_question.id)
            if idx is not None:
                category = "all"
        if idx is not None:
            effective_token = register_question_token(
                user_id=user_id, category=category, index=idx
            )

    await ensure_question_answer_text(resolved_question)

    text = format_question_card_text(resolved_question, answer_override=answer_override)
    markup = question_card_keyboard(
        category=category,
        page=page,
        token=effective_token,
        can_send=True,
        has_answer=getattr(resolved_question, "has_answer", False),
    )
    await send_section_message(
        SECTION_QUESTION_CARD,
        text=text,
        reply_markup=markup,
        message=message,
        callback=callback,
        user_id=user_id,
    )
    # Сохраняем карточку вопроса без удаления исходного списка, чтобы экран не исчезал.


@router.callback_query(MenuCallbackData.filter(F.section == "home"))
async def cb_home(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    await state.clear()
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_MENU,
        text="Главное меню",
        reply_markup=main_menu_keyboard(),
        callback=callback,
        user_id=user_id,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fbo"))
async def cb_fbo(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    action = callback_data.action
    user_id = callback.from_user.id
    await state.clear()
    if action == "summary":
        text = await get_orders_today_text()
        await send_section_message(
            SECTION_FBO,
            text=text,
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "month":
        await send_section_message(
            SECTION_FBO,
            text="Месячная сводка пока в разработке, покажем как только будет готово.",
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "filter":
        await send_section_message(
            SECTION_FBO,
            text="Фильтр скоро",
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "open":
        text = await get_orders_today_text()
        await send_section_message(
            SECTION_FBO,
            text=text,
            reply_markup=fbo_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )
    elif action == "home":
        await _clear_sections(
            callback.message.bot,
            user_id,
            [
                SECTION_FBO,
                SECTION_FINANCE_TODAY,
                SECTION_ACCOUNT,
                SECTION_REVIEWS_LIST,
                SECTION_REVIEW_CARD,
                SECTION_QUESTIONS_LIST,
                SECTION_QUESTION_CARD,
                SECTION_REVIEW_PROMPT,
                SECTION_QUESTION_PROMPT,
                SECTION_CHATS_LIST,
                SECTION_CHAT_HISTORY,
                SECTION_CHAT_PROMPT,
            ],
            force=True,
        )
        await send_section_message(
            SECTION_MENU,
            text="Главное меню",
            reply_markup=main_menu_keyboard(),
            callback=callback,
            user_id=user_id,
        )


@router.callback_query(MenuCallbackData.filter(F.section == "account"))
async def cb_account(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    text = await get_account_info_text()
    user_id = callback.from_user.id
    await state.clear()
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_ACCOUNT,
        text=text,
        reply_markup=account_keyboard(),
        callback=callback,
        user_id=user_id,
    )


@router.callback_query(MenuCallbackData.filter(F.section == "fin_today"))
async def cb_fin_today(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    text = await get_finance_today_text()
    user_id = callback.from_user.id
    await state.clear()
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_FINANCE_TODAY,
        text=text,
        reply_markup=main_menu_keyboard(),
        callback=callback,
        user_id=user_id,
    )


@router.callback_query(ReviewsCallbackData.filter())
async def cb_reviews(callback: CallbackQuery, callback_data: ReviewsCallbackData, state: FSMContext) -> None:
    action = callback_data.action
    category = callback_data.category or "unanswered"
    index = callback_data.index or 0
    user_id = callback.from_user.id
    review_token = callback_data.review_id
    review_id = resolve_review_id(user_id, review_token)
    page = callback_data.page or 0

    if action in {"list", "list_page"}:
        await callback.answer()
        if action == "list":
            await refresh_reviews(user_id)
        await _send_reviews_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
        )
        await delete_section_message(user_id, SECTION_REVIEW_CARD, callback.message.bot)
        return

    if action == "open_card":
        await callback.answer()
        await _send_review_card(
            user_id=user_id,
            category=category,
            index=index,
            callback=callback,
            review_id=review_id,
            page=page,
        )
        return

    if action == "list_page":
        await callback.answer()
        await _send_reviews_list(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
        )
        return

    if action == "card_ai":
        await callback.answer("Готовим ответ…", show_alert=False)
        review, new_index = await get_review_by_id(user_id, category, review_id)
        await _handle_ai_reply(
            callback=callback,
            category=category,
            page=page,
            review=review,
            index=new_index or 0,
        )
        return

    if action == "card_reprompt":
        await callback.answer()
        prompt = await send_section_message(
            SECTION_REVIEW_PROMPT,
            text="Напишите свои пожелания к ответу, я пересоберу текст.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(ReviewAnswerStates.reprompt)
        await state.update_data(
            review_id=review_id, category=category, page=page, prompt_message_id=prompt.message_id
        )
        return

    if action == "card_manual":
        await callback.answer()
        prompt = await send_section_message(
            SECTION_REVIEW_PROMPT,
            text="Пришлите текст ответа, я сохраню его как текущий.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(ReviewAnswerStates.manual)
        await state.update_data(
            review_id=review_id, category=category, page=page, prompt_message_id=prompt.message_id
        )
        return

    if action == "send":
        await callback.answer()
        if not review_id:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось определить ID отзыва, попробуйте обновить список.",
                user_id=user_id,
            )
            return

        review, _ = await get_review_by_id(user_id, category, review_id)
        if not review:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Отзыв не найден, обновите список.",
                user_id=user_id,
            )
            return

        final_answer = await _get_local_answer(user_id, review.id)
        if not final_answer:
            final_answer = review.answer_text
        if final_answer:
            final_answer = final_answer.strip()

        if not final_answer:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Нет сохранённого текста ответа. Сначала сгенерируйте или введите ответ.",
                user_id=user_id,
            )
            return

        client = get_write_client()
        if not client:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Отправка на Ozon недоступна: не задан OZON_API_KEY.",
                user_id=user_id,
            )
            return

        try:
            await client.create_review_comment(review.id, final_answer)
        except Exception as exc:
            logger.warning("Failed to send review %s to Ozon: %s", review.id, exc)
            _local_answers[(user_id, review.id)] = final_answer
            _local_answer_status[(user_id, review.id)] = "error"
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось отправить ответ в Ozon. Проверьте права API‑ключа OZON_API_KEY в личном кабинете Ozon или попробуйте позже.",
                user_id=user_id,
            )
            return

        _local_answers[(user_id, review.id)] = final_answer
        _local_answer_status[(user_id, review.id)] = "sent"
        mark_review_answered(review.id, user_id, final_answer)
        await refresh_reviews(user_id)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ отправлен в Ozon ✅",
            user_id=user_id,
        )
        await _send_review_card(
            user_id=user_id,
            category=category,
            index=index,
            callback=callback,
            review_id=review_id,
            page=page,
            answer_override=final_answer,
        )
        return

    # fallback для неизвестных сообщений
    await callback.message.answer(
        "Выберите действие в меню ниже", reply_markup=main_menu_keyboard()
    )


@router.callback_query(QuestionsCallbackData.filter())
async def cb_questions(callback: CallbackQuery, callback_data: QuestionsCallbackData, state: FSMContext) -> None:
    user_id = callback.from_user.id
    action = callback_data.action
    category = callback_data.category or "all"
    page = int(callback_data.page or 0)
    token = callback_data.token

    def _resolve_question(
        *, token_value: str | None = None, legacy_data: dict | None = None
    ):
        question = resolve_question_token(user_id, token_value) if token_value else None
        if question:
            return question

        legacy = legacy_data or {}
        q_id = legacy.get("question_id") or legacy.get("id")
        if q_id:
            return resolve_question_id(user_id, q_id)

        idx = legacy.get("index") or legacy.get("question_index")
        if idx is not None:
            try:
                idx_int = int(idx)
            except Exception:
                idx_int = None
            if idx_int is not None:
                cat = legacy.get("category") or category
                return get_question_by_index(user_id, cat, idx_int)
        return None

    if action == "list":
        await callback.answer()
        try:
            await refresh_questions(user_id, category)
        except OzonAPIError as exc:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                f"⚠️ Не удалось получить список вопросов. Ошибка: {exc}",
                user_id=user_id,
            )
            return
        except Exception:
            logger.exception("Unexpected error while refreshing questions")
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "⚠️ Не удалось получить список вопросов. Попробуйте позже.",
                user_id=user_id,
            )
            return

        await _send_questions_list(
            user_id=user_id, category=category, page=page, callback=callback
        )
        return

    if action in {"list_page", "page"}:
        await callback.answer()
        await _send_questions_list(
            user_id=user_id, category=category, page=page, callback=callback
        )
        return

    if action in {"open", "open_card"}:
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await callback.answer(
                "Не удалось найти этот вопрос. Обновите список и попробуйте ещё раз.",
                show_alert=True,
            )
            return

        idx = get_question_index(user_id, category, question.id)
        effective_token = token
        effective_category = category
        if idx is None:
            idx = get_question_index(user_id, "all", question.id)
            if idx is not None:
                effective_category = "all"
        if idx is not None and not effective_token:
            effective_token = register_question_token(
                user_id=user_id, category=effective_category, index=idx
            )

        await callback.answer()
        await _send_question_card(
            user_id=user_id,
            category=effective_category,
            index=idx,
            callback=callback,
            page=page,
            token=effective_token,
            question=question,
        )
        return

    if action == "prefill":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для обновления ответа.",
                user_id=user_id,
            )
            return

        answer_text = question.answer_text
        if not (answer_text or "").strip():
            try:
                answers = await list_question_answers(
                    question.id, limit=1, sku=getattr(question, "sku", None)
                )
                if answers:
                    question.answer_text = answers[0].text or question.answer_text
                    question.answer_id = answers[0].id or question.answer_id
                    question.has_answer = bool(question.answer_text)
                    answer_text = question.answer_text
            except Exception as exc:
                logger.warning("Failed to load current answer for %s: %s", question.id, exc)

        if not (answer_text or "").strip():
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Ответ для этого вопроса в Ozon не найден.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, answer_text, status="existing")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=answer_text,
            answer_source="existing",
            answer_sent_to_ozon=True,
        )
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Текущий ответ подставлен в черновик, можно отредактировать и отправить.",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=answer_text,
            token=token,
            question=question,
        )
        return

    if action == "delete":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Вопрос не найден. Обновите список и попробуйте снова.",
                user_id=user_id,
            )
            return

        answer_id = getattr(question, "answer_id", None)
        if not answer_id:
            try:
                answers = await list_question_answers(
                    question.id, limit=1, sku=getattr(question, "sku", None)
                )
                if answers:
                    answer_id = answers[0].id
                    question.answer_id = answer_id
                    question.answer_text = answers[0].text or question.answer_text
                    question.has_answer = bool(question.answer_text)
            except Exception as exc:
                logger.warning("Failed to fetch answers before delete %s: %s", question.id, exc)

        if not answer_id:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не нашли ответ, который можно удалить.",
                user_id=user_id,
            )
            return

        try:
            await delete_question_answer(question.id, answer_id=answer_id)
        except OzonAPIError as exc:
            logger.warning("Failed to delete question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                str(exc),
                user_id=user_id,
            )
            return
        except Exception as exc:
            logger.warning("Failed to delete question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось удалить ответ, попробуйте позже.",
                user_id=user_id,
            )
            return

        _forget_question_answer(user_id, question.id)
        question.answer_text = None
        question.answer_id = None
        question.has_answer = False
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=None,
            answer_source="deleted",
            answer_sent_to_ozon=False,
        )
        await refresh_questions(user_id, category)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ удалён в Ozon.",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            token=token,
            question=question,
            answer_override=None,
        )
        return

    if action == "card_ai":
        question = _resolve_question(
            token_value=token, legacy_data=callback_data.model_dump()
        )
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для генерации ответа.",
                user_id=user_id,
            )
            return
        ai_answer = await generate_answer_for_question(
            question_text=question.question_text,
            product_name=question.product_name,
            existing_answer=question.answer_text
            or get_last_question_answer(user_id, question.id),
        )
        if not ai_answer:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось сгенерировать ответ, попробуйте ещё раз позже.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, ai_answer, status="ai")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=ai_answer,
            answer_source="ai",
            answer_sent_to_ozon=False,
            meta={"chat_id": callback.message.chat.id, "message_id": callback.message.message_id},
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=ai_answer,
            token=token,
            question=question,
        )
        return

    if action == "card_manual":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для подготовки ответа.",
                user_id=user_id,
            )
            return
        prompt = await send_section_message(
            SECTION_QUESTION_PROMPT,
            text="Пришлите текст ответа для покупателя.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(QuestionAnswerStates.manual)
        await state.update_data(
            question_token=token,
            question_id=question.id,
            category=category,
            page=page,
            prompt_message_id=prompt.message_id,
        )
        return

    if action == "card_reprompt":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось найти вопрос для пересборки ответа.",
                user_id=user_id,
            )
            return
        prompt = await send_section_message(
            SECTION_QUESTION_PROMPT,
            text="Опишите, что изменить или добавить к ответу.",
            bot=callback.message.bot,
            chat_id=callback.message.chat.id,
            user_id=user_id,
            persistent=True,
        )
        await state.set_state(QuestionAnswerStates.reprompt)
        await state.update_data(
            question_token=token,
            question_id=question.id,
            category=category,
            page=page,
            prompt_message_id=prompt.message_id,
        )
        return

    if action == "send":
        question = _resolve_question(token_value=token, legacy_data=callback_data.model_dump())
        if question is None:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Вопрос не найден. Обновите список и попробуйте снова.",
                user_id=user_id,
            )
            return

        answer = get_last_question_answer(user_id, question.id) or question.answer_text
        answer_clean = (answer or "").strip()
        if len(answer_clean) < 2:
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Ответ пустой или слишком короткий, сначала отредактируйте текст.",
                user_id=user_id,
            )
            return
        try:
            await send_question_answer(question.id, answer_clean, sku=question.sku)
        except OzonAPIError as exc:
            logger.warning("Failed to send question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                str(exc),
                user_id=user_id,
            )
            return
        except Exception as exc:
            logger.warning("Failed to send question answer %s: %s", question.id, exc)
            await send_ephemeral_message(
                callback.message.bot,
                callback.message.chat.id,
                "Не удалось отправить ответ в Ozon. Проверьте права API‑ключа OZON_API_KEY.",
                user_id=user_id,
            )
            return

        _remember_question_answer(user_id, question.id, answer_clean, status="sent")
        upsert_question_answer(
            question_id=question.id,
            created_at=question.created_at,
            sku=question.sku,
            product_name=question.product_name,
            question=question.question_text,
            answer=answer_clean,
            answer_source=_question_answer_status.get((user_id, question.id), "manual"),
            answer_sent_to_ozon=True,
            answer_sent_at=datetime.now(timezone.utc).isoformat(),
            meta={"chat_id": callback.message.chat.id, "message_id": callback.message.message_id},
        )
        question.has_answer = True
        question.answer_text = answer_clean
        await refresh_questions(user_id, category)
        await send_ephemeral_message(
            callback.message.bot,
            callback.message.chat.id,
            "Ответ отправлен в Ozon ✅",
            user_id=user_id,
        )
        await _send_question_card(
            user_id=user_id,
            category=category,
            page=page,
            callback=callback,
            answer_override=answer,
            token=token,
            question=question,
        )
        return

    await callback.message.answer(
        "Выберите действие в меню ниже", reply_markup=main_menu_keyboard()
    )


@router.message(ReviewAnswerStates.reprompt)
async def handle_reprompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text_payload = (message.text or message.caption or "").strip()
    if not text_payload:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    review, resolved_index = await get_review_by_id(user_id, category, review_id)
    if not review:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти отзыв для пересборки.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
        await state.clear()
        return

    await _handle_ai_reply(
        callback=message,  # type: ignore[arg-type]
        category=category,
        page=page,
        review=review,
        index=resolved_index or 0,
        user_prompt=text_payload,
    )
    await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
    await state.clear()


@router.message(ReviewAnswerStates.manual)
async def handle_manual_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    review_id = data.get("review_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text = (message.text or message.caption or "").strip()
    if not text:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    await delete_section_message(user_id, SECTION_REVIEW_PROMPT, message.bot, force=True)
    await state.clear()
    await _remember_local_answer(user_id, review_id, text)
    await _send_review_card(
        user_id=user_id,
        category=category,
        index=0,
        message=message,
        review_id=review_id,
        page=page,
        answer_override=text,
    )


@router.message(QuestionAnswerStates.reprompt)
async def handle_question_reprompt(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_token = data.get("question_token")
    question_id = data.get("question_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text_payload = (message.text or message.caption or "").strip()
    if not text_payload:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    question = resolve_question_token(user_id, question_token) if question_token else None
    if question is None and question_id:
        question = find_question(user_id, question_id) or await api_get_question_by_id(
            question_id
        )
    if not question:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти вопрос для пересборки.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
        await state.clear()
        return

    previous = get_last_question_answer(user_id, question.id) or question.answer_text
    prompt = text_payload
    ai_answer = await generate_answer_for_question(
        question_text=question.question_text,
        product_name=question.product_name,
        existing_answer=previous,
        user_prompt=prompt,
    )
    _remember_question_answer(user_id, question.id, ai_answer, status="ai_edited")
    upsert_question_answer(
        question_id=question.id,
        created_at=question.created_at,
        sku=question.sku,
        product_name=question.product_name,
        question=question.question_text,
        answer=ai_answer,
        answer_source="ai_edited",
        answer_sent_to_ozon=False,
        meta={"chat_id": message.chat.id, "message_id": message.message_id},
    )
    await _send_question_card(
        user_id=user_id,
        category=category,
        page=page,
        message=message,
        answer_override=ai_answer,
        token=question_token,
        question=question,
    )
    await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
    await state.clear()


# ---------------------------------------------------------------------------
# Чаты с покупателями
# ---------------------------------------------------------------------------


@router.callback_query(MenuCallbackData.filter(F.section == "chats"))
async def cb_chats_menu(
    callback: CallbackQuery, callback_data: MenuCallbackData, state: FSMContext
) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    await state.clear()
    await _clear_sections(
        callback.message.bot,
        user_id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
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
    await state.clear()
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


@router.callback_query(ChatsCallbackData.filter(F.action == "manual"))
async def cb_chat_manual(
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


def _split_messages_by_role(messages: list[dict]) -> tuple[list[str], list[str]]:
    customer: list[str] = []
    seller: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = _extract_text(msg)
        if not text:
            continue

        role_lower = _detect_message_role(msg)
        if "crm" in role_lower or "support" in role_lower:
            # Сервисные сообщения не используем в промптах
            continue

        if "seller" in role_lower or "operator" in role_lower or "store" in role_lower:
            seller.append(str(text))
        else:
            customer.append(str(text))
    return customer, seller


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
    customer_msgs, seller_msgs = _split_messages_by_role(messages[-10:])

    draft = await generate_chat_reply(
        customer_messages=customer_msgs,
        seller_messages=seller_msgs,
        product_name=meta.get("product_name") if isinstance(meta, dict) else None,
    )

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


@router.message(QuestionAnswerStates.manual)
async def handle_question_manual(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question_token = data.get("question_token")
    question_id = data.get("question_id")
    category = data.get("category") or "all"
    page = int(data.get("page") or 0)
    user_id = message.from_user.id

    text = (message.text or message.caption or "").strip()
    if not text:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Ответ пустой, пришлите текст.",
            user_id=user_id,
        )
        return

    question = resolve_question_token(user_id, question_token) if question_token else None
    if question is None and question_id:
        question = find_question(user_id, question_id) or await api_get_question_by_id(
            question_id
        )
    if not question:
        await send_ephemeral_message(
            message.bot,
            message.chat.id,
            "Не удалось найти вопрос.",
            user_id=user_id,
        )
        await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
        await state.clear()
        return

    _remember_question_answer(user_id, question.id, text, status="manual")
    upsert_question_answer(
        question_id=question.id,
        created_at=question.created_at,
        sku=question.sku,
        product_name=question.product_name,
        question=question.question_text,
        answer=text,
        answer_source="manual",
        answer_sent_to_ozon=False,
        meta={"chat_id": message.chat.id, "message_id": message.message_id},
    )
    await _send_question_card(
        user_id=user_id,
        category=category,
        page=page,
        message=message,
        answer_override=text,
        token=question_token,
        question=question,
    )
    await delete_section_message(user_id, SECTION_QUESTION_PROMPT, message.bot, force=True)
    await state.clear()


@router.message()
async def handle_any(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _clear_sections(
        message.bot,
        message.from_user.id,
        [
            SECTION_FBO,
            SECTION_FINANCE_TODAY,
            SECTION_ACCOUNT,
            SECTION_REVIEWS_LIST,
            SECTION_REVIEW_CARD,
            SECTION_QUESTIONS_LIST,
            SECTION_QUESTION_CARD,
            SECTION_REVIEW_PROMPT,
            SECTION_QUESTION_PROMPT,
            SECTION_CHATS_LIST,
            SECTION_CHAT_HISTORY,
            SECTION_CHAT_PROMPT,
        ],
        force=True,
    )
    await send_section_message(
        SECTION_MENU,
        text="Выберите действие в меню ниже",
        reply_markup=main_menu_keyboard(),
        message=message,
    )


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


bot = Bot(
    token=TG_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = build_dispatcher()
app = FastAPI()


async def start_bot() -> None:
    """Стартуем polling один раз за процесс."""

    global _polling_task
    async with _polling_lock:
        if _polling_task and not _polling_task.done():
            logger.info("Polling уже запущен, повторно не стартуем")
            return
        if _polling_task and _polling_task.done():
            _polling_task = None

        # На Render запускается только один web-инстанс с ENABLE_TG_POLLING=1.
        # Второй инстанс/воркер должен выставлять ENABLE_TG_POLLING=0, иначе Telegram
        # вернёт TelegramConflictError из-за параллельного polling.
        logger.info("Telegram bot polling started (single instance)")
        _polling_task = asyncio.create_task(
            dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
            )
        )

    try:
        await _polling_task
    except asyncio.CancelledError:
        logger.info("Polling task cancelled, shutting down")
        raise


@app.on_event("startup")
async def on_startup() -> None:
    logger.info("Startup: validating Ozon credentials")
    get_client()

    if not ENABLE_TG_POLLING:
        # Локально ставим ENABLE_TG_POLLING=0, чтобы не лезть в Telegram,
        # пока прод на Render работает с ENABLE_TG_POLLING=1.
        logger.info("Telegram polling is disabled by ENABLE_TG_POLLING=0")
        return

    logger.info("Startup: creating polling task")
    asyncio.create_task(start_bot())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("Shutdown: closing Ozon client and bot")
    try:
        client = get_client()
    except Exception:
        client = None
    if client:
        await client.aclose()
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await _polling_task
    await bot.session.close()


@app.get("/")
async def root() -> dict:
    return {"status": "ok", "detail": "Ozon bot is running"}


@app.get("/reviews")
async def reviews(days: int = 30) -> dict:
    """HTTP-эндпоинт для выборки отзывов и названий товаров по SKU."""

    return await build_reviews_preview(days=days)


# Summary of latest changes:
# - Added a safe fallback for QuestionAnswerStates import to prevent FSM NameErrors on deploy.
# - Kept question list handling on Ozon-approved statuses with Pydantic parsing and user-facing warnings.

__all__ = ["app", "bot", "dp", "router"]
