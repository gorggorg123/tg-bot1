# botapp/reviews.py
from __future__ import annotations

import hashlib
import html
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from botapp.ozon_client import OzonClient, get_client

logger = logging.getLogger(__name__)

PAGE_SIZE = 8
CACHE_TTL_SECONDS = 35
REVIEWS_DAYS_BACK = int((os.getenv("REVIEWS_DAYS_BACK") or "30").strip() or "30")


@dataclass(slots=True)
class ReviewCard:
    id: str
    created_at: str | None = None
    updated_at: str | None = None
    rating: int | None = None
    text: str | None = None
    product_name: str | None = None
    sku: str | None = None

    has_answer: bool = False
    seller_comment: str | None = None

    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class ReviewsView:
    text: str
    period: str
    index: int = 0
    total: int = 0


@dataclass(slots=True)
class ReviewsCache:
    fetched_at: datetime | None = None
    all_reviews: list[ReviewCard] = field(default_factory=list)
    views: dict[str, list[str]] = field(default_factory=dict)

    token_to_rid: dict[str, str] = field(default_factory=dict)
    rid_to_token: dict[str, str] = field(default_factory=dict)


_USER_RCACHE: dict[int, ReviewsCache] = {}


def _rc(user_id: int) -> ReviewsCache:
    c = _USER_RCACHE.get(user_id)
    if c is None:
        c = ReviewsCache()
        _USER_RCACHE[user_id] = c
    return c


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cache_fresh(dt: datetime | None, ttl: int = CACHE_TTL_SECONDS) -> bool:
    if not dt:
        return False
    return (_now_utc() - dt) <= timedelta(seconds=int(ttl))


def _escape(s: str) -> str:
    return html.escape((s or "").strip())


