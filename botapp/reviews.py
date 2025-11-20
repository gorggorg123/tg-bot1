# botapp/reviews.py
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from .ai_client import AIClientError, generate_review_reply
from .ozon_client import OzonClient, get_client

logger = logging.getLogger(__name__)

DEFAULT_RECENT_DAYS = 30
MAX_REVIEW_LEN = 450
MAX_REVIEWS_LOAD = 200
MSK_SHIFT = timedelta(hours=3)
TELEGRAM_SOFT_LIMIT = 3500

_product_name_cache: dict[str, str] = {}
_review_answered_cache: set[str] = set()
_sessions: dict[int, "ReviewSession"] = {}


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
    answer_text: str | None = None


@dataclass
class ReviewView:
    text: str
    index: int
    total: int
    period: str


@dataclass
class ReviewSession:
    all_reviews: List[ReviewCard] = field(default_factory=list)
    unanswered_reviews: List[ReviewCard] = field(default_factory=list)
    pretty_period: str = ""
    indexes: Dict[str, int] = field(default_factory=lambda: {"all": 0, "unanswered": 0})


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


def is_answered(review: ReviewCard) -> bool:
    if review.id and review.id in _review_answered_cache:
        return True
    return bool(review.answered or (review.answer_text and review.answer_text.strip()))


def mark_review_answered(review_id: str | None) -> None:
    if review_id:
        _review_answered_cache.add(review_id)
    # Обновим сессии, чтобы отзыв пропал из непрочитанных
    for session in _sessions.values():
        session.unanswered_reviews = [c for c in session.all_reviews if not is_answered(c)]


def _normalize_review(raw: Dict[str, Any]) -> ReviewCard:
    rating = int(raw.get("rating") or raw.get("grade") or 0)
    text = (raw.get("text") or raw.get("comment") or "").strip()
    text = str(text)
    if len(text) > MAX_REVIEW_LEN:
        text = text[: MAX_REVIEW_LEN - 1] + "…"

    answer_payload = raw.get("answer") or raw.get("reply") or raw.get("response") or {}
    answer_text = ""
    if isinstance(answer_payload, dict):
        answer_text = str(answer_payload.get("text") or answer_payload.get("comment") or "").strip()
    elif isinstance(answer_payload, str):
        answer_text = answer_payload.strip()

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
        answer_text=answer_text or None,
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


def _pick_product_label(card: ReviewCard) -> str:
    product = card.product_name or ""
    if not product:
        if card.product_id:
            return f"{card.product_id} (имя недоступно)"
        if card.offer_id:
            return f"{card.offer_id} (имя недоступно)"
        return "—"
    return product


def _format_review_card_text(card: ReviewCard, index: int, total: int, period_title: str) -> str:
    """Сформировать карточку одного отзыва."""

    date_line = _fmt_dt_msk(card.created_at)
    stars = f"{card.rating}★" if card.rating else "—"
    status = "Есть ответ продавца" if is_answered(card) else "Без ответа"
    product_line = _pick_product_label(card)

    lines = [
        f"⭐ Отзыв {index + 1} из {total} • период: {period_title}",
        "",
        f"Рейтинг: {stars}",
        f"Товар: {product_line}",
    ]

    if card.id:
        lines.append(f"ID отзыва: {card.id}")
    if date_line:
        lines.append(f"Дата: {date_line} (МСК)")

    lines.extend([
        "",
        "Текст отзыва:",
        card.text or "(пустой отзыв)",
        "",
        f"Статус ответа: {status}",
    ])

    if card.answer_text:
        lines.extend([
            "",
            "Ответ продавца:",
            card.answer_text,
        ])

    return "\n".join(lines).strip()


