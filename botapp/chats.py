# botapp/chats.py
from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from botapp.ozon_client import ChatHistoryResponse, ChatListItem, chat_history as ozon_chat_history, chat_list as ozon_chat_list

logger = logging.getLogger(__name__)

PAGE_SIZE = 10
CACHE_TTL_SECONDS = 30
THREAD_TTL_SECONDS = 15

DEFAULT_THREAD_LIMIT = 30
MAX_THREAD_LIMIT = 180


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cache_fresh(dt: datetime | None, ttl: int) -> bool:
    if not dt:
        return False
    return (_now_utc() - dt) <= timedelta(seconds=int(ttl))


def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _escape(s: str) -> str:
    return html.escape((s or "").strip())


def _short_token(user_id: int, chat_id: str) -> str:
    return hashlib.blake2s(f"{user_id}:c:{chat_id}".encode("utf-8"), digest_size=8).hexdigest()


@dataclass(slots=True)
class ChatsCache:
    fetched_at: datetime | None = None
    chats: list[ChatListItem] = field(default_factory=list)
    token_to_chat_id: dict[str, str] = field(default_factory=dict)
    chat_id_to_token: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ThreadCache:
    fetched_at: datetime | None = None
    limit: int = DEFAULT_THREAD_LIMIT
    raw_messages: list[dict] = field(default_factory=list)
    last_message_id: int | None = None


@dataclass(slots=True)
class ThreadView:
    chat_id: str
    raw_messages: list[dict]
    fetched_at: datetime | None
    limit: int
    last_message_id: int | None = None


@dataclass(slots=True)
class NormalizedMessage:
    role: str  # buyer | seller | ozon
    text: str
    created_at: str | None = None
    raw: dict | None = None


def _classify_chat(chat: dict) -> tuple[bool, bool]:
    """Определить, является ли чат покупательским.

    Возвращает кортеж (is_buyer, recognized), где recognized=True означает, что
    решение было принято по явным правилам, а False — это консервативный фолбэк.
    """

    if not isinstance(chat, dict):
        return False, False

    raw = chat.get("_raw") if isinstance(chat.get("_raw"), dict) else {}

    # 1) Явные служебные типы
    chat_type = raw.get("chat_type") or raw.get("type")
    if isinstance(chat_type, str) and chat_type.lower() in {"support", "ozon_support", "service"}:
        return False, True

    # 2) Явные признаки покупателя
    buyer_block = chat.get("buyer") if isinstance(chat.get("buyer"), dict) else None
    if not buyer_block and isinstance(raw.get("buyer"), dict):
        buyer_block = raw["buyer"]
    if buyer_block and buyer_block.get("name"):
        return True, True

    for key in ("posting_number", "order_id", "buyer_id", "customer_id"):
        value = chat.get(key)
        if value:
            return True, True
        raw_value = raw.get(key)
        if raw_value:
            return True, True

    # 3) Ничего не нашли — консервативно False
    return False, False


def _classify_chat(chat: dict) -> tuple[bool, bool]:
    """Определить, является ли чат покупательским.

    Возвращает кортеж (is_buyer, recognized), где recognized=True означает, что
    решение было принято по явным правилам, а False — это консервативный фолбэк.
    """

    if not isinstance(chat, dict):
        return False, False

    raw = chat.get("_raw") if isinstance(chat.get("_raw"), dict) else {}

    # 1) Явные служебные типы
    chat_type = raw.get("chat_type") or raw.get("type")
    if isinstance(chat_type, str) and chat_type.lower() in {"support", "ozon_support", "service"}:
        return False, True

    # 2) Явные признаки покупателя
    buyer_block = chat.get("buyer") if isinstance(chat.get("buyer"), dict) else None
    if not buyer_block and isinstance(raw.get("buyer"), dict):
        buyer_block = raw["buyer"]
    if buyer_block and buyer_block.get("name"):
        return True, True

    for key in ("posting_number", "order_id", "buyer_id", "customer_id"):
        value = chat.get(key)
        if value:
            return True, True
        raw_value = raw.get(key)
        if raw_value:
            return True, True

    # 3) Ничего не нашли — консервативно False
    return False, False