def _trim(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _short_token(user_id: int, review_id: str) -> str:
    cache = _rc(user_id)
    rid = str(review_id).strip()
    if not rid:
        return ""
    if rid in cache.rid_to_token:
        return cache.rid_to_token[rid]
    t = hashlib.blake2s(f"{user_id}:r:{rid}".encode("utf-8"), digest_size=8).hexdigest()
    cache.rid_to_token[rid] = t
    cache.token_to_rid[t] = rid
    return t


def encode_review_id(user_id: int, review_id: str) -> str:
    return _short_token(user_id, review_id)


def resolve_review_id(user_id: int, token: str | None) -> str | None:
    if not token:
        return None
    return _rc(user_id).token_to_rid.get(token)


def _parse_dt_iso(s: str | None) -> float:
    if not s:
        return 0.0
    v = str(s).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _build_views(items: list[ReviewCard]) -> dict[str, list[str]]:
    all_ids: list[str] = []
    answered: list[str] = []
    unanswered: list[str] = []

    for r in items:
        if not r or not r.id:
            continue
        all_ids.append(r.id)
        if bool(r.has_answer) or bool((r.seller_comment or "").strip()):
            answered.append(r.id)
        else:
            unanswered.append(r.id)

    return {"all": all_ids, "answered": answered, "unanswered": unanswered}


def _pretty_period_title(cache: ReviewsCache) -> str:
    if not cache.fetched_at:
        return "Отзывы"
    stamp = cache.fetched_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return f"Отзывы (обновлено: {stamp})"


def find_review(user_id: int, review_id: str) -> ReviewCard | None:
    rid = str(review_id).strip()
    if not rid:
        return None
    for r in _rc(user_id).all_reviews:
        if r.id == rid:
            return r
    return None


def _extract_list_items(payload: dict) -> list[dict]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    if isinstance(result, dict):
        for key in ("items", "reviews", "review_list", "list"):
            v = result.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    if isinstance(result, list):
        return [x for x in result if isinstance(x, dict)]
    return []


def _to_review_card(raw: dict) -> ReviewCard | None:
    rid = raw.get("id") or raw.get("review_id")
    rid_s = str(rid).strip() if rid not in (None, "") else ""
    if not rid_s:
        return None

    rating = raw.get("rating") or raw.get("score")
    try:
        rating_i = int(rating) if rating not in (None, "") else None
    except Exception:
        rating_i = None

    txt = raw.get("text") or raw.get("comment") or raw.get("review_text") or raw.get("content")
    txt_s = str(txt).strip() if isinstance(txt, str) else None

    product_name = raw.get("product_name") or raw.get("item_name") or raw.get("product_title") or raw.get("name")
    product_name_s = str(product_name).strip() if product_name not in (None, "") else None

    created_at = raw.get("created_at") or raw.get("published_at") or raw.get("date")
    updated_at = raw.get("updated_at") or raw.get("updated")

    seller_comment = raw.get("seller_comment") or raw.get("comment_seller") or raw.get("answer_text")
    seller_comment_s = str(seller_comment).strip() if isinstance(seller_comment, str) and seller_comment.strip() else None

    has_answer = bool(seller_comment_s)
    if "is_answered" in raw:
        try:
            has_answer = bool(raw.get("is_answered")) or has_answer
        except Exception:
            pass

    sku = raw.get("sku")
    sku_s = str(sku).strip() if sku not in (None, "") else None

    return ReviewCard(
        id=rid_s,
        created_at=str(created_at).strip() if created_at not in (None, "") else None,
        updated_at=str(updated_at).strip() if updated_at not in (None, "") else None,
        rating=rating_i,
        text=txt_s,
        product_name=product_name_s,
        sku=sku_s,
        has_answer=has_answer,
        seller_comment=seller_comment_s,
        raw=raw,
    )


async def refresh_reviews(user_id: int, *, force: bool = False) -> None:
    cache = _rc(user_id)
    if not force and cache.all_reviews and _cache_fresh(cache.fetched_at):
        return

    client = get_client()

    date_end = _now_utc()
    date_start = date_end - timedelta(days=max(1, int(REVIEWS_DAYS_BACK)))

    payload = await client.review_list(
        date_start=date_start.isoformat(),
        date_end=date_end.isoformat(),
        limit=100,
    )
    if not isinstance(payload, dict):
        payload = {"result": payload}

    raw_items = _extract_list_items(payload)
    items: list[ReviewCard] = []
    for it in raw_items:
        r = _to_review_card(it)
        if r:
            items.append(r)

    items.sort(key=lambda r: _parse_dt_iso(r.updated_at or r.created_at), reverse=True)

    cache.all_reviews = items
    cache.views = _build_views(items)
    cache.fetched_at = _now_utc()

    seller_comments.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    latest_dt, latest_text = seller_comments[0]
    card.answer_text = latest_text
    card.answer_created_at = latest_dt or card.answer_created_at
    card.status = card.status or "ANSWERED"
    card.answered = True


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
    product = safe_strip(card.product_name)
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
    name = safe_strip(name_raw) if name_raw is not None else ""

    article_label, article_value = _product_article(card)

    if name:
        return name[:47] + "…" if len(name) > 50 else name
    if article_label and article_value:
        title = "Артикул" if article_label == "SKU" else article_label
        return f"{title}: {article_value}"
    if card.product_id:
        return f"ID: {card.product_id}"
    return "—"


async def ensure_review_product_name(card: ReviewCard) -> None:
    """Попробовать подтянуть название товара для карточки отзыва."""

    if not card or safe_strip(card.product_name):
        return

    product_id = card.offer_id or card.product_id
    if not product_id:
        return

    try:
        client = get_client()
    except Exception as exc:  # pragma: no cover - нет кредов/клиента
        logger.debug("Cannot init Ozon client for review product: %s", exc)
        return

    try:
        name = await client.get_product_name(str(product_id))
    except Exception as exc:  # pragma: no cover - сеть/HTTP
        logger.debug("Failed to load product name for review %s: %s", card.id, exc)
        return

    if name:
        card.product_name = name


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
    status_icon, status_label = _status_badge(card)
    answer_text = safe_strip(current_answer) or safe_strip(card.answer_text)
    answer_dt = _fmt_dt_msk(card.answer_created_at)

    title_parts = [f"{stars}"]
    if date_line:
        title_parts.append(date_line)
    title = " • ".join(title_parts) if title_parts else "Отзыв"

    text_body = card.text or "(пустой отзыв)"
    answer_lines: list[str] = []
    answer_block_title = "Ответ продавца"
    if current_answer:
        answer_block_title = "Черновик ответа"
    elif is_answered(card, user_id):
        answer_block_title = "Ответ продавца (опубликован на Ozon)"

    if answer_text:
        header = answer_block_title
        if answer_dt:
            header = f"Ответ продавца от {answer_dt}"
        answer_lines.extend([header + ":", answer_text])
    else:
        answer_lines.append(f"{answer_block_title}: Ответа продавца пока нет.")

    lines = [
        f"{title} • {period_title}",
        "",
        f"Позиция: {product_line}",
        "",
        "Текст отзыва:",
        text_body,
        "",
        f"Статус: {status_icon} {status_label}",
        "",
        *answer_lines,
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


async def refresh_reviews_from_api(user_id: int) -> None:
    await refresh_reviews(user_id, force=True)


async def refresh_review_from_api(card: ReviewCard, client: OzonClient) -> None:
    if not card or not card.id:
        return

    try:
        info = await client.review_info(card.id)
        if isinstance(info, dict):
            if not card.text:
                t = info.get("text") or info.get("comment") or info.get("content")
                if isinstance(t, str) and t.strip():
                    card.text = t.strip()
            if not card.product_name:
                pn = info.get("product_name") or info.get("product_title") or info.get("item_name")
                if pn not in (None, ""):
                    card.product_name = str(pn).strip()
            if card.rating is None:
                try:
                    card.rating = int(info.get("rating")) if info.get("rating") not in (None, "") else None
                except Exception:
                    pass
            sc = info.get("seller_comment") or info.get("comment_seller")
            if isinstance(sc, str) and sc.strip():
                card.seller_comment = sc.strip()
                card.has_answer = True
    except Exception:
        pass

    if not (card.seller_comment or "").strip():
        try:
            comments = await client.review_comment_list(card.id, limit=50)
            result = comments.get("result") if isinstance(comments, dict) and isinstance(comments.get("result"), dict) else comments
            raw = []
            if isinstance(result, dict):
                raw = result.get("items") or result.get("comments") or []
            if isinstance(raw, list):
                for c in raw:
                    if not isinstance(c, dict):
                        continue
                    author = str(c.get("author_type") or c.get("type") or c.get("author") or "").lower()
                    txt = c.get("text")
                    if isinstance(txt, str) and txt.strip() and ("seller" in author or "vendor" in author):
                        card.seller_comment = txt.strip()
                        card.has_answer = True
                        break
        except Exception:
            pass


def _label_for_list_item(r: ReviewCard) -> str:
    icon = "✅" if (r.has_answer or (r.seller_comment or "").strip()) else "🟡"
    stars = ""
    if r.rating is not None:
        stars = f"{int(r.rating)}/5"
    prod = _trim((r.product_name or "").replace("\n", " "), 22)
    txt = _trim((r.text or "").replace("\n", " "), 46)
    if prod:
        return f"{icon} {stars} {prod}: {txt}".strip()
    return f"{icon} {stars} {txt}".strip()


async def get_reviews_table(*, user_id: int, category: str, page: int) -> tuple[str, list[dict], int, int]:
    await refresh_reviews(user_id)

    c = _rc(user_id)
    ids = c.views.get(category) or c.views.get("all") or []
    total = len(ids)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))

    start = safe_page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_ids = ids[start:end]

    items: list[dict] = []
    for i, rid in enumerate(page_ids, start=start):
        r = find_review(user_id, rid)
        if not r:
            continue
        token = _short_token(user_id, r.id)
        items.append({"token": token, "label": _label_for_list_item(r), "index": i})

    title = _pretty_period_title(c)
    header = f"<b>{title}</b>\n"
    header += f"Категория: <b>{_escape(category)}</b>\n"
    header += f"Всего: <b>{total}</b> | Страница: <b>{safe_page + 1}/{total_pages}</b>\n\n"
    header += "Выберите отзыв:"

    return header, items, safe_page, total_pages


