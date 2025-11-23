# botapp/reviews.py
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Tuple

from .ai_client import AIClientError, generate_review_reply
from .ozon_client import OzonClient, get_client

logger = logging.getLogger(__name__)

DEFAULT_RECENT_DAYS = 30
MAX_REVIEW_LEN = 450
MAX_REVIEWS_LOAD = 2000  # было 200, увеличено, чтобы брать больше свежих отзывов
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)
TELEGRAM_SOFT_LIMIT = 4000
REVIEWS_PAGE_SIZE = 10
SESSION_TTL = timedelta(minutes=2)

_product_name_cache: dict[str, str | None] = {}
_review_answered_cache: dict[int, set[str]] = {}
_sessions: dict[int, "ReviewSession"] = {}
# NEW: Короткие токены для review_id, чтобы callback_data помещалась в лимит Telegram
_review_id_to_token: dict[int, dict[str, str]] = {}
_token_to_review_id: dict[int, dict[str, str]] = {}

API_ANALYTICS_SAMPLE_LIMIT = 5


@dataclass
class ReviewCard:
    id: str | None
    rating: int
    text: str
    product_name: str | None
    offer_id: str | None
    product_id: str | None
    created_at: datetime | None
    raw_created_at: Any | None = None
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
    page: Dict[str, int] = field(default_factory=lambda: {"all": 0, "unanswered": 0, "answered": 0})
    loaded_at: datetime = field(default_factory=datetime.utcnow)
    product_cache: Dict[str, str | None] = field(default_factory=dict)

    def rebuild_unanswered(self, user_id: int) -> None:
        self.unanswered_reviews = [c for c in self.all_reviews if not is_answered(c, user_id)]


def _parse_date(value: Any) -> datetime | None:
    """Привести дату из Ozon к aware-UTC datetime.

    Поддерживаем ISO-строки, timestamp в секундах/миллисекундах и вложенные
    словари. Наивные даты считаем UTC и только после этого конвертируем.
    """

    if value is None or value == "":
        return None

    if isinstance(value, dict):
        nested = next(
            (
                value.get(key)
                for key in ("value", "created_at", "createdAt", "date", "datetime")
                if value.get(key) not in (None, "")
            ),
            None,
        )
        if nested is None:
            return None
        return _parse_date(nested)

    # Числовой timestamp (секунды или миллисекунды)
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            ts = ts / 1000 if ts > 10**11 else ts
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None

    txt = str(value).strip()
    if not txt:
        return None

    # Строка с timestamp
    if txt.isdigit():
        try:
            num = int(txt)
            ts = num / 1000 if num > 10**11 else num
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None

    # ISO-строка (включая варианты с пробелом и Z)
    try:
        dt = datetime.fromisoformat(txt.replace(" ", "T").replace("Z", "+00:00"))
    except Exception:
        return None

    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _to_msk(dt: datetime | None) -> datetime | None:
    base_dt = _to_utc(dt)
    return base_dt.astimezone(MSK_TZ) if base_dt else None


def _msk_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(MSK_TZ)


def _ensure_msk(dt: datetime | date | None, *, end_of_day: bool = False) -> datetime | None:
    """Привести границы периода к datetime в МСК.

    Поддерживает как ``datetime``, так и ``date``. Для ``date`` добавляется
    ``time.min`` либо ``time.max`` в зависимости от флага ``end_of_day``.
    """

    if dt is None:
        return None

    base_dt: datetime
    if isinstance(dt, datetime):
        base_dt = dt
    elif isinstance(dt, date):
        base_dt = datetime.combine(dt, time.max if end_of_day else time.min)
    else:
        return None

    if base_dt.tzinfo:
        return base_dt.astimezone(MSK_TZ)
    return base_dt.replace(tzinfo=MSK_TZ)