def _classify_chat(chat: dict) -> tuple[bool, bool]:
    """Определить, является ли чат покупательским.

    Возвращает кортеж (is_buyer, recognized), где recognized=True означает, что
    решение было принято по явным правилам, а False — это консервативный фолбэк.
    """

    if not isinstance(chat, dict):
        return False, False

    raw = chat.get("_raw") if isinstance(chat.get("_raw"), dict) else {}

    # 1) Явные служебные типы
    chat_type = raw.get("chat_type") or raw.get("type")
    if isinstance(chat_type, str) and chat_type.lower() in {"support", "ozon_support", "service"}:
        return False, True

    # 2) Явные признаки покупателя
    buyer_block = chat.get("buyer") if isinstance(chat.get("buyer"), dict) else None
    if not buyer_block and isinstance(raw.get("buyer"), dict):
        buyer_block = raw["buyer"]
    if buyer_block and buyer_block.get("name"):
        return True, True

    for key in ("posting_number", "order_id", "buyer_id", "customer_id"):
        value = chat.get(key)
        if value:
            return True, True
        raw_value = raw.get(key)
        if raw_value:
            return True, True

    # 3) Ничего не нашли — консервативно False
    return False, False


def _classify_chat(chat: dict) -> tuple[bool, bool]:
    """Определить, является ли чат покупательским.

    Возвращает кортеж (is_buyer, recognized), где recognized=True означает, что
    решение было принято по явным правилам, а False — это консервативный фолбэк.
    """

    if not isinstance(chat, dict):
        return False, False

    raw = chat.get("_raw") if isinstance(chat.get("_raw"), dict) else {}

    # 1) Явные служебные типы
    chat_type = raw.get("chat_type") or raw.get("type")
    if isinstance(chat_type, str) and chat_type.lower() in {"support", "ozon_support", "service"}:
        return False, True

    # 2) Явные признаки покупателя
    buyer_block = chat.get("buyer") if isinstance(chat.get("buyer"), dict) else None
    if not buyer_block and isinstance(raw.get("buyer"), dict):
        buyer_block = raw["buyer"]
    if buyer_block and buyer_block.get("name"):
        return True, True

    for key in ("posting_number", "order_id", "buyer_id", "customer_id"):
        value = chat.get(key)
        if value:
            return True, True
        raw_value = raw.get(key)
        if raw_value:
            return True, True

    # 3) Ничего не нашли — консервативно False
    return False, False


def is_buyer_chat(chat: dict) -> bool:
    """Отфильтровать служебные/системные чаты и оставить только покупательские."""

    is_buyer, _ = _classify_chat(chat)
    return is_buyer


def _tc(user_id: int, chat_id: str) -> ThreadCache:
    key = (int(user_id), str(chat_id))
    t = _USER_THREADS.get(key)
    if t is None:
        t = ThreadCache()
        _USER_THREADS[key] = t
    return t


def resolve_chat_id(user_id: int, token: str | None) -> str | None:
    if not token:
        return None
    return _cc(user_id).token_to_chat_id.get(str(token).strip())


