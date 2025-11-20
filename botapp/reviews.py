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
TELEGRAM_SOFT_LIMIT = 4000

_product_name_cache: dict[str, str] = {}
_review_answered_cache: dict[int, set[str]] = {}
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
    indexes: Dict[str, int] = field(default_factory=lambda: {"all": 0, "unanswered": 0, "answered": 0})

    def rebuild_unanswered(self, user_id: int) -> None:
        self.unanswered_reviews = [c for c in self.all_reviews if not is_answered(c, user_id)]


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


def _answered_for_user(user_id: int) -> set[str]:
    """Вернуть (и при необходимости восстановить) кэш отвеченных отзывов для пользователя."""

    global _review_answered_cache
    if not isinstance(_review_answered_cache, dict):
        logger.warning("Answered cache corrupted, resetting it")
        _review_answered_cache = {}

    bucket = _review_answered_cache.get(user_id)
    if not isinstance(bucket, set):
        bucket = set(bucket or [])
        _review_answered_cache[user_id] = bucket
    return bucket


def is_answered(review: ReviewCard, user_id: int | None = None) -> bool:
    user_cache = _answered_for_user(user_id or 0)
    if review.id and review.id in user_cache:
        return True
    return bool(review.answered or (review.answer_text and review.answer_text.strip()))


def mark_review_answered(review_id: str | None, user_id: int) -> None:
    if review_id:
        _answered_for_user(user_id).add(review_id)
    # Обновим сессии, чтобы отзыв пропал из непрочитанных
    for uid, session in _sessions.items():
        session.rebuild_unanswered(uid)


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
    product_id = card.product_id or card.offer_id
    product = card.product_name or ""
    if product:
        return f"{product} (ID: {product_id})" if product_id else product
    if product_id:
        return f"{product_id} (название недоступно)"
    return "— (название недоступно)"


def _format_review_card_text(card: ReviewCard, index: int, total: int, period_title: str, user_id: int) -> str:
    """Сформировать карточку одного отзыва."""

    date_line = _fmt_dt_msk(card.created_at)
    stars = f"{card.rating}★" if card.rating else "—"
    status = "Есть ответ продавца" if is_answered(card, user_id) else "Без ответа"
    product_line = _pick_product_label(card)

    lines = [f"⭐ Отзыв {index + 1}/{total}"]
    if date_line:
        lines.append(f"Дата: {date_line} (МСК)")
    lines.extend(
        [
            f"Рейтинг: {stars}",
            f"Позиция: {product_line}",
        ]
    )

    if card.id:
        lines.append(f"ID отзыва: {card.id}")

    lines.extend([
        "",
        "Текст отзыва:",
        card.text or "(пустой отзыв)",
        "",
        f"Статус: {status}",
    ])

    if card.answer_text:
        lines.extend([
            "",
            "Ответ покупателю:",
            card.answer_text,
        ])

    return "\n".join(lines).strip()


def trim_for_telegram(text: str, max_len: int = TELEGRAM_SOFT_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    suffix = "… (обрезано)"
    return text[: max_len - len(suffix)] + suffix


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
        else:
            logger.warning("Product name not resolved for %s", pid)

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


def _build_review_view(cards: List[ReviewCard], index: int, pretty: str, user_id: int) -> ReviewView:
    if not cards:
        return ReviewView(
            text="Отзывы за выбранный период не найдены.",
            index=0,
            total=0,
            period=pretty,
        )

    safe_index = max(0, min(index, len(cards) - 1))
    text = _format_review_card_text(cards[safe_index], safe_index, len(cards), pretty, user_id)
    text = trim_for_telegram(text)
    return ReviewView(text=text, index=safe_index, total=len(cards), period=pretty)


async def _ensure_session(user_id: int, client: OzonClient | None = None) -> ReviewSession:
    if user_id in _sessions:
        return _sessions[user_id]

    cards, pretty = await fetch_recent_reviews(client)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c, user_id)],
        pretty_period=pretty,
    )
    _sessions[user_id] = session
    return session


def _get_cards_for_category(session: ReviewSession, category: str, user_id: int) -> List[ReviewCard]:
    if category == "unanswered":
        session.rebuild_unanswered(user_id)
        return session.unanswered_reviews
    if category == "answered":
        return [c for c in session.all_reviews if is_answered(c, user_id)]
    return session.all_reviews


def _find_card_by_id(cards: List[ReviewCard], review_id: str | None) -> tuple[int, ReviewCard] | tuple[int, None]:
    if not review_id:
        return 0, cards[0] if cards else None
    for idx, card in enumerate(cards):
        if card.id == review_id:
            return idx, card
    return 0, cards[0] if cards else None


async def get_review_view(
    user_id: int,
    category: str = "unanswered",
    index: int = 0,
    client: OzonClient | None = None,
) -> ReviewView:
    session = await _ensure_session(user_id, client)
    cards = _get_cards_for_category(session, category, user_id)
    view = _build_review_view(cards, index, session.pretty_period, user_id)
    session.indexes[category] = view.index
    return view


async def get_review_and_card(
    user_id: int,
    category: str,
    index: int,
    client: OzonClient | None = None,
    review_id: str | None = None,
) -> tuple[ReviewView, ReviewCard | None]:
    session = await _ensure_session(user_id, client)
    cards = _get_cards_for_category(session, category, user_id)
    if review_id:
        index, card = _find_card_by_id(cards, review_id)
    else:
        card = cards[index] if cards else None
    view = _build_review_view(cards, index, session.pretty_period, user_id)
    session.indexes[category] = view.index
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


async def get_review_by_id(
    user_id: int,
    category: str,
    review_id: str | None,
    client: OzonClient | None = None,
) -> tuple[ReviewCard | None, int]:
    view, card = await get_review_and_card(user_id, category, 0, client, review_id=review_id)
    return card, view.index


async def refresh_reviews(user_id: int, client: OzonClient | None = None) -> ReviewSession:
    cards, pretty = await fetch_recent_reviews(client)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c, user_id)],
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
    "get_review_by_id",
    "get_review_by_index",
    "refresh_reviews",
    "get_ai_reply_for_review",
    "mark_review_answered",
    "is_answered",
]
