# botapp/sections/chats/logic.py
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Iterable

from botapp.api.ozon_client import (
    ChatListItem,
    OzonAPIError,
    chat_history as ozon_chat_history,
    chat_list as ozon_chat_list,
    get_client,
)
from botapp.catalog_cache import get_sku_title_from_cache, save_sku_title_to_cache
from botapp.sections._base import is_cache_fresh
from botapp.ui import TokenStore
from botapp.utils.storage import get_activated_chat_ids, mark_chat_activated

logger = logging.getLogger(__name__)

PAGE_SIZE = 10
CACHE_TTL_SECONDS = 60
THREAD_TTL_SECONDS = 10
MSK_TZ = timezone(timedelta(hours=3))

_chat_tokens = TokenStore(ttl_seconds=CACHE_TTL_SECONDS)
_chat_list_locks: dict[int, asyncio.Lock] = {}
_thread_locks: dict[tuple[int, str], asyncio.Lock] = {}

DEFAULT_THREAD_LIMIT = 30
MAX_THREAD_LIMIT = 180


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)



def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _escape(s: str) -> str:
    return html.escape((s or "").strip())


def _looks_like_placeholder_title(title: str | None) -> bool:
    if not title:
        return True
    norm = title.strip().lower()
    if not norm:
        return True
    return any(hint in norm for hint in ("buyer_seller", "unspecified", "chat"))


def _is_premium_plus_error(exc: Exception) -> bool:
    message = str(exc).lower()
    if "obsolete" in message and "chat" in message:
        return True
    if "premium plus" in message:
        return True
    return bool(re.search(r"code\s*[:=]?\s*9\b", message))


def _short_token(user_id: int, chat_id: str) -> str:
    return _chat_tokens.generate(user_id, chat_id, key=chat_id)


def friendly_chat_error(exc: Exception) -> str:
    text = str(exc).strip() or "ошибка Ozon API"
    lower = text.lower()
    if "429" in lower or "too many" in lower or "rate" in lower:
        return "Слишком много запросов. Подождите немного и попробуйте обновить чат."
    if "403" in lower or "forbidden" in lower:
        return (
            "Чаты временно недоступны для этого аккаунта/покупателя. "
            "Обновите чат позже или дождитесь ответа покупателя."
        )
    if _is_premium_plus_error(exc):
        return "Чаты в Seller API доступны только с подпиской Premium Plus."
    return f"Чат временно недоступен: {text}"


def _extract_last_message_id(raw_messages: list[dict]) -> int | None:
    last_id: int | None = None
    for m in raw_messages or []:
        if not isinstance(m, dict):
            continue
        for key in ("message_id", "id"):
            candidate = m.get(key)
            if candidate is None:
                continue
            try:
                cid = int(candidate)
            except (TypeError, ValueError):
                continue
            if last_id is None or cid > last_id:
                last_id = cid
            break

        if last_id is None:
            created = m.get("created_at") or m.get("timestamp")
            if isinstance(created, str) and created:
                try:
                    ts_val = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                    pseudo_id = int(ts_val)
                    if last_id is None or pseudo_id > last_id:
                        last_id = pseudo_id
                except Exception:
                    continue
    return last_id


@dataclass(slots=True)
class ChatsCache:
    fetched_at: datetime | None = None
    chats: list[ChatListItem] = field(default_factory=list)
    token_to_chat_id: dict[str, str] = field(default_factory=dict)
    chat_id_to_token: dict[str, str] = field(default_factory=dict)
    chat_titles: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    last_page: int = 0


@dataclass(slots=True)
class ThreadCache:
    fetched_at: datetime | None = None
    limit: int = DEFAULT_THREAD_LIMIT
    raw_messages: list[dict] = field(default_factory=list)
    last_message_id: int | None = None
    error: str | None = None


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
    context: dict[str, str] | None = None
    attachments: list[str] = field(default_factory=list)
    media_urls: list[str] = field(default_factory=list)
    product_title: str | None = None
    raw: dict | None = None


_USER_CACHE: dict[int, ChatsCache] = {}
_USER_THREADS: dict[tuple[int, str], ThreadCache] = {}


class PremiumPlusRequired(RuntimeError):
    """Raised when chat API is blocked for the account."""


def _cc(user_id: int) -> ChatsCache:
    c = _USER_CACHE.get(user_id)
    if c is None:
        c = ChatsCache()
        _USER_CACHE[user_id] = c
    return c