def _fmt_dt_msk(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt_msk = _to_msk(dt)
    if not dt_msk:
        return ""
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _human_age(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt_msk = _to_msk(dt)
    if not dt_msk:
        return ""
    dt_msk_date = dt_msk.date()
    today_msk = _msk_now().date()
    days = (today_msk - dt_msk_date).days
    if days < 0:
        return "из будущего"
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    return f"{days} дн. назад"


def _msk_range_last_days(days: int = DEFAULT_RECENT_DAYS) -> Tuple[datetime, datetime, str]:
    """Вернуть диапазон последних *days* дней в МСК (начиная с полуночи)."""
    now_msk = _msk_now()
    start_msk = datetime(now_msk.year, now_msk.month, now_msk.day, tzinfo=MSK_TZ) - timedelta(
        days=days - 1
    )
    end_msk = now_msk
    pretty = f"{start_msk:%d.%m.%Y} 00:00 — {end_msk:%d.%m.%Y %H:%M} (МСК)"
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


def _has_answer_payload(review: ReviewCard) -> bool:
    return bool((review.answer_text or "").strip() or review.answered)


def is_answered(review: ReviewCard, user_id: int | None = None) -> bool:
    user_cache = _answered_for_user(user_id or 0)
    if review.id and review.id in user_cache:
        return True
    return _has_answer_payload(review)


def _reset_review_tokens(user_id: int) -> None:
    """Очистить токены отзывов для пользователя (при обновлении списка)."""

    _review_id_to_token[user_id] = {}
    _token_to_review_id[user_id] = {}


def _base36(num: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    digits = []
    while num:
        num, rem = divmod(num, 36)
        digits.append(alphabet[rem])
    return "".join(reversed(digits))


def _get_review_token(user_id: int, review_id: str | None) -> str | None:
    """Выдать короткий токен для review_id, чтобы уместиться в 64 байта callback."""

    if not review_id:
        return None
    bucket = _review_id_to_token.setdefault(user_id, {})
    if review_id in bucket:
        return bucket[review_id]

    # Хэшируем и переводим в base36, ограничиваем длину, чтобы избежать переполнения
    raw = abs(hash((user_id, review_id)))
    token = _base36(raw)[:8]
    if not token:
        token = "r0"

    # Разрешаем редкие коллизии, добавляя суффикс
    used = _token_to_review_id.setdefault(user_id, {})
    suffix = 0
    while token in used and used[token] != review_id:
        suffix += 1
        token_candidate = f"{token[:6]}{_base36(suffix)[:2]}"
        token = token_candidate[:8]

    bucket[review_id] = token
    used[token] = review_id
    return token


def resolve_review_id(user_id: int, review_ref: str | None) -> str | None:
    """Преобразовать токен из callback обратно в реальный review_id."""

    if not review_ref:
        return None
    mapping = _token_to_review_id.get(user_id, {})
    return mapping.get(review_ref, review_ref)


def encode_review_id(user_id: int, review_id: str | None) -> str | None:
    """Вернуть короткий токен для review_id."""

    return _get_review_token(user_id, review_id)


def mark_review_answered(review_id: str | None, user_id: int, answer_text: str | None = None) -> None:
    if review_id:
        _answered_for_user(user_id).add(review_id)

    session = _sessions.get(user_id)
    if not session:
        return

    for card in session.all_reviews:
        if review_id and card.id == review_id:
            card.answered = True
            if answer_text is not None:
                card.answer_text = answer_text

    session.rebuild_unanswered(user_id)


def _filter_reviews_and_stats(
    reviews: List[ReviewCard],
    *,
    period_from_msk: datetime | date | None = None,
    period_to_msk: datetime | date | None = None,
    answer_filter: str = "all",
) -> tuple[List[ReviewCard], dict[str, int]]:
    """Отфильтровать отзывы и вернуть счётчики исключений."""

    from_is_date = isinstance(period_from_msk, date) and not isinstance(period_from_msk, datetime)
    to_is_date = isinstance(period_to_msk, date) and not isinstance(period_to_msk, datetime)

    safe_from_msk = _ensure_msk(period_from_msk) if period_from_msk else None
    safe_to_msk = _ensure_msk(period_to_msk, end_of_day=to_is_date) if period_to_msk else None

    # Если оба края заданы датами, сравниваем по календарным датам, чтобы избежать
    # пограничных рассинхронизаций из-за времени в течение дня.
    compare_by_date = from_is_date or to_is_date

    if safe_from_msk and safe_to_msk and safe_from_msk > safe_to_msk:
        safe_from_msk, safe_to_msk = safe_to_msk, safe_from_msk

    stats = {
        "missing_dates": 0,
        "dropped_by_date": 0,
        "answered": 0,
        "unanswered": 0,
    }

    filtered: list[ReviewCard] = []
    collected_dates: list[datetime] = []
    seen_dates: list[datetime] = []

    for review in reviews:
        created_utc = _to_utc(review.created_at)
        created_msk = created_utc.astimezone(MSK_TZ) if created_utc else None

        if created_msk:
            seen_dates.append(created_msk)

        if safe_from_msk or safe_to_msk:
            if created_msk is None:
                stats["missing_dates"] += 1
                continue

            if compare_by_date:
                created_key = created_msk.date()
                from_key = safe_from_msk.date() if safe_from_msk else None
                to_key = safe_to_msk.date() if safe_to_msk else None
            else:
                created_key = created_msk
                from_key = safe_from_msk
                to_key = safe_to_msk

            if from_key and created_key < from_key:
                stats["dropped_by_date"] += 1
                continue
            if to_key and created_key > to_key:
                stats["dropped_by_date"] += 1
                continue

            collected_dates.append(created_msk)

        if answer_filter == "unanswered" and _has_answer_payload(review):
            continue
        if answer_filter == "answered" and not _has_answer_payload(review):
            continue

        filtered.append(review)

    stats["answered"] = sum(1 for r in filtered if _has_answer_payload(r))
    stats["unanswered"] = len(filtered) - stats["answered"]

    if (safe_from_msk or safe_to_msk) and collected_dates:
        earliest = min(collected_dates)
        latest = max(collected_dates)
        logger.debug(
            "Filter window=%s..%s (MSK), seen_in_window=%s..%s (MSK) before drops",
            safe_from_msk,
            safe_to_msk,
            earliest,
            latest,
        )

    if seen_dates:
        min_seen, max_seen = min(seen_dates), max(seen_dates)
        logger.debug(
            "Observed review dates (MSK) span %s..%s across %s items",
            min_seen,
            max_seen,
            len(seen_dates),
        )

    return filtered, stats


def filter_reviews(
    reviews: List[ReviewCard],
    *,
    period_from_msk: datetime | date | None = None,
    period_to_msk: datetime | date | None = None,
    answer_filter: str = "all",
) -> List[ReviewCard]:
    """Отфильтровать отзывы по периоду (МСК) и наличию ответа."""

    filtered, _ = _filter_reviews_and_stats(
        reviews,
        period_from_msk=period_from_msk,
        period_to_msk=period_to_msk,
        answer_filter=answer_filter,
    )
    return filtered


def _range_summary_msk(values: list[datetime]) -> str:
    if not values:
        return "—"
    earliest = min(values)
    latest = max(values)
    return f"{earliest} — {latest}"


def _normalize_review(raw: Dict[str, Any]) -> ReviewCard:
    rating = int(raw.get("rating") or raw.get("grade") or 0)
    text = (raw.get("text") or raw.get("comment") or "").strip()
    text = str(text)
    if len(text) > MAX_REVIEW_LEN:
        text = text[: MAX_REVIEW_LEN - 1] + "…"

    answer_payload = (
        raw.get("answer")
        or raw.get("reply")
        or raw.get("response")
        or raw.get("seller_answer")
        or {}
    )
    answer_text = ""
    if isinstance(answer_payload, dict):
        answer_text = str(answer_payload.get("text") or answer_payload.get("comment") or "").strip()
    elif isinstance(answer_payload, str):
        answer_text = answer_payload.strip()

    answered_flag = raw.get("answered") or raw.get("has_answer") or raw.get("is_answered")
    answered = bool(answer_payload or answered_flag)

    product_block_raw = raw.get("product") or raw.get("product_info") or {}
    product_block = product_block_raw if isinstance(product_block_raw, dict) else {}
    product_id_raw = (
        raw.get("sku")
        or product_block.get("sku")
        or raw.get("product_id")
        or product_block.get("product_id")
    )
    offer_id_raw = (
        product_block.get("offer_id")
        or raw.get("offer_id")
        or raw.get("sku")
        or raw.get("product_id")
    )
    product_name_fields = [
        product_block.get("name"),
        product_block.get("title"),
        product_block.get("product_name"),
        product_block.get("productTitle"),
        product_block.get("product_title"),
        raw.get("product_title"),
        raw.get("product_name"),
        raw.get("title"),
        raw.get("product_title_text"),
        raw.get("productTitle"),
        raw.get("name"),
    ]
    product_name = next((str(v).strip() for v in product_name_fields if v not in (None, "")), None)

    # NEW: приводим идентификаторы к строкам, чтобы избежать ошибок .strip() для int
    offer_id = str(offer_id_raw) if offer_id_raw is not None else None
    product_id = str(product_id_raw) if product_id_raw is not None else None

    created_candidates = [
        raw.get("created_at"),
        raw.get("createdAt"),
        raw.get("creation_date"),
        raw.get("created_date"),
        raw.get("date"),
        raw.get("published_at"),
        raw.get("submitted_at"),
    ]
    raw_created_at = next((v for v in created_candidates if v not in (None, "")), None)
    created_at = next((dt for dt in (_parse_date(v) for v in created_candidates) if dt), None)

    return ReviewCard(
        id=str(raw.get("id") or raw.get("review_id") or raw.get("uuid") or "") or None,
        rating=rating,
        text=text,
        product_name=product_name,
        offer_id=offer_id,
        product_id=product_id,
        created_at=created_at,
        raw_created_at=raw_created_at,
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


def _product_article(card: ReviewCard) -> tuple[str | None, str | None]:
    if card.offer_id:
        return "SKU", card.offer_id
    if card.product_id:
        return "ID", card.product_id
    return None, None


def _pick_product_label(card: ReviewCard) -> str:
    product = (card.product_name or "").strip()
    article_label, article_value = _product_article(card)

    if product and article_label and article_value:
        return f"{product} ({article_label}: {article_value})"
    if product and card.product_id:
        return f"{product} (ID: {card.product_id})"
    if product:
        return product
    if article_label and article_value:
        title = "Артикул" if article_label == "SKU" else article_label
        return f"{title}: {article_value}"
    if card.product_id:
        return f"ID: {card.product_id}"
    return "— (название недоступно)"


def _pick_short_product_label(card: ReviewCard) -> str:
    """Короткое имя товара для таблицы."""

    name_raw = card.product_name
    name = str(name_raw).strip() if name_raw is not None else ""

    article_label, article_value = _product_article(card)

    if name:
        return name[:47] + "…" if len(name) > 50 else name
    if article_label and article_value:
        title = "Артикул" if article_label == "SKU" else article_label
        return f"{title}: {article_value}"
    if card.product_id:
        return f"ID: {card.product_id}"
    return "—"


def format_review_card_text(
    *,
    card: ReviewCard,
    index: int,
    total: int,
    period_title: str,
    user_id: int,
    current_answer: str | None = None,
) -> str:
    """Сформировать карточку одного отзыва с блоком текущего ответа."""

    date_line = _fmt_dt_msk(card.created_at)
    stars = f"{card.rating}★" if card.rating else "—"
    product_line = _pick_product_label(card)
    status = "Есть ответ" if is_answered(card, user_id) else "Без ответа"
    answer_text = current_answer or card.answer_text or "(ответа пока нет)"

    title_parts = [f"{stars}"]
    if date_line:
        title_parts.append(date_line)
    title = " • ".join(title_parts) if title_parts else "Отзыв"

    text_body = card.text or "(пустой отзыв)"
    lines = [
        f"{title} • {period_title}",
        "",
        f"Позиция: {product_line}",
        "",
        "Текст отзыва:",
        text_body,
        "",
        f"Статус: {status}",
        "",
        "Текущий ответ:",
        answer_text,
    ]
    if card.id:
        lines.insert(3, f"ID отзыва: {card.id}")

    body = "\n".join(lines).strip()
    return trim_for_telegram(body)


def trim_for_telegram(text: str, max_len: int = TELEGRAM_SOFT_LIMIT) -> str:
    if len(text) <= max_len:
        return text
    suffix = "… (обрезано)"
    return text[: max_len - len(suffix)] + suffix


def _iso_no_ms(dt: datetime) -> str:
    """ISO-строка без миллисекунд с суффиксом Z для UTC."""

    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    else:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _iso_date(dt: datetime) -> str:
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.date().isoformat()


async def _resolve_product_names(
    cards: List[ReviewCard],
    client: OzonClient,
    product_cache: Dict[str, str | None] | None = None,
    *,
    analytics_from: str | None = None,
    analytics_to: str | None = None,
) -> None:
    cache = product_cache if product_cache is not None else {}

    sku_set: set[str] = set()
    for card in cards:
        if not card.product_id and card.offer_id:
            card.product_id = card.offer_id

        sku = card.product_id
        if not sku:
            continue

        if card.product_name:
            cache.setdefault(sku, card.product_name)
            _product_name_cache.setdefault(sku, card.product_name)
            continue

        cached_name = cache.get(sku)
        if cached_name is None and sku in _product_name_cache:
            cached_name = _product_name_cache[sku]
            cache[sku] = cached_name

        if cached_name:
            card.product_name = cached_name
            continue

        sku_set.add(sku)

    fetched_map: Dict[str, str] = {}
    if sku_set and analytics_from and analytics_to:
        try:
            status, fetched_map, _ = await client.get_sku_title_map(
                analytics_from, analytics_to, limit=1000, offset=0
            )
            if status != 200:
                logger.warning(
                    "SKU analytics HTTP %s while resolving %s products", status, len(sku_set)
                )
                fetched_map = {}
        except Exception as exc:
            logger.warning("Failed to fetch SKU analytics: %s", exc)

    for sku, title in fetched_map.items():
        cache[sku] = title
        _product_name_cache[sku] = title

    for card in cards:
        if card.product_name:
            continue

        sku = card.product_id or card.offer_id
        if not sku:
            continue

        cached_name = cache.get(sku)
        if cached_name is None and sku in _product_name_cache:
            cached_name = _product_name_cache.get(sku)
            cache.setdefault(sku, cached_name)

        if cached_name:
            card.product_name = cached_name

    missing_ids: set[str] = {
        card.product_id
        for card in cards
        if card.product_id and not card.product_name and card.product_id not in cache and card.product_id not in _product_name_cache
    }

    for product_id in missing_ids:
        name: str | None = None
        try:
            name = await client.get_product_name(product_id)
        except Exception as exc:
            logger.warning("Failed to fetch product name for %s: %s", product_id, exc)

        cache[product_id] = name
        _product_name_cache[product_id] = name

        if name:
            for card in cards:
                if card.product_id == product_id and not card.product_name:
                    card.product_name = name


async def fetch_recent_reviews(
    client: OzonClient | None = None,
    *,
    days: int = DEFAULT_RECENT_DAYS,
    limit_per_page: int = 100,
    max_reviews: int = MAX_REVIEWS_LOAD,
    product_cache: Dict[str, str | None] | None = None,
) -> Tuple[List[ReviewCard], str]:
    """Загрузить отзывы за последние *days* дней одним списком."""

    client = client or get_client()
    product_cache = product_cache if product_cache is not None else {}
    since_msk, to_msk, pretty = _msk_range_last_days(days)
    # Берём чуть шире окно для запроса, чтобы не потерять отзывы на границах
    fetch_since_msk = since_msk - timedelta(days=2)
    fetch_from_utc = _to_utc(fetch_since_msk)
    fetch_to_utc = _to_utc(to_msk)
    analytics_from = _iso_date(fetch_from_utc or fetch_since_msk)
    analytics_to = _iso_date(fetch_to_utc or to_msk)
    raw = await client.get_reviews(
        fetch_from_utc or fetch_since_msk,
        fetch_to_utc or to_msk,
        limit_per_page=limit_per_page,
        max_count=max_reviews,
    )
    if raw:
        # DEBUG: один пример для сверки схемы ReviewAPI, чтобы не спамить логи
        logger.debug("Sample review payload: %r", raw[0])
    cards = [_normalize_review(r) for r in raw if isinstance(r, dict)]
    filter_from_date = since_msk.date()
    filter_to_date = to_msk.date()

    filtered_cards, stats = _filter_reviews_and_stats(
        cards,
        period_from_msk=filter_from_date,
        period_to_msk=filter_to_date,
        answer_filter="all",
    )
    await _resolve_product_names(
        filtered_cards,
        client,
        product_cache,
        analytics_from=analytics_from,
        analytics_to=analytics_to,
    )
    filtered_cards.sort(
        key=lambda c: _to_msk(c.created_at) or datetime.min.replace(tzinfo=MSK_TZ),
        reverse=True,
    )

    unanswered_count = len(filter_reviews(filtered_cards, answer_filter="unanswered"))
    answered_count = len(filter_reviews(filtered_cards, answer_filter="answered"))

    raw_dates_utc = [dt for dt in (_to_utc(c.created_at) for c in cards) if dt]
    raw_dates_msk = [dt.astimezone(MSK_TZ) for dt in raw_dates_utc]
    filtered_dates_msk = [dt for dt in (_to_msk(c.created_at) for c in filtered_cards) if dt]

    year_counts: dict[int, int] = {}
    for dt in raw_dates_utc:
        year_counts[dt.year] = year_counts.get(dt.year, 0) + 1

    logger.info(
        "Reviews fetched from API: %s items (UTC range: %s — %s) | filter_msk_dates=%s..%s",
        len(raw),
        (fetch_from_utc or fetch_since_msk).isoformat(),
        (fetch_to_utc or to_msk).isoformat(),
        filter_from_date,
        filter_to_date,
    )

    logger.info(
        "Reviews date span (MSK): raw=%s filtered=%s | raw_span_utc=%s | year_counts=%s",
        _range_summary_msk(raw_dates_msk),
        _range_summary_msk(filtered_dates_msk),
        _range_summary_msk(raw_dates_utc),
        year_counts,
    )

    if stats.get("dropped_by_date") == len(cards) and cards:
        logger.warning(
            "All reviews dropped by date: window_msk=%s..%s, raw_span=%s",
            since_msk,
            to_msk,
            _range_summary_msk(raw_dates_msk),
        )

    debug_dates = False
    if debug_dates:
        for sample in filtered_cards[:5]:
            logger.info(
                "Review debug: id=%s created_at_raw=%r created_at_parsed=%s created_at_msk=%s",
                sample.id,
                sample.raw_created_at,
                sample.created_at,
                _to_msk(sample.created_at),
            )
    logger.info(
        "Reviews after filter: %s items for period=%s (МСК), filter=all | unanswered=%s | answered=%s | missing_dates=%s | dropped_by_date=%s",
        len(filtered_cards),
        pretty,
        unanswered_count,
        answered_count,
        stats.get("missing_dates", 0),
        stats.get("dropped_by_date", len(cards) - len(filtered_cards)),
    )
    return filtered_cards, pretty


def _slice_cards(cards: List[ReviewCard], page: int, page_size: int) -> tuple[List[ReviewCard], int, int]:
    total = len(cards)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * page_size
    end = start + page_size
    return cards[start:end], safe_page, total_pages


def build_reviews_table(
    *,
    cards: List[ReviewCard],
    pretty_period: str,
    category: str,
    user_id: int,
    page: int = 0,
    page_size: int = REVIEWS_PAGE_SIZE,
) -> tuple[str, List[tuple[str, str | None, int]], int, int]:
    """Собрать текст таблицы и кнопки для списка отзывов."""

    if not cards:
        return (
            "Отзывы не найдены за выбранный период.",
            [],
            0,
            0,
        )

    slice_items, safe_page, total_pages = _slice_cards(cards, page, page_size)
    rows: List[str] = [f"⭐ Отзывы ({category})", pretty_period, ""]
    items: List[tuple[str, str | None, int]] = []

    for idx, card in enumerate(slice_items):
        global_index = safe_page * page_size + idx
        status_icon = "✅" if is_answered(card, user_id) else "✏️"
        status_text = "Есть ответ" if is_answered(card, user_id) else "Без ответа"
        stars = f"{card.rating}★" if card.rating else "—"
        product_short = _pick_short_product_label(card)
        snippet = (card.text or "").strip()
        if len(snippet) > 50:
            snippet = snippet[:47] + "…"
        date_part = _fmt_dt_msk(card.created_at) or "дата неизвестна"
        age = _human_age(card.created_at)
        age_part = f" ({age})" if age else ""
        label = (
            f"{status_icon} {stars} | {date_part}{age_part} | "
            f"Товар: {product_short} | {status_text}"
        )
        if snippet:
            label = f"{label} | {snippet}"
        token = _get_review_token(user_id, card.id)
        items.append((label, token, global_index))

    rows.append(f"Страница {safe_page + 1}/{total_pages}")
    text = "\n".join(rows)
    return trim_for_telegram(text), items, safe_page, total_pages


def _build_review_view(cards: List[ReviewCard], index: int, pretty: str, user_id: int) -> ReviewView:
    if not cards:
        return ReviewView(
            text="Отзывы за выбранный период не найдены.",
            index=0,
            total=0,
            period=pretty,
        )

    safe_index = max(0, min(index, len(cards) - 1))
    text = format_review_card_text(
        card=cards[safe_index],
        index=safe_index,
        total=len(cards),
        period_title=pretty,
        user_id=user_id,
    )
    return ReviewView(text=text, index=safe_index, total=len(cards), period=pretty)


async def _ensure_session(user_id: int, client: OzonClient | None = None) -> ReviewSession:
    session = _sessions.get(user_id)
    now = datetime.utcnow()

    if session and (now - session.loaded_at) < SESSION_TTL:
        return session

    product_cache: Dict[str, str | None] = {}
    cards, pretty = await fetch_recent_reviews(client, product_cache=product_cache)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c, user_id)],
        pretty_period=pretty,
        loaded_at=now,
        product_cache=product_cache,
    )
    _reset_review_tokens(user_id)
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


async def get_reviews_table(
    *, user_id: int, category: str = "all", page: int = 0, client: OzonClient | None = None
) -> tuple[str, list[tuple[str, str | None, int]], int, int]:
    session = await _ensure_session(user_id, client)
    cards = _get_cards_for_category(session, category, user_id)
    text, items, safe_page, total_pages = build_reviews_table(
        cards=cards,
        pretty_period=session.pretty_period,
        category=category,
        user_id=user_id,
        page=page,
    )
    session.page[category] = safe_page
    return text, items, safe_page, total_pages


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
    now = datetime.utcnow()
    product_cache: Dict[str, str | None] = {}
    cards, pretty = await fetch_recent_reviews(client, product_cache=product_cache)
    session = ReviewSession(
        all_reviews=cards,
        unanswered_reviews=[c for c in cards if not is_answered(c, user_id)],
        pretty_period=pretty,
        loaded_at=now,
        product_cache=product_cache,
    )
    _reset_review_tokens(user_id)
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
        "Выберите список: новые без ответа или все за период."
        f"\n{pretty}"
    )


