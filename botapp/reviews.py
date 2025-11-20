# botapp/reviews.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any, Dict, List, Tuple

from .ai_client import generate_review_reply
from .ozon_client import OzonClient, fmt_int, get_client

logger = logging.getLogger(__name__)

DEFAULT_RECENT_DAYS = 30
MAX_REVIEW_LEN = 450
MAX_REVIEWS_LOAD = 200
MSK_SHIFT = timedelta(hours=3)
TELEGRAM_SOFT_LIMIT = 3500
REVIEWS_PAGE_SIZE = 5
MODE_ANSWERED = "answered"
MODE_UNANSWERED = "unanswered"


@dataclass
class ReviewCard:
    id: str | None
    rating: int
    text: str
    product_name: str | None
    offer_id: str | None
    product_id: str | None
    created_at: datetime | None
    answered: bool = False


@dataclass
class ReviewsPage:
    text: str
    page: int
    total_pages: int
    total_reviews: int
    total_filtered: int
    mode: str
    pretty_period: str


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
    dt_msk = dt + MSK_SHIFT
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _msk_range_last_days(days: int = DEFAULT_RECENT_DAYS) -> Tuple[datetime, datetime, str]:
    """Вернуть диапазон последних *days* дней в МСК (начиная с полуночи)."""
    now_utc = datetime.utcnow()
    now_msk = now_utc + MSK_SHIFT
    start_msk = datetime(now_msk.year, now_msk.month, now_msk.day) - timedelta(days=days - 1)
    end_msk = datetime(now_msk.year, now_msk.month, now_msk.day, 23, 59, 59)
    pretty = f"{start_msk:%d.%m.%Y} — {end_msk:%d.%m.%Y} (МСК)"
    return start_msk, end_msk, pretty


def _normalize_review(raw: Dict[str, Any]) -> ReviewCard:
    rating = int(raw.get("rating") or raw.get("grade") or 0)
    text = (raw.get("text") or raw.get("comment") or "").strip()
    text = str(text)
    if len(text) > MAX_REVIEW_LEN:
        text = text[: MAX_REVIEW_LEN - 1] + "…"

    answer_payload = raw.get("answer") or raw.get("reply") or raw.get("response")
    answered = bool(answer_payload or raw.get("answered"))

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
        answered=answered,
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


def _shorten(text: str, limit: int = MAX_REVIEW_LEN) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def trim_for_telegram(text: str, max_len: int = TELEGRAM_SOFT_LIMIT) -> str:
    """Обрезать текст до безопасной длины для Telegram, оставляя пометку."""

    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


async def fetch_recent_reviews(
    client: OzonClient | None = None,
    *,
    days: int = DEFAULT_RECENT_DAYS,
    limit_per_page: int = 80,
    max_reviews: int = MAX_REVIEWS_LOAD,
) -> Tuple[List[ReviewCard], str]:
    """Загрузить отзывы за последние *days* дней одним списком.

    Серверная часть не делит отзывы по периодам; мы берём фиксированное окно и
    аккуратно логируем объём выборки.
    """

    client = client or get_client()
    since_msk, to_msk, pretty = _msk_range_last_days(days)
    raw = await client.get_reviews(
        since_msk,
        to_msk,
        limit_per_page=limit_per_page,
        max_count=max_reviews,
    )
    cards = [_normalize_review(r) for r in raw if isinstance(r, dict)]
    cards.sort(key=lambda c: c.created_at or datetime.min, reverse=True)
    logger.info(
        "Reviews fetched (flat): %s items, period=%s",
        len(cards),
        pretty,
    )
    return cards, pretty