def last_seen_page(user_id: int) -> int:
    """Последняя страница списка чатов, которую видел пользователь."""

    return max(0, int(_cc(user_id).last_page))


def _tc(user_id: int, chat_id: str) -> ThreadCache:
    key = (int(user_id), str(chat_id))
    t = _USER_THREADS.get(key)
    if t is None:
        t = ThreadCache()
        _USER_THREADS[key] = t
    return t


def _chat_title_from_cache(user_id: int, chat_id: str) -> str | None:
    cid = str(chat_id).strip()
    cache = _cc(user_id)
    if cache.chat_titles.get(cid):
        return cache.chat_titles[cid]
    for c in cache.chats:
        if (c.safe_chat_id or str(c.chat_id or "").strip()) == cid:
            raw_title = (c.title or "").strip()
            if raw_title:
                return raw_title
    return None


def _lock_for_chat_list(user_id: int) -> asyncio.Lock:
    lock = _chat_list_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _chat_list_locks[user_id] = lock
    return lock


def _lock_for_thread(user_id: int, chat_id: str) -> asyncio.Lock:
    key = (int(user_id), str(chat_id))
    lock = _thread_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[key] = lock
    return lock


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
    for k in ("from", "sender", "author", "user"):
        v = m.get(k)
        if isinstance(v, dict):
            for kk in ("type", "role", "user_type", "kind"):
                vv = v.get(kk)
                if isinstance(vv, str) and vv:
                    candidates.append(vv.lower())

    blob = " ".join(candidates)

    if any(x in blob for x in ("notification", "system", "unspecified", "auto")):
        return "ozon"
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


def extract_media_urls_from_text(text: str) -> tuple[str, list[str]]:
    """Убираем URL картинок из текста и возвращаем их отдельно."""

    media_urls: list[str] = []
    clean = text or ""

    markdown_pattern = re.compile(r"!\[[^\]]*?\]\((https?://[^\s)]+)\)")
    api_pattern = re.compile(r"(https?://api-seller\.ozon\.ru/v2/chat/file/[^\s)]+)")

    for match in markdown_pattern.findall(clean):
        media_urls.append(match)
    clean = markdown_pattern.sub("", clean)

    for match in api_pattern.findall(clean):
        media_urls.append(match)
    clean = api_pattern.sub("", clean)

    # normalize spacing after removals
    clean = " ".join(clean.split())

    return clean.strip(), media_urls


