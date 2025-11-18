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


def _format_review_item(r: Dict[str, Any]) -> Tuple[datetime | None, str]:
    rating = int(r.get("rating") or r.get("grade") or 0)

    dt = (
        _parse_date(r.get("date"))
        or _parse_date(r.get("created_at"))
        or _parse_date(r.get("createdAt"))
    )
    dt_str = dt.strftime("%d.%m %H:%M") if dt else ""

    text = r.get("text") or r.get("comment") or ""
    text = str(text)
    if len(text) > 200:
        text = text[:197] + "…"

    offer = r.get("offer_id") or r.get("sku") or r.get("product_id") or ""
    product = r.get("product_title") or r.get("product_name") or ""
    prefix = f"{rating}★ "
    meta = []
    if dt_str:
        meta.append(dt_str)
    if offer:
        meta.append(str(offer))
    if product:
        meta.append(str(product))
    meta_str = " • ".join(meta)
    item_text = f"• {prefix}{meta_str}\n{text}".strip()
    return dt, item_text


async def _get_reviews_text(
    client: OzonClient,
    period_name: str,
    since: str,
    to: str,
    pretty: str,
) -> str:
    reviews = await client.get_reviews(since, to, limit=100)

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
    return await _get_reviews_text(client, "сегодня", since, to, pretty)


async def get_reviews_week_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_week_range()
    return await _get_reviews_text(client, "последние 7 дней", since, to, pretty)


async def get_reviews_month_text(client: OzonClient | None = None) -> str:
    client = client or get_client()
    since, to, pretty = msk_current_month_range()
    return await _get_reviews_text(client, "текущий месяц", since, to, pretty)


async def get_reviews_menu_text() -> str:
    return (
        "⭐ Раздел отзывов\n\n"
        "Доступны команды:\n"
        "• /reviews_today — отзывы за сегодня\n"
        "• /reviews_week — отзывы за 7 дней\n"
        "• /reviews_month — отзывы за текущий месяц"
    )
