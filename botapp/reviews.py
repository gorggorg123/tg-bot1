# botapp/reviews.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .ai_client import generate_review_reply
from .ozon_client import (
    OzonClient,
    fmt_int,
    get_client,
    msk_current_month_range,
    msk_today_range,
    msk_week_range,
)

logger = logging.getLogger(__name__)

MAX_REVIEW_LEN = 450
MAX_CACHED_REVIEWS = 200


@dataclass
class ReviewCard:
    id: str | None
    rating: int
    text: str
    product_name: str | None
    offer_id: str | None
    product_id: str | None
    created_at: datetime | None


@dataclass
class ReviewView:
    text: str
    index: int
    total: int
    period: str


# user_id -> cache entry
_user_reviews_cache: Dict[int, Dict[str, Any]] = {}


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    except Exception:
        return None


def _fmt_dt_msk(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt_msk = dt + timedelta(hours=3)
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _normalize_review(raw: Dict[str, Any]) -> ReviewCard:
    rating = int(raw.get("rating") or raw.get("grade") or 0)
    text = (raw.get("text") or raw.get("comment") or "").strip()
    text = str(text)
    if len(text) > MAX_REVIEW_LEN:
        text = text[: MAX_REVIEW_LEN - 1] + "…"

    return ReviewCard(
        id=str(raw.get("id") or raw.get("review_id") or "") or None,
        rating=rating,
        text=text,
        product_name=(raw.get("product_title") or raw.get("product_name") or raw.get("title")),
        offer_id=(raw.get("offer_id") or raw.get("sku") or raw.get("product_id")),
        product_id=(raw.get("product_id") or raw.get("sku") or None),
        created_at=(
            _parse_date(raw.get("date"))
            or _parse_date(raw.get("created_at"))
            or _parse_date(raw.get("createdAt"))
        ),
    )


def _period_meta(period_key: str):
    mapping = {
        "today": (msk_today_range, "Сегодня"),
        "week": (msk_week_range, "Последние 7 дней"),
        "month": (msk_current_month_range, "Текущий месяц"),
    }
    return mapping.get(period_key)


async def _fetch_period_reviews(period_key: str, client: OzonClient) -> Tuple[List[ReviewCard], str]:
    meta = _period_meta(period_key)
    if not meta:
        raise ValueError("Неизвестный период отзывов")

    since, to, pretty = meta[0]()
    logger.info("Fetching reviews period=%s since=%s to=%s", period_key, since, to)
    raw = await client.get_reviews(since, to, limit=80, max_reviews=MAX_CACHED_REVIEWS)
    cards = [_normalize_review(r) for r in raw if isinstance(r, dict)]
    cards.sort(key=lambda c: c.created_at or datetime.min, reverse=True)
    return cards, pretty


async def _ensure_cache(user_id: int, period_key: str, client: OzonClient) -> Dict[str, Any]:
    cached = _user_reviews_cache.get(user_id)
    if cached and cached.get("period") == period_key:
        return cached

    cards, pretty = await _fetch_period_reviews(period_key, client)
    cache = {
        "period": period_key,
        "pretty": pretty,
        "cards": cards,
        "index": 0,
    }
    _user_reviews_cache[user_id] = cache
    return cache


def _calc_stats(cards: List[ReviewCard]) -> Tuple[int, float, Dict[int, int]]:
    dist = {i: 0 for i in range(1, 6)}
    total = 0
    sum_rating = 0.0
    for c in cards:
        if 1 <= c.rating <= 5:
            dist[c.rating] += 1
            total += 1
            sum_rating += c.rating
    avg = (sum_rating / total) if total else 0.0
    return total, avg, dist


def _dist_line(dist: Dict[int, int]) -> str:
    return (
        f"1★ {fmt_int(dist[1])} • "
        f"2★ {fmt_int(dist[2])} • "
        f"3★ {fmt_int(dist[3])} • "
        f"4★ {fmt_int(dist[4])} • "
        f"5★ {fmt_int(dist[5])}"
    )


def _format_header(period_title: str, pretty: str, stats: Tuple[int, float, Dict[int, int]]) -> List[str]:
    total, avg, dist = stats
    lines = [
        f"⭐ Отзывы • {period_title}",
        pretty,
        "",
        f"Всего отзывов: {fmt_int(total)}",
        f"Средний рейтинг: {avg:.2f}",
        f"Распределение: {_dist_line(dist)}",
        "",
    ]
    return lines


def _format_card(period_key: str, pretty: str, cards: List[ReviewCard], index: int) -> ReviewView:
    if not cards:
        return ReviewView(
            text=f"⭐ Отзывы • {period_key}\n{pretty}\n\nЗа выбранный период отзывов нет.",
            index=0,
            total=0,
            period=period_key,
        )

    stats = _calc_stats(cards)
    header = _format_header(_period_meta(period_key)[1], pretty, stats)

    card = cards[index]
    total = len(cards)
    body = [
        f"⭐ Отзыв {index + 1} из {total} • период: {_period_meta(period_key)[1]}",
        "",
        f"Рейтинг: {card.rating}★",
    ]
    product = card.product_name or card.offer_id or card.product_id
    if product:
        body.append(f"Товар: {product}")
    if card.id:
        body.append(f"ID отзыва: {card.id}")
    if card.created_at:
        body.append(f"Дата: {_fmt_dt_msk(card.created_at)} (МСК)")

    body.append("")
    body.append("Текст отзыва:")
    body.append(card.text or "(пустой отзыв)")

    text = "\n".join(header + body)
    return ReviewView(text=text, index=index, total=total, period=period_key)


async def get_review_view(user_id: int, period_key: str, index: int = 0, client: OzonClient | None = None) -> ReviewView:
    client = client or get_client()
    cache = await _ensure_cache(user_id, period_key, client)
    cards: List[ReviewCard] = cache.get("cards", [])
    pretty = cache.get("pretty", "")

    safe_index = 0 if not cards else max(0, min(index, len(cards) - 1))
    cache["index"] = safe_index
    return _format_card(period_key, pretty, cards, safe_index)


async def shift_review_view(user_id: int, period_key: str, step: int, client: OzonClient | None = None) -> ReviewView:
    client = client or get_client()
    cache = await _ensure_cache(user_id, period_key, client)
    cards: List[ReviewCard] = cache.get("cards", [])
    new_index = cache.get("index", 0) + step
    if cards:
        new_index = max(0, min(new_index, len(cards) - 1))
    cache["index"] = new_index
    return await get_review_view(user_id, period_key, new_index, client)


async def get_reviews_today(client: OzonClient | None = None) -> Tuple[List[ReviewCard], str]:
    client = client or get_client()
    cards, pretty = await _fetch_period_reviews("today", client)
    return cards, pretty


async def get_reviews_week(client: OzonClient | None = None) -> Tuple[List[ReviewCard], str]:
    client = client or get_client()
    cards, pretty = await _fetch_period_reviews("week", client)
    return cards, pretty


async def get_reviews_month(client: OzonClient | None = None) -> Tuple[List[ReviewCard], str]:
    client = client or get_client()
    cards, pretty = await _fetch_period_reviews("month", client)
    return cards, pretty


async def get_current_review(user_id: int, period_key: str, client: OzonClient | None = None) -> ReviewCard | None:
    client = client or get_client()
    cache = await _ensure_cache(user_id, period_key, client)
    cards: List[ReviewCard] = cache.get("cards", [])
    if not cards:
        return None
    idx = cache.get("index", 0)
    idx = max(0, min(idx, len(cards) - 1))
    return cards[idx]


async def get_ai_reply_for_review(review: ReviewCard) -> str:
    try:
        return await generate_review_reply(
            review_text=review.text,
            product_name=review.product_name,
            rating=review.rating,
        )
    except Exception:
        logger.exception("AI reply generation failed")
        raise


async def get_reviews_menu_text() -> str:
    return "⭐ Раздел отзывов. Выберите период:"


__all__ = [
    "ReviewCard",
    "ReviewView",
    "get_review_view",
    "shift_review_view",
    "get_current_review",
    "get_reviews_today",
    "get_reviews_week",
    "get_reviews_month",
    "get_ai_reply_for_review",
    "get_reviews_menu_text",
]
