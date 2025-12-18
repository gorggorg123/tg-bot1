# botapp/questions.py
from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from botapp.ozon_client import Question, QuestionAnswer, get_question_answers, get_questions_list

logger = logging.getLogger(__name__)

PAGE_SIZE = 8
CACHE_TTL_SECONDS = 35
DEFAULT_STATUS = "unanswered"


@dataclass
class QuestionsCache:
    fetched_at: datetime | None = None
    all_questions: list[Question] = field(default_factory=list)
    views: dict[str, list[str]] = field(default_factory=dict)

    token_to_qid: dict[str, str] = field(default_factory=dict)
    qid_to_token: dict[str, str] = field(default_factory=dict)


_USER_QCACHE: dict[int, QuestionsCache] = {}


def _qc(user_id: int) -> QuestionsCache:
    c = _USER_QCACHE.get(user_id)
    if c is None:
        c = QuestionsCache()
        _USER_QCACHE[user_id] = c
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


def _short_token(user_id: int, question_id: str) -> str:
    cache = _qc(user_id)
    qid = str(question_id).strip()
    if not qid:
        return ""
    if qid in cache.qid_to_token:
        return cache.qid_to_token[qid]
    t = hashlib.blake2s(f"{user_id}:q:{qid}".encode("utf-8"), digest_size=8).hexdigest()
    cache.qid_to_token[qid] = t
    cache.token_to_qid[t] = qid
    return t


def resolve_question_token(user_id: int, token: str | None) -> Question | None:
    if not token:
        return None
    qid = _qc(user_id).token_to_qid.get(token)
    if not qid:
        return None
    return find_question(user_id, qid)


def resolve_question_id(user_id: int, question_id: str | None) -> Question | None:
    if not question_id:
        return None
    return find_question(user_id, str(question_id).strip())


def _build_views(items: list[Question]) -> dict[str, list[str]]:
    all_ids: list[str] = []
    answered: list[str] = []
    unanswered: list[str] = []

    for q in items:
        if not q or not q.id:
            continue
        all_ids.append(q.id)
        if bool(q.has_answer) or bool((q.answer_text or "").strip()):
            answered.append(q.id)
        else:
            unanswered.append(q.id)

    return {"all": all_ids, "answered": answered, "unanswered": unanswered}


async def refresh_questions(user_id: int, *, force: bool = False) -> None:
    cache = _qc(user_id)
    if not force and cache.all_questions and _cache_fresh(cache.fetched_at):
        return

    items = await get_questions_list(status="all", limit=200, offset=0)
    items = [q for q in items if q and q.id]

    def _k(q: Question) -> float:
        s = (q.updated_at or q.created_at or "").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0

    items.sort(key=_k, reverse=True)

    cache.all_questions = items
    cache.views = _build_views(items)
    cache.fetched_at = _now_utc()

    cache.token_to_qid.clear()
    cache.qid_to_token.clear()
    for q in items:
        _short_token(user_id, q.id)


async def _ensure_cache(user_id: int) -> None:
    c = _qc(user_id)
    if not c.all_questions:
        await refresh_questions(user_id, force=True)


def get_questions_pretty_period(user_id: int) -> str:
    c = _qc(user_id)
    if not c.fetched_at:
        return "Вопросы"
    stamp = c.fetched_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return f"Вопросы (обновлено: {stamp})"


def find_question(user_id: int, question_id: str) -> Question | None:
    qid = str(question_id).strip()
    if not qid:
        return None
    for q in _qc(user_id).all_questions:
        if q.id == qid:
            return q
    return None


async def ensure_question_answer_text(q: Question, *, user_id: int) -> None:
    if not q or not q.id:
        return
    if (q.answer_text or "").strip():
        return
    if not bool(q.has_answer):
        return

    sku = None
    try:
        sku = int(q.sku) if (q.sku or "").isdigit() else None
    except Exception:
        sku = None

    try:
        answers = await get_question_answers(q.id, sku=sku, limit=1)
    except Exception:
        return
    if not answers:
        return
    a = answers[0]
    if (a.text or "").strip():
        q.answer_text = a.text
        q.answer_id = a.id
        q.has_answer = True


def get_question_by_index(user_id: int, category: str, index: int) -> tuple[Question | None, int, int]:
    c = _qc(user_id)
    ids = c.views.get(category) or c.views.get(DEFAULT_STATUS) or c.views.get("all") or []
    total = len(ids)
    if total == 0:
        return None, 0, 0
    idx = max(0, min(int(index), total - 1))
    qid = ids[idx]
    return find_question(user_id, qid), idx, total


def _label_for_list_item(q: Question) -> str:
    prefix = "🟡" if not (q.has_answer or (q.answer_text or "").strip()) else "✅"
    prod = _trim((q.product_name or "").replace("\n", " "), 22)
    text = _trim((q.question_text or "").replace("\n", " "), 46)
    if prod:
        return f"{prefix} {prod}: {text}"
    return f"{prefix} {text}"