def _collect_review_sku(
    review: Dict[str, Any]
) -> tuple[str | None, Any, Dict[str, Any], Any]:
    product_block_raw = review.get("product") or review.get("product_info") or {}
    product_block = product_block_raw if isinstance(product_block_raw, dict) else {}
    raw_fields = review.get("raw_fields") if isinstance(review.get("raw_fields"), dict) else {}

    sku_candidates = [
        review.get("sku"),
        product_block.get("sku"),
        product_block.get("offer_id"),
        product_block.get("offerId"),
        review.get("product_id"),
        review.get("productId"),
        raw_fields.get("sku"),
    ]
    sku_raw = next((v for v in sku_candidates if v not in (None, "")), None)
    sku = str(sku_raw).strip() if sku_raw is not None else None

    product_id_orig = review.get("product_id") or product_block.get("product_id") or raw_fields.get("product_id")

    return sku, product_id_orig, product_block, sku_raw


async def build_reviews_preview(
    *, days: int = DEFAULT_RECENT_DAYS, client: OzonClient | None = None
) -> Dict[str, Any]:
    """Построить JSON-ответ по отзывам и аналитике, аналогичный Cloudflare Worker."""

    safe_days = max(1, days)
    client = client or get_client()

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    date_start = now - timedelta(days=safe_days)
    date_end = now

    date_start_iso = _iso_no_ms(date_start)
    date_end_iso = _iso_no_ms(date_end)
    analytics_from = _iso_date(date_start)
    analytics_to = _iso_date(date_end)

    ozon_status, reviews_payload = await client.get_reviews_page(
        date_start_iso, date_end_iso, limit=100, page=1
    )

    response: Dict[str, Any] = {
        "date_from": date_start_iso,
        "date_to": date_end_iso,
        "ozon_status": ozon_status,
        "reviews_count": 0,
        "product_titles_count": 0,
        "reviews_preview": [],
        "analytics_status": None,
        "analytics_error": None,
        "analytics_sample": [],
    }

    if ozon_status != 200 or not isinstance(reviews_payload, dict):
        response["ozon_status"] = f"HTTP {ozon_status}"
        response["analytics_error"] = "Отзывы недоступны"
        return response

    reviews_root = reviews_payload.get("result") if isinstance(reviews_payload.get("result"), dict) else reviews_payload
    items = reviews_root.get("reviews") or reviews_root.get("feedbacks") or reviews_root.get("items") or []
    reviews_list = [r for r in items if isinstance(r, dict)] if isinstance(items, list) else []

    previews: list[Dict[str, Any]] = []
    sku_set: set[str] = set()

    for rev in reviews_list:
        review_id = rev.get("review_id") or rev.get("id") or rev.get("uuid")
        rating = int(rev.get("rating") or 0)
        text = str(rev.get("text") or rev.get("comment") or "")

        sku, product_id_raw, raw_product, raw_sku = _collect_review_sku(rev)
        if sku:
            sku_set.add(sku)

        previews.append(
            {
                "review_id": str(review_id) if review_id is not None else None,
                "rating": rating,
                "text": text,
                "product_id": sku,
                "product_name": None,
                "raw_product": raw_product if raw_product else None,
                "raw_fields": {"product_id": product_id_raw, "sku": raw_sku},
            }
        )

    response["reviews_preview"] = previews
    response["reviews_count"] = len(previews)

    analytics_status: int | None = None
    analytics_error: str | None = None
    sku_title_map: Dict[str, str] = {}
    analytics_sample: list[Any] = []

    if sku_set:
        try:
            analytics_status, sku_title_map, sample_rows = await client.get_sku_title_map(
                analytics_from, analytics_to, limit=1000, offset=0
            )
            analytics_sample = sample_rows[:API_ANALYTICS_SAMPLE_LIMIT]
            if analytics_status != 200:
                analytics_error = f"Analytics HTTP {analytics_status}"
        except Exception as exc:  # pragma: no cover - сетевые ошибки
            analytics_error = str(exc)

    if sku_title_map:
        for item in previews:
            pid = item.get("product_id")
            if pid and pid in sku_title_map:
                item["product_name"] = sku_title_map.get(pid)

    response["product_titles_count"] = len(sku_title_map)
    response["analytics_status"] = analytics_status
    response["analytics_error"] = analytics_error
    response["analytics_sample"] = analytics_sample

    return response


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
    "get_reviews_table",
    "refresh_reviews",
    "get_ai_reply_for_review",
    "mark_review_answered",
    "is_answered",
    "encode_review_id",
    "resolve_review_id",
    "format_review_card_text",
    "build_reviews_preview",
]