def _extract_attachments(m: dict) -> list[str]:
    attachments: list[str] = []

    def _human_size(size: int | float | None) -> str:
        try:
            sz = float(size or 0)
        except Exception:
            return ""
        units = ["Б", "КБ", "МБ", "ГБ"]
        idx = 0
        while sz >= 1024 and idx < len(units) - 1:
            sz /= 1024
            idx += 1
        return f"{sz:.0f} {units[idx]}" if sz else ""

    def _append(item: dict | str) -> None:
        if isinstance(item, str):
            label = item.strip()
            if label:
                attachments.append(f"📎 {label}")
            return
        if not isinstance(item, dict):
            return
        name = item.get("file_name") or item.get("name") or item.get("filename") or item.get("title")
        kind = item.get("type") or item.get("mime_type")
        size = _human_size(item.get("size") or item.get("file_size"))
        parts = [p for p in (name, kind, size) if p]
        label = "; ".join(parts) if parts else "вложение"
        attachments.append(f"📎 {label}")

    for key in ("attachments", "files", "documents", "images", "photos"):
        maybe = m.get(key)
        if isinstance(maybe, list):
            for entry in maybe:
                _append(entry)
        elif isinstance(maybe, dict):
            _append(maybe)

    payload = m.get("payload")
    if isinstance(payload, dict):
        for key in ("attachments", "files"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                for entry in maybe:
                    _append(entry)

    return attachments


def _extract_context(m: dict) -> dict[str, str]:
    ctx: dict[str, str] = {}

    for key in ("sku", "posting_number", "order_number", "product_id"):
        v = m.get(key)
        if isinstance(v, (str, int)) and str(v).strip():
            ctx[key] = str(v).strip()

    payload = m.get("payload")
    if isinstance(payload, dict):
        for key in ("sku", "posting_number", "order_number"):
            v = payload.get(key)
            if isinstance(v, (str, int)) and str(v).strip():
                ctx.setdefault(key, str(v).strip())

        ctx_block = payload.get("context")
        if isinstance(ctx_block, dict):
            for key in ("sku", "posting_number", "order_number"):
                v = ctx_block.get(key)
                if isinstance(v, (str, int)) and str(v).strip():
                    ctx.setdefault(key, str(v).strip())

    return ctx


def _extract_created_at(m: dict) -> str | None:
    for k in ("created_at", "create_time", "timestamp", "date", "time"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _sort_key_for_message(m: dict, idx: int) -> tuple[int, int]:
    """Стабильный ключ сортировки: по времени/ID, затем по порядку."""

    ts = None
    created_at = _extract_created_at(m)
    if created_at:
        try:
            ts = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
        except Exception:
            ts = None

    mid = None
    for key in ("message_id", "id"):
        val = m.get(key)
        try:
            mid = int(val)
            break
        except Exception:
            continue

    return (ts if ts is not None else -10**12, mid if mid is not None else idx)


def normalize_thread_messages(raw_messages: list[dict], *, customer_only: bool = True, include_seller: bool = True) -> list[NormalizedMessage]:
    out: list[NormalizedMessage] = []

    sorted_pairs = sorted(
        list(enumerate([m for m in raw_messages or [] if isinstance(m, dict)])),
        key=lambda pair_idx: _sort_key_for_message(pair_idx[1], pair_idx[0]),
    )

    for _idx, m in sorted_pairs:
        if not isinstance(m, dict):
            continue
        role = _detect_sender_type(m)
        txt_raw = _extract_text(m)
        txt, media_urls = extract_media_urls_from_text(txt_raw)
        attachments = _extract_attachments(m)
        if attachments and txt:
            txt = txt + "\n" + "\n".join(attachments)
        elif attachments and not txt:
            txt = "\n".join(attachments)
        if not (txt or attachments or media_urls):
            continue

        if customer_only:
            if role not in ("buyer", "seller"):
                continue
            if role == "seller" and not include_seller:
                continue

        out.append(
            NormalizedMessage(
                role=role,
                text=txt,
                created_at=_extract_created_at(m),
                context=_extract_context(m) or None,
                attachments=attachments,
                media_urls=media_urls,
                raw=m,
            )
        )

    return out


async def resolve_product_title_for_message(
    user_id: int, msg: NormalizedMessage | dict, chat_title: str | None = None
) -> str | None:
    ctx: dict[str, str] = {}
    raw: dict | None = None
    if isinstance(msg, NormalizedMessage):
        ctx = msg.context or {}
        raw = msg.raw
    elif isinstance(msg, dict):
        raw = msg
        if isinstance(msg.get("context"), dict):
            ctx = msg.get("context") or {}

    product_id = str(ctx.get("product_id") or "").strip() if ctx else ""
    sku = str(ctx.get("sku") or "").strip() if ctx else ""

    if not product_id and isinstance(raw, dict):
        nested_ctx = raw.get("context") if isinstance(raw.get("context"), dict) else {}
        product_id = product_id or str((nested_ctx or {}).get("product_id") or "").strip()
        sku = sku or str((nested_ctx or {}).get("sku") or "").strip()

    # 1) product_id via API
    if product_id:
        try:
            name = await get_client().get_product_name(product_id)
            if name:
                if sku:
                    save_sku_title_to_cache(sku, name)
                return name
        except Exception:
            logger.debug("product_name lookup failed for %s", product_id, exc_info=True)

    # 2) sku cached title
    cached = get_sku_title_from_cache(sku)
    if cached:
        return cached

    # 3) chat title fallback if it looks human-readable
    if chat_title and not _looks_like_placeholder_title(chat_title):
        return chat_title.strip()

    return None


def derive_chat_title_from_thread(thread: list[NormalizedMessage]) -> str | None:
    """Attempt to infer a chat title from buyer messages."""

    first_buyer: NormalizedMessage | None = None
    for msg in thread:
        if msg.role == "buyer" and (msg.text or "").strip():
            first_buyer = msg
            break

    if not first_buyer:
        return None

    text = (first_buyer.text or "").strip()
    pattern = re.compile(r"(?:товар[ау]?|по\s+товару)\s*[\"“«](.{6,80}?)[\"”»]", re.IGNORECASE)
    match = pattern.search(text)
    if match:
        return match.group(1).strip()

    generic = re.compile(r"[\"“«](.{6,80}?)[\"”»]")
    match = generic.search(text)
    if match:
        return match.group(1).strip()

    return None


def last_buyer_message_text(user_id: int, chat_id: str) -> str | None:
    cid = str(chat_id).strip()
    t = _tc(user_id, cid)
    raw_messages = t.raw_messages or []
    if not raw_messages:
        return None

    norm = normalize_thread_messages(raw_messages, customer_only=True, include_seller=False)
    for msg in reversed(norm):
        if msg.role == "buyer" and (msg.text or "").strip():
            return msg.text.strip()
    return None


def last_buyer_message(user_id: int, chat_id: str) -> NormalizedMessage | None:
    cid = str(chat_id).strip()
    t = _tc(user_id, cid)
    raw_messages = t.raw_messages or []
    if not raw_messages:
        return None

    norm = normalize_thread_messages(raw_messages, customer_only=True, include_seller=False)
    for msg in reversed(norm):
        if msg.role == "buyer" and (msg.text or "").strip():
            return msg
    return None


async def refresh_chats_list(user_id: int, *, force: bool = False) -> None:
    cache = _cc(user_id)
    lock = _lock_for_chat_list(user_id)

    activated = get_activated_chat_ids(user_id)
    activation_checks = 0

    async with lock:
        if not force and cache.chats and is_cache_fresh(cache.fetched_at, CACHE_TTL_SECONDS):
            return

        try:
            resp = await ozon_chat_list(
                limit=200,
                offset=0,
                include_service=False,
                chat_type_whitelist=("buyer_seller", "buyer-seller", "buyer_seller_chat", "buyer_seller"),
            )
        except OzonAPIError as exc:
            if _is_premium_plus_error(exc):
                cache.error = "premium_plus_required"
                cache.chats = []
                cache.fetched_at = _now_utc()
                cache.token_to_chat_id.clear()
                cache.chat_id_to_token.clear()
                return
            cache.error = str(exc)
            raise

        chats = resp.chats or list(resp.iter_items())

        time_budget_seconds = 2.0
        max_checks = 30
        concurrency = 4
        t0 = time.monotonic()
        semaphore = asyncio.Semaphore(concurrency)

        candidates: list[tuple[int, ChatListItem]] = []
        for c in chats:
            cid = (c.safe_chat_id or str(c.chat_id or "").strip() or "").strip()
            if not cid or cid in activated:
                continue
            try:
                last_id = int(c.last_message_id or 0)
            except Exception:
                last_id = 0
            if last_id > 0:
                candidates.append((last_id, c))

        candidates.sort(key=lambda item: -item[0])

        async def _probe_and_activate(cid: str) -> None:
            nonlocal activated
            async with semaphore:
                if time.monotonic() - t0 > time_budget_seconds:
                    return
                try:
                    history = await ozon_chat_history(cid, limit=10)
                    for m in history or []:
                        if not isinstance(m, dict):
                            continue
                        if _detect_sender_type(m) == "buyer":
                            mark_chat_activated(user_id, cid)
                            activated.add(cid)
                            break
                except Exception:
                    logger.debug(
                        "Failed to auto-activate chat %s for user %s",
                        cid,
                        user_id,
                        exc_info=True,
                    )

        tasks: list[asyncio.Task[None]] = []
        for _last_id, c in candidates:
            if activation_checks >= max_checks or (time.monotonic() - t0) > time_budget_seconds:
                break
            cid = (c.safe_chat_id or str(c.chat_id or "").strip() or "").strip()
            if not cid or cid in activated:
                continue
            activation_checks += 1
            tasks.append(asyncio.create_task(_probe_and_activate(cid)))

        if tasks:
            await asyncio.gather(*tasks)

        deduped: list[ChatListItem] = []
        seen: set[str] = set()
        for c in chats:
            cid = (c.safe_chat_id or str(c.chat_id or "").strip() or "").strip()
            if not cid:
                continue
            ctype = str(c.chat_type or "").lower()
            if ctype and ctype not in ("buyer_seller", "buyer-seller", "buyer_seller_chat"):
                continue
            unread_count = int(c.unread_count or 0)
            if unread_count <= 0 and cid not in activated:
                continue
            if cid in seen:
                continue
            seen.add(cid)
            deduped.append(c)

        # simple ordering: unread first, then by last_message_id desc
        deduped.sort(key=lambda c: (-(int(c.unread_count or 0)), -(int(c.last_message_id or 0))))

        _chat_tokens.clear(user_id)
        cache.chats = deduped
        cache.fetched_at = _now_utc()
        cache.error = None

        cache.token_to_chat_id.clear()
        cache.chat_id_to_token.clear()
        for c in deduped:
            cid = c.safe_chat_id or str(c.chat_id or "").strip()
            if not cid:
                continue
            tok = _short_token(user_id, cid)
            cache.chat_id_to_token[cid] = tok
            cache.token_to_chat_id[tok] = cid

        if not deduped:
            logger.info("Chat list returned 0 items for user %s (HTTP 200)", user_id)
        spent = time.monotonic() - t0
        logger.info(
            "Chats list: total_from_api=%s activated=%s shown=%s activation_checks=%s time_spent=%.2fs",
            len(chats),
            len(activated),
            len(deduped),
            activation_checks,
            spent,
        )


async def get_chats_table(*, user_id: int, page: int, force_refresh: bool = False) -> tuple[str, list[dict], int, int]:
    await refresh_chats_list(user_id, force=force_refresh)
    cache = _cc(user_id)

    if cache.error == "premium_plus_required":
        raise PremiumPlusRequired()

    total = len(cache.chats)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))
    cache.last_page = safe_page

    start = safe_page * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = cache.chats[start:end]

    items: list[dict] = []
    for i, c in enumerate(chunk, start=1 + start):
        cid = c.safe_chat_id or str(c.chat_id or "").strip()
        if not cid:
            continue
        ctype = str(c.chat_type or "").lower()
        if ctype and ctype not in ("buyer_seller", "buyer-seller", "buyer_seller_chat"):
            continue
        tok = cache.chat_id_to_token.get(cid) or _short_token(user_id, cid)
        cached_title = cache.chat_titles.get(cid)
        raw_title = (c.title or "").strip()
        final_title = cached_title or raw_title
        title = _trim((final_title or f"Чат {cid}").replace("\n", " "), 42)
        extras = getattr(c, "model_extra", {}) or {}
        last_message = extras.get("last_message") or getattr(c, "last_message", None)
        if isinstance(last_message, dict):
            last_snippet = _trim(_extract_text(last_message) or _extract_text(last_message.get("message") or {}) or "", 60)
            if not last_snippet:
                attach_preview = _extract_attachments(last_message)
                last_snippet = attach_preview[0] if attach_preview else ""
            last_time = _fmt_time(last_message.get("created_at") or last_message.get("time") or last_message.get("timestamp"))
        else:
            last_snippet = _trim(str(getattr(c, "last_message_text", "") or extras.get("last_message_text") or ""), 60)
            last_time = _fmt_time(getattr(c, "last_message_time", None) or extras.get("last_message_time"))

        product_title = None
        if raw_title and not _looks_like_placeholder_title(raw_title):
            product_title = _trim(raw_title.replace("\n", " "), 60)
        else:
            for key in ("product_id", "sku", "offer_id"):
                cached = get_sku_title_from_cache(str(extras.get(key) or "")) if extras else None
                if cached:
                    product_title = _trim(cached, 60)
                    break

        badge = f" непр.:{int(c.unread_count or 0)}" if int(c.unread_count or 0) > 0 else ""
        subtitle_parts = [p for p in (last_snippet, last_time) if p]
        subtitle = " • ".join(subtitle_parts)
        short_id = cid[-8:] if len(cid) > 8 else cid
        idx = f"{i:02d})"
        caption_bits = [f"{idx} {title}", f"ID:{short_id}"]
        if product_title:
            caption_bits.append(f"Товар: {product_title}")
        if subtitle:
            caption_bits.append(subtitle)
        if badge:
            caption_bits.append(badge)
        caption = _trim(" • ".join([bit for bit in caption_bits if bit]).replace("\n", " "), 64)

        items.append({"token": tok, "title": caption or title, "unread_count": int(c.unread_count or 0)})

    stamp = cache.fetched_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC") if cache.fetched_at else "—"
    if total == 0:
        text = (
            "<b>Чаты</b>\n"
            f"Обновлено: <code>{stamp}</code>\n"
            "Чаты пустые или нет доступа к методам чатов этим ключом/тарифом.\n"
            "Проверьте права OZON_CLIENT_ID / OZON_API_KEY и подписку Premium Plus."
        )
    else:
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
    lock = _lock_for_thread(user_id, cid)

    async with lock:
        if force:
            t.fetched_at = None

        # we maintain and clamp limit
        if limit:
            t.limit = max(10, min(int(limit), MAX_THREAD_LIMIT))
        else:
            t.limit = max(10, min(int(t.limit or DEFAULT_THREAD_LIMIT), MAX_THREAD_LIMIT))

        if not force and t.raw_messages and is_cache_fresh(t.fetched_at, THREAD_TTL_SECONDS):
            return ThreadView(
                chat_id=cid,
                raw_messages=t.raw_messages,
                fetched_at=t.fetched_at,
                limit=t.limit,
                last_message_id=t.last_message_id,
            )

        raw_messages = await ozon_chat_history(cid, limit=t.limit)
        t.raw_messages = raw_messages or []
        t.last_message_id = _extract_last_message_id(t.raw_messages)
        t.fetched_at = _now_utc()
        t.error = None

        for m in t.raw_messages:
            if not isinstance(m, dict):
                continue
            if _detect_sender_type(m) == "buyer":
                mark_chat_activated(user_id, cid)
                break

        try:
            normalized_thread = normalize_thread_messages(t.raw_messages, customer_only=True, include_seller=True)
            title = derive_chat_title_from_thread(normalized_thread)
            if title and cid:
                cache = _cc(user_id)
                cache.chat_titles[cid] = title
                logger.info("Derived chat title for %s: %s", cid, title)
        except Exception:
            logger.debug("Failed to derive chat title for %s", cid, exc_info=True)

        return ThreadView(
            chat_id=cid,
            raw_messages=t.raw_messages,
            fetched_at=t.fetched_at,
            limit=t.limit,
            last_message_id=t.last_message_id,
        )


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
        return dt.astimezone(MSK_TZ).strftime("%d.%m %H:%M")
    except Exception:
        return ""