async def get_questions_table(*, user_id: int, category: str, page: int) -> tuple[str, list[dict], int, int]:
    await _ensure_cache(user_id)
    c = _qc(user_id)

    ids = c.views.get(category) or c.views.get(DEFAULT_STATUS) or c.views.get("all") or []
    total = len(ids)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    safe_page = max(0, min(int(page), total_pages - 1))

    start = safe_page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_ids = ids[start:end]

    items: list[dict] = []
    for i, qid in enumerate(page_ids, start=start):
        q = find_question(user_id, qid)
        if not q:
            continue
        token = _short_token(user_id, q.id)
        items.append({"token": token, "label": _label_for_list_item(q), "index": i})

    first = answers[0]
    question.answer_text = first.text or question.answer_text
    question.answer_id = first.id or question.answer_id
    question.has_answer = bool(question.answer_text)
    question.answers_count = question.answers_count or len(answers)

    if session:
        session.answer_cache[question.id] = {
            "text": question.answer_text,
            "answer_id": question.answer_id,
            "answers_count": question.answers_count,
        }


async def ensure_question_product_name(question: Question) -> None:
    """Попробовать дополнить вопрос названием товара для карточки/списка."""

    if not question or safe_strip(getattr(question, "product_name", None)):
        return

    product_id = getattr(question, "product_id", None) or getattr(question, "sku", None)
    if not product_id:
        return

    try:
        client = get_client()
    except Exception as exc:  # pragma: no cover - если нет кредов на чтение
        logger.debug("Cannot init Ozon client for question product: %s", exc)
        return

    try:
        name = await client.get_product_name(str(product_id))
    except Exception as exc:  # pragma: no cover - сеть/HTTP
        logger.debug("Failed to load product name for question %s: %s", question.id, exc)
        return

    if name:
        question.product_name = name


# ---------------------------------------------------------------------------
# Пагинация и таблица списка вопросов
# ---------------------------------------------------------------------------


async def get_questions_table(
    *,
    user_id: int,
    category: str,
    page: int = 0,
) -> tuple[str, List[tuple[str, str, int]], int, int]:
    """Вернуть (text, items, current_page, total_pages) для списка вопросов."""

    questions = _get_cached_questions(user_id, category)
    if not questions:
        questions = await refresh_questions(user_id, category)

    session = _get_session(user_id)
    pretty_period = session.pretty_period or "период не определён"

    text, items, safe_page, total_pages = build_questions_table(
        cards=questions,
        pretty_period=pretty_period,
        category=(category or "all").lower(),
        page=page,
        page_size=QUESTIONS_PAGE_SIZE,
    )

    return text, items, safe_page, total_pages


# ---------------------------------------------------------------------------
# Поиск вопроса по индексу / ID
# ---------------------------------------------------------------------------


    return header, items, safe_page, total_pages


def format_question_card_text(q: Question, *, answer_override: str | None, period_title: str) -> str:
    created = (q.created_at or "").strip()
    sku = (q.sku or "").strip()
    prod = _escape(q.product_name or "—")
    qtext = _escape(q.question_text or "—")

    has_answer = bool(q.has_answer) or bool((q.answer_text or "").strip())
    status = "✅ С ответом" if has_answer else "🟡 Без ответа"

    ozon_answer = (q.answer_text or "").strip()
    draft = (answer_override or "").strip()

def resolve_question_id(user_id: int, question_id: str) -> Optional[Question]:
    """Backward-совместимый helper: сейчас просто find_question."""
    return find_question(user_id, question_id)


# ---------------------------------------------------------------------------
# Форматирование карточки вопроса
# ---------------------------------------------------------------------------


def format_question_card_text(
    question: Question,
    answer_override: Optional[str] = None,
    answers_count: Optional[int] = None,
    *,
    period_title: str,
) -> str:
    """Собираем человекочитаемую карточку вопроса для Telegram."""

    created = _parse_date(getattr(question, "created_at", None))
    product_name = getattr(question, "product_name", None) or "—"
    status_icon, status_text = _status_badge_question(question)

    sku_part = ""
    sku_value = safe_strip(getattr(question, "sku", None))
    if sku_value:
        sku_part = f" (SKU: {sku_value})"

    header_date = _fmt_dt_msk(created) or "—"

    product_label = safe_str(product_name)
    if not product_label:
        pid = getattr(question, "product_id", None) or getattr(question, "sku", None)
        product_label = safe_str(pid) or "—"

    lines: List[str] = [
        f"❓ • {header_date} • {period_title}",
        f"Позиция: {product_label}{sku_part}",
        f"ID вопроса: {getattr(question, 'id', '—')}",
        "",
        "Текст вопроса:",
        getattr(question, "question_text", None)
        or getattr(question, "text", None)
        or getattr(question, "message", None)
        or "—",
        "",
    ]

    answer_text = safe_strip(answer_override) or safe_strip(getattr(question, "answer_text", None))
    answer_block_title = "Ответ продавца"
    if answer_override:
        answer_block_title = "Черновик ответа"
    elif getattr(question, "has_answer", False) or answer_text:
        answer_block_title = "Ответ продавца (опубликован на Ozon)"
    lines.extend(
        [
            f"Статус: {status_icon} {status_text}",
            "",
            f"{answer_block_title}:",
            answer_text or "Ответа продавца пока нет.",
        ]
    )

    return "\n".join(lines)