def _format_reviews_page(
    cards: List[ReviewCard],
    *,
    mode: str,
    page: int,
    pretty: str,
    page_size: int = REVIEWS_PAGE_SIZE,
) -> ReviewsPage:
    total = len(cards)
    answered = [c for c in cards if c.answered]
    unanswered = [c for c in cards if not c.answered]
    filtered = answered if mode == MODE_ANSWERED else unanswered

    total_filtered = len(filtered)
    total_pages = max(1, ceil(total_filtered / page_size)) if total_filtered else 1
    safe_page = max(0, min(page, total_pages - 1))

    start = safe_page * page_size
    end = start + page_size
    page_items = filtered[start:end] if filtered else []

    header = [
        f"⭐ Отзывы (последние {DEFAULT_RECENT_DAYS} дней)",
        pretty,
        "",
        f"Всего: {fmt_int(total)}",
        f"Без ответа: {fmt_int(len(unanswered))}",
        f"С ответом: {fmt_int(len(answered))}",
        "",
        f"Режим: {'неотвеченные' if mode == MODE_UNANSWERED else 'отвеченные'}",
    ]

    if not page_items:
        body = ["Отзывов в выбранном режиме нет."]
    else:
        body = []
        for idx, card in enumerate(page_items, start=start + 1):
            product = card.product_name or card.offer_id or card.product_id or "—"
            body.extend(
                [
                    f"{idx}) {card.rating or '—'}★ — {product}",
                    f"Дата: {_fmt_dt_msk(card.created_at) or '—'} (МСК)",
                    "Текст:",
                    _shorten(card.text or "(пустой отзыв)", limit=MAX_REVIEW_LEN),
                    f"Ответ: {'есть' if card.answered else 'нет'}",
                    "",
                ]
            )

    text = "\n".join(header + body).strip()
    text = trim_for_telegram(text)
    return ReviewsPage(
        text=text,
        page=safe_page,
        total_pages=total_pages,
        total_reviews=total,
        total_filtered=total_filtered,
        mode=mode,
        pretty_period=pretty,
    )
    cards = [_normalize_review(r) for r in raw if isinstance(r, dict)]
    cards.sort(key=lambda c: c.created_at or datetime.min, reverse=True)
    logger.info(
        "Reviews fetched (flat): %s items, period=%s",
        len(cards),
        pretty,
    )
    return cards, pretty


def _build_review_view(cards: List[ReviewCard], index: int, pretty: str) -> ReviewView:
    if not cards:
        return ReviewView(
            text="Отзывы за указанный период не найдены.",
            index=0,
            total=0,
            period=pretty,
        )

    safe_index = max(0, min(index, len(cards) - 1))
    text = _format_review_card_text(cards[safe_index], safe_index, len(cards), pretty)
    text = trim_for_telegram(text)
    return ReviewView(text=text, index=safe_index, total=len(cards), period=pretty)


async def get_review_view(
    user_id: int,
    period_key: str = "recent",
    index: int = 0,
    client: OzonClient | None = None,
) -> ReviewView:
    """Вернуть представление отдельной карточки отзыва."""

    cards, pretty = await fetch_recent_reviews(client)
    return _build_review_view(cards, index, pretty)


async def shift_review_view(
    user_id: int,
    period_key: str,
    step: int,
    client: OzonClient | None = None,
) -> ReviewView:
    # Периоды и переключение карточек больше не используются; возвращаем актуальный список
    return await get_review_view(user_id, period_key, 0, client)



async def get_reviews_page(
    *,
    mode: str = MODE_UNANSWERED,
    page: int = 0,
    client: OzonClient | None = None,
    days: int = DEFAULT_RECENT_DAYS,
) -> ReviewsPage:
    cards, pretty = await fetch_recent_reviews(client, days=days)
    return _format_reviews_page(cards, mode=mode, page=page, pretty=pretty)


async def get_reviews_today(client: OzonClient | None = None):  # back-compat
    return await fetch_recent_reviews(client)


async def get_reviews_week(client: OzonClient | None = None):  # back-compat
    return await fetch_recent_reviews(client)


async def get_reviews_month(client: OzonClient | None = None):  # back-compat
    return await fetch_recent_reviews(client)


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
    _, _, pretty = _msk_range_last_days(DEFAULT_RECENT_DAYS)
    return f"⭐ Отзывы (последние {DEFAULT_RECENT_DAYS} дней)\n{pretty}"


__all__ = [
    "ReviewCard",
    "ReviewsPage",
    "MODE_ANSWERED",
    "MODE_UNANSWERED",
    "fetch_recent_reviews",
    "trim_for_telegram",
    "get_reviews_page",
    "get_reviews_today",
    "get_reviews_week",
    "get_reviews_month",
    "get_ai_reply_for_review",
    "get_reviews_menu_text",
]
