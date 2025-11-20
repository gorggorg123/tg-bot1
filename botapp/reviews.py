# botapp/reviews.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from .ozon_client import (
    OzonClient,
    fmt_int,
    get_client,
    msk_current_month_range,
    msk_today_range,
    msk_week_range,
)


MAX_REVIEW_LEN = 450
_last_reviews_cache: Dict[str, Any] = {"period": "", "reviews": []}


def _parse_date(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).replace(" ", "T")
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _remember_reviews(period: str, reviews: List[Dict[str, Any]]) -> None:
    _last_reviews_cache["period"] = period
    _last_reviews_cache["reviews"] = reviews


def _latest_cached_review(period: str | None = None) -> Dict[str, Any] | None:
    if period and _last_reviews_cache.get("period") != period:
        return None
    reviews = _last_reviews_cache.get("reviews") or []
    if not reviews:
        return None
    sorted_reviews = sorted(
        reviews,
        key=lambda r: _parse_date(r.get("date"))
        or _parse_date(r.get("created_at"))
        or datetime.min,
        reverse=True,
    )
    return sorted_reviews[0]


def _format_review_item(r: Dict[str, Any]) -> Tuple[datetime | None, str]:
    rating = int(r.get("rating") or r.get("grade") or 0)

    dt = (
        _parse_date(r.get("date"))
        or _parse_date(r.get("created_at"))
        or _parse_date(r.get("createdAt"))
    )
    dt_str = dt.strftime("%d.%m %H:%M") if dt else ""

    text = (r.get("text") or r.get("comment") or "").strip()
    text = str(text)
    if len(text) > MAX_REVIEW_LEN:
        text = text[: MAX_REVIEW_LEN - 1] + "…"

    offer = r.get("offer_id") or r.get("sku") or r.get("product_id") or ""
    product = r.get("product_title") or r.get("product_name") or ""
    review_id = r.get("id") or r.get("review_id")
    product_id = r.get("product_id") or r.get("sku")
    link = f"https://www.ozon.ru/product/{product_id}/" if product_id else None

    head = f"{rating}★"
    if review_id:
        head += f" #{review_id}"
    if link:
        head = f"<a href=\"{link}\">{head}</a>"

    title_parts = [head]
    if product:
        title_parts.append(str(product))
    if offer:
        title_parts.append(str(offer))
    if dt_str:
        title_parts.append(dt_str)

    title = " • ".join(part for part in title_parts if part)
    item_text = f"• {title}\n{text}".strip()
    return dt, item_text


async def _get_reviews_text(
    client: OzonClient,
    period_name: str,
    period_key: str,
    since: str,
    to: str,
    pretty: str,
) -> str:
    reviews = await client.get_reviews(since, to, limit=120)
    _remember_reviews(period_key, reviews)

    dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    total = 0
    sum_rating = 0

    last_items: List[Tuple[datetime | None, str]] = []

    for r in reviews:
        rating = int(r.get("rating") or r.get("grade") or 0)
        if 1 <= rating <= 5:
            dist[rating] += 1
            total += 1
            sum_rating += rating

        last_items.append(_format_review_item(r))

    avg = (sum_rating / total) if total else 0

    dist_line = (
        f"1★ {fmt_int(dist[1])} • "
        f"2★ {fmt_int(dist[2])} • "
        f"3★ {fmt_int(dist[3])} • "
        f"4★ {fmt_int(dist[4])} • "
        f"5★ {fmt_int(dist[5])}"
    )

    header = (
        f"<b>⭐ Отзывы • {period_name}</b>\n"
        f"{pretty}\n\n"
        f"Всего отзывов: <b>{fmt_int(total)} шт</b>\n"
        f"Средний рейтинг: <b>{avg:.2f}</b>\n"
        f"Распределение: {dist_line}\n"
    )

    if last_items:
        last_items.sort(key=lambda x: x[0] or datetime.min, reverse=True)
        header += "\n<b>Последние отзывы (до 10 шт)</b>\n" + "\n\n".join(
            text for _, text in last_items[:10]
        )
    else:
        header += "\nОтзывы за период не найдены."

    return header


async def get_reviews_today_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_today_range()
    return await _get_reviews_text(client, "сегодня", "today", since, to, pretty)


async def get_reviews_week_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_week_range()
    return await _get_reviews_text(client, "последние 7 дней", "week", since, to, pretty)


async def get_reviews_month_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_current_month_range()
    return await _get_reviews_text(client, "текущий месяц", "month", since, to, pretty)


async def get_reviews_menu_text() -> str:
    return "⭐ Раздел отзывов. Выберите период:"


async def get_latest_review(period_key: str, client: OzonClient | None = None) -> Dict[str, Any] | None:
    cached = _latest_cached_review(period_key)
    if cached:
        return cached

    period_map = {
        "today": msk_today_range,
        "week": msk_week_range,
        "month": msk_current_month_range,
    }
    range_func = period_map.get(period_key)
    if not range_func:
        return None

    client = client or get_client()
    since, to, _ = range_func()
    reviews = await client.get_reviews(since, to, limit=20)
    if not reviews:
        return None
    _remember_reviews(period_key, reviews)
    return _latest_cached_review(period_key)