def _slice_questions(items: List[Question], page: int, page_size: int) -> tuple[List[Question], int, int]:
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * page_size
    end = start + page_size
    return items[start:end], safe_page, total_pages


def _status_badge_question(q: Question) -> tuple[str, str]:
    if (
        getattr(q, "has_answer", False)
        or safe_strip(getattr(q, "answer_text", None))
        or (getattr(q, "status", "") or "").upper() == "PROCESSED"
    ):
        return "✅", "Ответ есть"
    return "✏️", "Без ответа"


def _pick_short_product_label_question(q: Question) -> str:
    raw_product_name = getattr(q, "product_name", None)
    product_name = safe_strip(raw_product_name)
    raw_sku = getattr(q, "sku", None)
    sku = safe_strip(raw_sku)
    if product_name:
        return product_name[:50] + ("…" if len(product_name) > 50 else "")
    if sku:
        return f"Артикул: {sku}"
    return "—"


def build_questions_table(
    *,
    cards: List[Question],
    pretty_period: str,
    category: str,
    page: int = 0,
    page_size: int = QUESTIONS_PAGE_SIZE,
) -> tuple[str, List[tuple[str, str, int]], int, int]:
    """Собрать текст таблицы и кнопки для списка вопросов."""

    SNIPPET_MAX_LEN = 100
    TELEGRAM_TEXT_LIMIT = 4096

    slice_items, safe_page, total_pages = _slice_questions(cards, page, page_size)
    category_label = {
        "unanswered": "Без ответа",
        "answered": "С ответом",
    }.get((category or "all").lower(), "Все")
    rows: List[str] = [
        "❓ Вопросы покупателей",
        f"Период: {pretty_period}",
        "",
        f"Категория: {category_label}",
        "",
        f"Страница {safe_page + 1}/{total_pages}",
        "",
    ]
    items: List[tuple[str, str, int]] = []

    for idx, q in enumerate(slice_items, start=1):
        global_index = safe_page * page_size + (idx - 1)
        status_icon, status_text = _status_badge_question(q)
        product_short = safe_str(_pick_short_product_label_question(q))
        snippet_raw = safe_strip(
            getattr(q, "question_text", None)
            or getattr(q, "text", None)
            or getattr(q, "message", None)
            or ""
        )
        snippet = snippet_raw or "—"
        if len(snippet) > SNIPPET_MAX_LEN:
            snippet = snippet[: SNIPPET_MAX_LEN - 1] + "…"
        created_at = _parse_date(getattr(q, "created_at", None))
        date_part = _fmt_dt_msk(created_at) or "дата неизвестна"
        age = _human_age(created_at)
        age_part = f" ({age})" if age else ""
        status_label = status_text.upper() if status_text else ""
        line = (
            f"{idx}) {status_icon} {date_part}{age_part} | "
            f"Товар: {product_short} | {status_label or 'СТАТУС НЕИЗВЕСТЕН'}"
        )
        line = f"{line} | Вопрос: {snippet}"
        rows.append(line)

        question_id = getattr(q, "id", None)
        if not question_id:
            continue
        button_label = f"{idx}{status_icon}" if status_icon else str(idx)
        items.append((button_label, safe_str(question_id), global_index))

    text = "\n".join(rows)
    if len(text) > TELEGRAM_TEXT_LIMIT:
        truncated_rows: List[str] = []
        current_length = 0
        suffix = " (обрезано)"

        for row in rows:
            if not truncated_rows:
                projected_length = len(row)
            else:
                projected_length = current_length + 1 + len(row)

            if projected_length > TELEGRAM_TEXT_LIMIT - len(suffix):
                break

            truncated_rows.append(row)
            current_length = projected_length

        if truncated_rows:
            truncated_rows[-1] = f"{truncated_rows[-1]}{suffix}"
        else:
            truncated_rows.append(suffix.strip())

        text = "\n".join(truncated_rows)

    return text, items, safe_page, total_pages


def get_questions_pretty_period(user_id: int) -> str:
    session = _get_session(user_id)
    return session.pretty_period or ""


# ---------------------------------------------------------------------------
# Токены для компактных callback_data
# ---------------------------------------------------------------------------

    parts.append("\n<b>Вопрос покупателя:</b>\n" + _trim(qtext, 3400))

    parts.append("\n<b>Ответ в Ozon:</b>\n" + (_trim(_escape(ozon_answer), 1800) if ozon_answer else "—"))

    if draft:
        parts.append("\n<b>Текущий черновик:</b>\n" + _trim(_escape(draft), 1800))

    parts.append(
        "\n<i>Подсказка:</i> «ИИ-ответ» генерирует черновик. "
        "«Пересобрать» учитывает ваши пожелания. «Отправить» публикует ответ в Ozon."
    )
    return _trim("\n".join(parts), 3900)


__all__ = [
    "refresh_questions",
    "get_questions_pretty_period",
    "get_questions_table",
    "format_question_card_text",
    "ensure_question_answer_text",
    "ensure_question_product_name",
    "register_question_token",
    "resolve_question_token",
    "resolve_question_id",
]