def _detect_sender_type(m: dict) -> str:
    """
    Пытаемся понять автора сообщения:
      - buyer/customer/client/user
      - seller/vendor/shop
      - ozon/system/service/support
    """
    candidates: list[str] = []

    for k in ("author_type", "sender_type", "type", "from_type", "user_type", "role"):
        v = m.get(k)
        if isinstance(v, str) and v:
            candidates.append(v.lower())

    # nested structures
    for k in ("from", "sender", "author"):
        v = m.get(k)
        if isinstance(v, dict):
            for kk in ("type", "role", "user_type", "kind"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv:
                    candidates.append(vv.lower())

    blob = " ".join(candidates)

    if any(x in blob for x in ("customer", "buyer", "client", "consumer", "user")):
        return "buyer"
    if any(x in blob for x in ("seller", "vendor", "shop", "merchant")):
        return "seller"
    if any(x in blob for x in ("ozon", "system", "service", "support", "robot", "auto")):
        return "ozon"

    # fallback: sometimes there is "is_my" / "is_seller"
    if m.get("is_seller") is True or m.get("is_my") is True:
        return "seller"
    if m.get("is_customer") is True:
        return "buyer"

    return "ozon"


def _extract_text(m: dict) -> str:
    # common fields
    for k in ("text", "message", "content", "body"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # some APIs store in payload
    payload = m.get("payload")
    if isinstance(payload, dict):
        v = payload.get("text") or payload.get("message")
        if isinstance(v, str) and v.strip():
            return v.strip()

    return ""


def _extract_created_at(m: dict) -> str | None:
    for k in ("created_at", "create_time", "timestamp", "date", "time"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def normalize_thread_messages(raw_messages: list[dict], *, customer_only: bool = True, include_seller: bool = True) -> list[NormalizedMessage]:
    out: list[NormalizedMessage] = []
    for m in raw_messages or []:
        if not isinstance(m, dict):
            continue
        role = _detect_sender_type(m)
        txt = _extract_text(m)
        if not txt:
            continue

        if customer_only:
            if role == "ozon":
                continue
            if role == "seller" and not include_seller:
                continue

        out.append(NormalizedMessage(role=role, text=txt, created_at=_extract_created_at(m), raw=m))

    return out


async def refresh_chats_list(user_id: int, *, force: bool = False) -> None:
    cache = _cc(user_id)
    if not force and cache.chats and _cache_fresh(cache.fetched_at, CACHE_TTL_SECONDS):
        return

    resp = await ozon_chat_list(limit=200, offset=0, refresh=True)
    chats = resp.chats or []

    # simple ordering: unread first, then by last_message_id desc
    chats.sort(key=lambda c: (-(int(c.unread_count or 0)), -(int(c.last_message_id or 0))))

    cache.chats = chats
    cache.fetched_at = _now_utc()

    cache.token_to_chat_id.clear()
    cache.chat_id_to_token.clear()
    for c in chats:
        cid = str(c.chat_id)
        tok = _short_token(user_id, cid)
        cache.chat_id_to_token[cid] = tok
        cache.token_to_chat_id[tok] = cid


async def get_chats_table(*, user_id: int, page: int, force_refresh: bool = False) -> tuple[str, list[dict], int, int]:
    await refresh_chats_list(user_id, force=force_refresh)
    cache = _cc(user_id)
    total = len(cache.chats)

    sorted_items = sorted(items_raw, key=_chat_sort_key, reverse=True)
    total_loaded = len(sorted_items)

    classifications: dict[int, tuple[bool, bool]] = {}
    buyer_count = 0
    service_count = 0
    unknown_count = 0
    for chat in sorted_items:
        chat_dict = chat if isinstance(chat, dict) else {}
        is_buyer, recognized = _classify_chat(chat_dict)
        classifications[id(chat)] = (is_buyer, recognized)
        if is_buyer:
            buyer_count += 1
        elif recognized:
            service_count += 1
        else:
            unknown_count += 1

    buyer_chats = [chat for chat in sorted_items if classifications.get(id(chat), (False, False))[0]]
    visible_items = sorted_items if show_service else buyer_chats
    logger.debug(
        "Chats list filter applied (show_service=%s): total=%s, after_filter=%s",
        show_service,
        total_loaded,
        len(visible_items),
    )
    logger.debug(
        "Chats classification stats: total=%s, buyer=%s, non_buyer=%s, unknown=%s",
        total_loaded,
        buyer_count,
        service_count,
        unknown_count,
    )

    cache: dict[str, dict] = {}
    for chat in visible_items:
        cid = _safe_chat_id(chat)
        if cid:
            cache[cid] = chat if isinstance(chat, dict) else {}

    filtered_items = [chat for chat in visible_items if not unread_flag or _chat_unread_count(chat) > 0]
    total_count = len(visible_items)
    unread_total = sum(1 for chat in visible_items if _chat_unread_count(chat) > 0)
    total_pages = max(1, math.ceil(max(1, len(filtered_items)) / CHAT_PAGE_SIZE))
    safe_page = 0 if page >= total_pages else max(0, min(page, total_pages - 1))
    start = safe_page * CHAT_PAGE_SIZE
    end = start + CHAT_PAGE_SIZE
    page_slice = filtered_items[start:end]

    display_rows: list[str] = []
    keyboard_items: list[tuple[str, str]] = []
    history_cache = data.get("chat_history") if isinstance(data.get("chat_history"), list) else None
    history_chat_id = data.get("current_chat_id")
    for idx, chat in enumerate(page_slice, start=start + 1):
        cid_history = history_cache if history_chat_id == _safe_chat_id(chat) else None
        chat_id_val, title, short_title, unread_count, preview, last_dt = _chat_display(
            chat, history=cid_history
        )
        if not chat_id_val:
            continue
        is_service_chat = not classifications.get(id(chat), (False, False))[0]
        badge = f"🔴{unread_count}" if unread_count > 0 else "⚪"
        ts_label = last_dt.strftime("%d.%m %H:%M") if last_dt else ""
        preview_label = preview or ""
        display_title = f"🛠 {title}" if show_service and is_service_chat else title
        line_parts = [
            f"{idx}) {badge} {display_title}",
            ts_label,
            preview_label,
        ]
        display_rows.append(" | ".join([part for part in line_parts if part]))
        kb_caption_parts = [badge, _truncate_text(display_title, limit=30)]
        if ts_label:
            kb_caption_parts.append(ts_label)
        if preview_label:
            kb_caption_parts.append(_truncate_text(preview_label, limit=50))
        kb_caption = " | ".join(kb_caption_parts)
        keyboard_items.append((chat_id_val, kb_caption))

    lines = ["💬 Чаты с покупателями"]
    lines.append(f"Всего: {total_count} | Непрочитано: {unread_total}")
    lines.append(
        "Фильтр: "
        + ("все" if show_service else "только покупатели")
        + f" | Только непрочитанные: {'ON' if unread_flag else 'OFF'}"
    )
    lines.append(f"Стр. {safe_page + 1}/{total_pages}")
    lines.append("")

    items: list[dict] = []
    for c in chunk:
        cid = str(c.chat_id)
        tok = cache.chat_id_to_token.get(cid) or _short_token(user_id, cid)
        title = (c.title or "").strip() or f"Чат {cid}"
        title = _trim(title.replace("\n", " "), 42)
        items.append({"token": tok, "title": title, "unread_count": int(c.unread_count or 0)})

    stamp = cache.fetched_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if cache.fetched_at else "—"
    text = (
        f"<b>Чаты</b>\n"
        f"Обновлено: <code>{stamp}</code>\n"
        f"Всего: <b>{total}</b> | Страница: <b>{safe_page + 1}/{total_pages}</b>\n\n"
        f"Выберите чат:"
    )
    return text, items, safe_page, total_pages


async def refresh_chat_thread(*, user_id: int, chat_id: str, force: bool = False, limit: int = DEFAULT_THREAD_LIMIT) -> ThreadView:
    cid = str(chat_id).strip()
    t = _tc(user_id, cid)

    if force:
        t.fetched_at = None

    # we maintain and clamp limit
    if limit:
        t.limit = max(10, min(int(limit), MAX_THREAD_LIMIT))
    else:
        t.limit = max(10, min(int(t.limit or DEFAULT_THREAD_LIMIT), MAX_THREAD_LIMIT))

    if not force and t.raw_messages and _cache_fresh(t.fetched_at, THREAD_TTL_SECONDS):
        return ThreadView(chat_id=cid, raw_messages=t.raw_messages, fetched_at=t.fetched_at, limit=t.limit, last_message_id=t.last_message_id)

    resp: ChatHistoryResponse = await ozon_chat_history(cid, limit=t.limit)
    t.raw_messages = resp.messages or []
    t.last_message_id = resp.last_message_id
    t.fetched_at = _now_utc()

    return ThreadView(chat_id=cid, raw_messages=t.raw_messages, fetched_at=t.fetched_at, limit=t.limit, last_message_id=t.last_message_id)


async def load_older_messages(*, user_id: int, chat_id: str, pages: int = 1, limit: int = 30) -> ThreadView:
    """
    У Ozon history часто нет явной пагинации в seller API (может меняться).
    Поэтому "старые" реализуем как увеличение limit на N*limit.
    """
    cid = str(chat_id).strip()
    t = _tc(user_id, cid)
    add = max(1, int(pages)) * max(10, int(limit))
    new_limit = min(MAX_THREAD_LIMIT, max(DEFAULT_THREAD_LIMIT, (t.limit or DEFAULT_THREAD_LIMIT) + add))
    return await refresh_chat_thread(user_id=user_id, chat_id=cid, force=True, limit=new_limit)


def _fmt_time(created_at: str | None) -> str:
    if not created_at:
        return ""
    s = created_at.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%H:%M")
    except Exception:
        return ""


def _bubble_text(msg: NormalizedMessage) -> str:
    tm = _fmt_time(msg.created_at)
    txt = _escape(msg.text)

    # минималистично, чтобы было похоже на “пузырь”
    if msg.role == "buyer":
        return f"{txt}\n<i>{tm}</i>" if tm else txt
    if msg.role == "seller":
        return f"<b>Вы:</b> {txt}\n<i>{tm}</i>" if tm else f"<b>Вы:</b> {txt}"
    return f"<i>{txt}</i>"


async def get_chat_bubbles_for_ui(
    *,
    user_id: int,
    chat_id: str,
    force_refresh: bool = False,
    customer_only: bool = True,
    include_seller: bool = False,
    max_messages: int = 18,
) -> list[str]:
    th = await refresh_chat_thread(user_id=user_id, chat_id=chat_id, force=force_refresh, limit=DEFAULT_THREAD_LIMIT)
    norm = normalize_thread_messages(th.raw_messages, customer_only=customer_only, include_seller=include_seller)

    # показываем последние max_messages
    tail = norm[-max(1, int(max_messages)) :]
    bubbles = [_bubble_text(m) for m in tail if (m.text or "").strip()]

    # если после фильтра стало пусто — вероятно, только системные сообщения
    return bubbles


__all__ = [
    "resolve_chat_id",
    "get_chats_table",
    "refresh_chat_thread",
    "load_older_messages",
    "normalize_thread_messages",
    "get_chat_bubbles_for_ui",
    "NormalizedMessage",
    "ThreadView",
]