def _bubble_text(msg: NormalizedMessage) -> str:
    tm = _fmt_time(msg.created_at)
    txt = _escape(msg.text)

    label = "👤 Покупатель" if msg.role == "buyer" else ("🏪 Мы" if msg.role == "seller" else "Сервис")
    prefix = f"<b>{label}:</b> "
    suffix_lines: list[str] = []
    if msg.product_title:
        suffix_lines.append(f"Товар: {_escape(msg.product_title)}")
    if msg.context:
        bits: list[str] = []
        sku = msg.context.get("sku") or msg.context.get("product_id")
        posting = msg.context.get("posting_number") or msg.context.get("order_number")
        if sku:
            bits.append(f"SKU {sku}")
        if posting:
            bits.append(f"Заказ {posting}")
        if bits:
            suffix_lines.append("<i>" + ", ".join(bits) + "</i>")

    suffix = ("\n".join(suffix_lines)) if suffix_lines else ""
    if suffix and not suffix.startswith("\n"):
        suffix = "\n" + suffix

    if tm:
        return f"{prefix}{txt}{suffix}\n<i>{tm}</i>"
    return prefix + txt + suffix


async def get_chat_bubbles_for_ui(
    *,
    user_id: int,
    chat_id: str,
    force_refresh: bool = False,
    customer_only: bool = True,
    include_seller: bool = False,
    max_messages: int = 18,
) -> list[NormalizedMessage]:
    th = await refresh_chat_thread(user_id=user_id, chat_id=chat_id, force=force_refresh, limit=DEFAULT_THREAD_LIMIT)
    norm = normalize_thread_messages(
        th.raw_messages, customer_only=customer_only, include_seller=include_seller
    )

    # показываем последние max_messages
    tail = norm[-max(1, int(max_messages)) :]
    chat_title = _chat_title_from_cache(user_id, chat_id)
    for msg in tail:
        if msg.product_title:
            continue
        try:
            msg.product_title = await resolve_product_title_for_message(user_id, msg, chat_title=chat_title)
        except Exception:
            logger.debug("Failed to resolve product title for chat %s", chat_id, exc_info=True)
    return tail


__all__ = [
    "resolve_chat_id",
    "get_chats_table",
    "refresh_chat_thread",
    "load_older_messages",
    "normalize_thread_messages",
    "last_buyer_message_text",
    "last_buyer_message",
    "get_chat_bubbles_for_ui",
    "resolve_product_title_for_message",
    "NormalizedMessage",
    "ThreadView",
    "friendly_chat_error",
    "last_seen_page",
    "extract_media_urls_from_text",
]