def trim_for_telegram(text: str, max_len: int = TELEGRAM_SOFT_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


async def _resolve_product_names(cards: List[ReviewCard], client: OzonClient) -> None:
    missing_ids = [c.product_id for c in cards if c.product_id and not c.product_name]
    unique_ids = [pid for pid in dict.fromkeys(missing_ids) if pid]
    for pid in unique_ids:
        if pid in _product_name_cache:
            continue
        try:
            title = await client.get_product_name(pid)
        except Exception:
            logger.exception("Failed to fetch product name for %s", pid)
            title = None
        if title:
            _product_name_cache[pid] = title

    for card in cards:
        if card.product_id and not card.product_name:
            card.product_name = _product_name_cache.get(card.product_id) or card.product_name


async def fetch_recent_reviews(
    client: OzonClient | None = None,
    *,
    days: int = DEFAULT_RECENT_DAYS,
    limit_per_page: int = 80,
    max_reviews: int = MAX_REVIEWS_LOAD,
) -> Tuple[List[ReviewCard], str]:
    """Загрузить отзывы за последние *days* дней одним списком."""

    client = client or get_client()
    since_msk, to_msk, pretty = _msk_range_last_days(days)
    raw = await client.get_reviews(
        since_msk,
        to_msk,
        limit_per_page=limit_per_page,
        max_count=max_reviews,
    )
    cards = [_normalize_review(r) for r in raw if isinstance(r, dict)]
    await _resolve_product_names(cards, client)
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


async def _ensure_session(user_id: int, client: OzonClient | None = None) -> ReviewSession:
    if user_id in _sessions:
        return _sessions[user_id]

    cards, pretty = await fetch_recent_reviews(client)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c)],
        pretty_period=pretty,
    )
    _sessions[user_id] = session
    return session


def _get_cards_for_category(session: ReviewSession, category: str) -> List[ReviewCard]:
    if category == "unanswered":
        session.unanswered_reviews = [c for c in session.all_reviews if not is_answered(c)]
        return session.unanswered_reviews
    return session.all_reviews


async def get_review_view(
    user_id: int,
    category: str = "unanswered",
    index: int = 0,
    client: OzonClient | None = None,
) -> ReviewView:
    session = await _ensure_session(user_id, client)
    cards = _get_cards_for_category(session, category)
    view = _build_review_view(cards, index, session.pretty_period)
    session.indexes[category] = view.index
    return view


async def get_review_and_card(
    user_id: int,
    category: str,
    index: int,
    client: OzonClient | None = None,
) -> tuple[ReviewView, ReviewCard | None]:
    session = await _ensure_session(user_id, client)
    cards = _get_cards_for_category(session, category)
    view = _build_review_view(cards, index, session.pretty_period)
    session.indexes[category] = view.index
    card = cards[view.index] if cards else None
    return view, card


async def shift_review_view(
    user_id: int,
    category: str,
    step: int,
    client: OzonClient | None = None,
) -> ReviewView:
    session = await _ensure_session(user_id, client)
    current = session.indexes.get(category, 0)
    new_index = current + step
    return await get_review_view(user_id, category, new_index, client)


async def get_review_by_index(
    user_id: int,
    category: str,
    index: int,
    client: OzonClient | None = None,
) -> ReviewCard | None:
    _, card = await get_review_and_card(user_id, category, index, client)
    return card


async def refresh_reviews(user_id: int, client: OzonClient | None = None) -> ReviewSession:
    cards, pretty = await fetch_recent_reviews(client)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c)],
        pretty_period=pretty,
    )
    _sessions[user_id] = session
    return session


async def get_ai_reply_for_review(review: ReviewCard) -> str:
    return await generate_review_reply(
        review_text=review.text,
        product_name=review.product_name,
        rating=review.rating,
    )


async def get_reviews_menu_text() -> str:
    _, _, pretty = _msk_range_last_days(DEFAULT_RECENT_DAYS)
    return (
        "⭐ Отзывы\n"
        "Выберите список: новые без ответа или все за период."\
        f"\n{pretty}"
    )


__all__ = [
    "ReviewCard",
    "ReviewView",
    "fetch_recent_reviews",
    "trim_for_telegram",
    "get_review_view",
    "shift_review_view",
    "get_review_and_card",
    "get_review_by_index",
    "refresh_reviews",
    "get_ai_reply_for_review",
    "get_reviews_menu_text",
    "mark_review_answered",
    "is_answered",
]