async def get_review_and_card(user_id: int, category: str, *, index: int, review_id: str | None = None) -> tuple[ReviewsView, ReviewCard | None]:
    await refresh_reviews(user_id)

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

    SNIPPET_MAX_LEN = 100
    category_label = {
        "unanswered": "Без ответа",
        "answered": "С ответом",
    }.get((category or "all").lower(), "Все")

    if not cards:
        return (
            "Отзывы не найдены за выбранный период.",
            [],
            0,
            0,
        )

    slice_items, safe_page, total_pages = _slice_cards(cards, page, page_size)
    rows: List[str] = [
        "⭐ Отзывы покупателей",
        f"Период: {pretty_period}",
        "",
        f"Категория: {category_label}",
        "",
        f"Страница {safe_page + 1}/{total_pages}",
        "",
    ]
    items: List[tuple[str, str | None, int]] = []

    for idx, card in enumerate(slice_items):
        global_index = safe_page * page_size + idx
        status_icon, status_text = _status_badge(card)
        stars = f"{card.rating}★" if card.rating else "—"
        product_short = safe_str(_pick_short_product_label(card))
        snippet_raw = safe_strip(card.text)
        snippet = snippet_raw or "—"
        if len(snippet) > SNIPPET_MAX_LEN:
            snippet = snippet[: SNIPPET_MAX_LEN - 1] + "…"
        date_part = _fmt_dt_msk(card.created_at) or "дата неизвестна"
        age = _human_age(card.created_at)
        age_part = f" ({age})" if age else ""
        status_label = status_text.upper() if status_text else ""
        label = (
            f"{status_icon} {stars} | {date_part}{age_part} | "
            f"Товар: {product_short} | {status_label or 'СТАТУС НЕИЗВЕСТЕН'}"
        )
        label = f"{label} | Отзыв: {snippet}"
        token = _get_review_token(user_id, card.id)
        items.append((label, token, global_index))

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

    if total == 0:
        return ReviewsView(text=f"<b>{period}</b>\n\nПока нет отзывов в категории <b>{_escape(category)}</b>.", period=period, total=0), None

    if review_id:
        card = find_review(user_id, review_id)
        if card:
            try:
                idx = ids.index(review_id)
            except ValueError:
                idx = 0
            view = ReviewsView(text="", period=period, index=idx, total=total)
            return view, card

    idx = max(0, min(int(index), total - 1))
    rid = ids[idx]
    card = find_review(user_id, rid)
    view = ReviewsView(text="", period=period, index=idx, total=total)
    return view, card


