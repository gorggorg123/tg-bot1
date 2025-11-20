# botapp/reviews.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

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
class PeriodView:
    text: str
    has_prev: bool
    has_next: bool
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


def _period_meta(period_key: str):
    mapping = {
        "today": (msk_today_range, "сегодня"),
        "week": (msk_week_range, "последние 7 дней"),
        "month": (msk_current_month_range, "текущий месяц"),
    }
    return mapping.get(period_key)


def _format_review_card(
    period_key: str,
    pretty: str,
    cards: List[ReviewCard],
    stats: Tuple[int, float, Dict[int, int]],
    index: int,
) -> PeriodView:
    total, avg, dist = stats
    has_prev = index > 0
    has_next = index + 1 < len(cards)

    period_name = _period_meta(period_key)[1] if _period_meta(period_key) else period_key
    lines = [
        f"<b>⭐ Отзывы • {period_name}</b>",
        pretty,
        "",
        f"Всего отзывов: <b>{fmt_int(total)} шт</b>",
        f"Средний рейтинг: <b>{avg:.2f}</b>",
        f"Распределение: {_dist_line(dist)}",
    ]

    if not cards:
        lines.append("")
        lines.append("Отзывы за период не найдены.")
        return PeriodView("\n".join(lines), False, False, period_key)

    card = cards[index]
    lines.extend(
        [
            "",
            f"⭐ Отзыв {index + 1}/{len(cards)} • {card.rating}★",
        ]
    )

    product = card.product_name or card.offer_id or "—"
    if product:
        lines.append(f"Товар: {product}")
    if card.created_at:
        lines.append(f"Дата: {_fmt_dt_msk(card.created_at)} (МСК)")

    lines.append("")
    lines.append(card.text or "(пустой отзыв)")

    return PeriodView("\n".join(lines), has_prev, has_next, period_key)


async def _reload_cache(user_id: int, period_key: str, client: OzonClient) -> Dict[str, Any]:
    meta = _period_meta(period_key)
    if not meta:
        raise ValueError("Неизвестный период отзывов")

    since, to, pretty = meta[0]()
    logger.info("Fetching reviews period=%s since=%s to=%s", period_key, since, to)
    reviews_raw = await client.get_reviews(since, to, limit=80, max_reviews=300)

    cards = [_normalize_review(r) for r in reviews_raw if isinstance(r, dict)]
    cards.sort(key=lambda c: c.created_at or datetime.min, reverse=True)

    stats = _calc_stats(cards)
    cache = {
        "period": period_key,
        "pretty": pretty,
        "cards": cards,
        "index": 0,
        "stats": stats,
    }
    _user_reviews_cache[user_id] = cache
    return cache


async def _ensure_cache(user_id: int, period_key: str, client: OzonClient) -> Dict[str, Any]:
    cached = _user_reviews_cache.get(user_id)
    if cached and cached.get("period") == period_key:
        return cached
    return await _reload_cache(user_id, period_key, client)


def _current_view_from_cache(period_key: str, cache: Dict[str, Any]) -> PeriodView:
    cards: List[ReviewCard] = cache.get("cards", [])
    index = min(cache.get("index", 0), max(len(cards) - 1, 0))
    cache["index"] = index
    stats = cache.get("stats") or _calc_stats(cards)
    cache["stats"] = stats
    pretty = cache.get("pretty") or ""
    return _format_review_card(period_key, pretty, cards, stats, index)


async def get_reviews_period_view(
    user_id: int,
    period_key: str,
    client: OzonClient | None = None,
) -> PeriodView:
    client = client or get_client()
    cache = await _ensure_cache(user_id, period_key, client)
    cache["index"] = 0
    return _current_view_from_cache(period_key, cache)


async def shift_reviews_view(user_id: int, step: int) -> PeriodView | None:
    cache = _user_reviews_cache.get(user_id)
    if not cache:
        return None
    cards: List[ReviewCard] = cache.get("cards", [])
    if not cards:
        return _current_view_from_cache(cache.get("period", ""), cache)

    new_index = cache.get("index", 0) + step
    new_index = max(0, min(new_index, len(cards) - 1))
    if new_index == cache.get("index", 0) and cards:
        # нет сдвига
        return _current_view_from_cache(cache.get("period", ""), cache)

    cache["index"] = new_index
    return _current_view_from_cache(cache.get("period", ""), cache)


async def get_latest_review(period_key: str, user_id: int) -> Dict[str, Any] | None:
    cache = _user_reviews_cache.get(user_id)
    if not cache or cache.get("period") != period_key:
        return None
    cards: List[ReviewCard] = cache.get("cards", [])
    if not cards:
        return None
    card = cards[min(cache.get("index", 0), len(cards) - 1)]
    return {
        "text": card.text,
        "rating": card.rating,
        "product_name": card.product_name,
        "offer_id": card.offer_id,
        "product_id": card.product_id,
        "created_at": card.created_at.isoformat() if card.created_at else None,
    }


async def get_reviews_today_text(user_id: int | None = None, client: OzonClient | None = None) -> str:
    view = await get_reviews_period_view(user_id or 0, "today", client)
    return view.text


async def get_reviews_week_text(user_id: int | None = None, client: OzonClient | None = None) -> str:
    view = await get_reviews_period_view(user_id or 0, "week", client)
    return view.text


async def get_reviews_month_text(user_id: int | None = None, client: OzonClient | None = None) -> str:
    view = await get_reviews_period_view(user_id or 0, "month", client)
    return view.text


async def get_reviews_menu_text() -> str:
    return "⭐ Раздел отзывов. Выберите период:"