def format_review_card_text(*, card: ReviewCard, index: int, total: int, period_title: str, user_id: int, current_answer: str | None) -> str:
    rid = _escape(card.id)
    created = _escape(card.created_at or "—")
    prod = _escape(card.product_name or "—")
    rating = str(card.rating) if card.rating is not None else "—"

    status = "✅ С ответом" if (card.has_answer or (card.seller_comment or "").strip()) else "🟡 Без ответа"

    review_text = _escape(card.text or "—")
    ozon_answer = _escape((card.seller_comment or "").strip()) if (card.seller_comment or "").strip() else "—"
    draft = (current_answer or "").strip()

    parts: list[str] = []
    parts.append(f"<b>{_escape(period_title)}</b>")
    parts.append(f"{status}  •  {index + 1}/{max(1, total)}")
    parts.append(f"🆔 <code>{rid}</code>")
    parts.append(f"🕒 {created}")
    parts.append(f"🧾 Товар: {prod}")
    parts.append(f"⭐ Оценка: <b>{_escape(rating)}</b>")

    parts.append("\n<b>Текст отзыва:</b>\n" + _trim(review_text, 3400))
    parts.append("\n<b>Ответ в Ozon:</b>\n" + _trim(ozon_answer, 1600))

    if draft:
        parts.append("\n<b>Текущий черновик:</b>\n" + _trim(_escape(draft), 1600))

    parts.append(
        "\n<i>Подсказка:</i> «ИИ-ответ» создаёт черновик. "
        "«Пересобрать» учитывает ваши пожелания. «Отправить» публикует ответ в Ozon."
    )

    return _trim("\n".join(parts), 3900)


def mark_review_answered(review_id: str, user_id: int, text: str | None = None) -> None:
    rid = str(review_id).strip()
    if not rid:
        return
    c = _rc(user_id)
    r = find_review(user_id, rid)
    if not r:
        return
    r.has_answer = True
    if text and str(text).strip():
        r.seller_comment = str(text).strip()

    c.views = _build_views(c.all_reviews)


__all__ = [
    "ReviewCard",
    "ReviewsView",
    "refresh_reviews",
    "refresh_reviews_from_api",
    "refresh_review_from_api",
    "get_reviews_table",
    "get_review_and_card",
    "format_review_card_text",
    "mark_review_answered",
    "encode_review_id",
    "resolve_review_id",
    "format_review_card_text",
    "build_reviews_preview",
    "refresh_review_from_api",
    "ensure_review_product_name",
]
