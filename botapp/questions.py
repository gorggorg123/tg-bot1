# botapp/questions.py
"""Helpers for loading and formatting customer questions from Ozon.

Логика максимально похожа на модуль с отзывами:
- кешируем вопросы по user_id,
- поддерживаем категории (all / unanswered / answered),
- даём удобные функции для main.py и keyboards.py.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple, Optional

from botapp.ozon_client import (
    Question,
    get_client,
    get_question_answers,
    get_questions_list,
)
from botapp.text_utils import safe_strip, safe_str

logger = logging.getLogger(__name__)

# МСК: Ozon все даты отдаёт в UTC, но интерфейс — под МСК
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)

# Сколько вопросов на странице списка
QUESTIONS_PAGE_SIZE = 10

# Время жизни кеша списка вопросов
SESSION_TTL = timedelta(minutes=2)


# ---------------------------------------------------------------------------
# Модель сессии вопросов на одного Telegram-пользователя
# ---------------------------------------------------------------------------


@dataclass
class QuestionsSession:
    """Кеш состояния по вопросам для одного пользователя Telegram."""

    # Полный список вопросов, как пришёл от API
    all: List[Question] = field(default_factory=list)
    # Быстрые предфильтры
    unanswered: List[Question] = field(default_factory=list)
    answered: List[Question] = field(default_factory=list)

    pretty_period: str = ""

    # Текущие страницы по категориям (для возможной навигации)
    page: Dict[str, int] = field(
        default_factory=lambda: {"all": 0, "unanswered": 0, "answered": 0}
    )

    # Время загрузки кеша
    loaded_at: datetime = field(default_factory=datetime.utcnow)

    # Токены -> (category, index) для компактных callback_data
    tokens: Dict[str, Tuple[str, int]] = field(default_factory=dict)

    # Кеш текстов ответов по question.id, живёт вместе с сессией
    answer_cache: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)


# user_id -> QuestionsSession
_sessions: Dict[int, QuestionsSession] = {}


# ---------------------------------------------------------------------------
# Вспомогательные функции для дат и человекочитаемых меток
# ---------------------------------------------------------------------------


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Аккуратно парсим ISO-дату из API Ozon, возвращаем UTC-datetime."""
    if not value:
        return None
    try:
        # Ozon часто отдаёт "2025-11-27T09:07:33.288Z"
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _to_msk(dt: Optional[datetime]) -> Optional[datetime]:
    """Переводим datetime в МСК."""
    if not dt:
        return None
    base = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return base.astimezone(MSK_TZ)


def _fmt_dt_msk(dt: Optional[datetime]) -> str:
    """Форматируем дату в строку МСК вида 27.11.2025 12:34."""
    if not dt:
        return ""
    msk = _to_msk(dt)
    return msk.strftime("%d.%m.%Y %H:%M") if msk else ""


def _human_age(dt: Optional[datetime]) -> str:
    """Человекочитаемый возраст даты: "сегодня", "вчера", "N дн. назад"."""
    if not dt:
        return ""
    msk = _to_msk(dt)
    if not msk:
        return ""
    today = datetime.now(MSK_TZ).date()
    delta_days = (today - msk.date()).days
    if delta_days < 0:
        return "из будущего"
    if delta_days == 0:
        return "сегодня"
    if delta_days == 1:
        return "вчера"
    return f"{delta_days} дн. назад"


# ---------------------------------------------------------------------------
# Фильтрация и кеширование списков вопросов
# ---------------------------------------------------------------------------


_CYRILLIC_RE = re.compile("[А-Яа-яЁё]")


def _filter_by_category(items: List[Question], category: str) -> List[Question]:
    """Фильтруем вопросы по UI-категории.

    category:
      - "all"         — без фильтра
      - "unanswered"  — без ответа / не обработанные
      - "answered"    — есть ответ / обработанные
    """
    cat = (category or "all").lower()

    if cat == "unanswered":
        # Ориентируемся на отсутствие текста ответа и статус != PROCESSED
        return [
            q
            for q in items
            if not getattr(q, "has_answer", False)
            and (getattr(q, "status", "") or "").upper() != "PROCESSED"
        ]

    if cat == "answered":
        return [
            q
            for q in items
            if getattr(q, "has_answer", False)
            or (getattr(q, "status", "") or "").upper() == "PROCESSED"
            or safe_strip(getattr(q, "answer_text", None))
        ]

    # "all" — без фильтра
    return items


async def _prefetch_question_product_names(questions: List[Question]) -> None:
    """Попробовать дополнить названия товаров для вопросов по product_id/sku."""

    try:
        client = get_client()
    except Exception as exc:  # pragma: no cover - защита на случай отсутствия ключей
        logger.warning("Cannot init Ozon client for product names: %s", exc)
        return

    if not questions:
        return

    missing_ids: list[str] = []
    for q in questions:
        existing_name = safe_strip(getattr(q, "product_name", None))
        has_cyrillic = bool(_CYRILLIC_RE.search(existing_name))
        if existing_name and has_cyrillic:
            continue
        pid = getattr(q, "product_id", None) or getattr(q, "sku", None)
        pid_str = safe_strip(pid) if pid not in (None, "") else ""
        if pid_str:
            missing_ids.append(pid_str)

    seen: set[str] = set()
    unique_ids = []
    for pid in missing_ids:
        if pid not in seen:
            seen.add(pid)
            unique_ids.append(pid)

    title_map: dict[str, str] = {}
    covered_ids: set[str] = set()
    if unique_ids:
        date_to = datetime.utcnow().date()
        date_from = date_to - timedelta(days=60)
        try:
            status, fetched_map, _ = await client.get_sku_title_map(
                date_from.isoformat(), date_to.isoformat(), limit=1000, offset=0
            )
            if status == 200:
                title_map = {str(k): v for k, v in fetched_map.items() if v}
        except Exception as exc:
            logger.warning("Failed to prefetch SKU titles for questions: %s", exc)

    if title_map:
        for q in questions:
            pid_val = getattr(q, "product_id", None) or getattr(q, "sku", None)
            pid_str = safe_strip(pid_val)
            if not pid_str:
                continue
            if getattr(q, "product_name", None) and _CYRILLIC_RE.search(
                str(q.product_name)
            ):
                covered_ids.add(pid_str)
                continue
            mapped_name = safe_strip(title_map.get(pid_str))
            if not mapped_name:
                continue
            q.product_name = mapped_name
            if _CYRILLIC_RE.search(mapped_name):
                covered_ids.add(pid_str)

    for pid in unique_ids:
        if pid in covered_ids:
            continue
        try:
            name = await client.get_product_name(pid)
        except Exception as exc:
            logger.warning("Failed to fetch product name for %s: %s", pid, exc)
            continue

        if not name:
            continue

        for q in questions:
            pid_val = getattr(q, "product_id", None) or getattr(q, "sku", None)
            if safe_strip(pid_val) != pid:
                continue
            existing_name = safe_strip(getattr(q, "product_name", None))
            if not existing_name or not _CYRILLIC_RE.search(existing_name):
                q.product_name = name


async def refresh_questions(user_id: int, category: str) -> List[Question]:
    """Запрашиваем список вопросов с Ozon и обновляем кеш для пользователя.

    Возвращаем список уже отфильтрованный по категории.
    """
    # Загружаем все вопросы один раз и фильтруем локально
    questions = await get_questions_list(
        status=None,
        limit=200,
        offset=0,
    )

    await _prefetch_question_product_names(questions)

    session = _sessions.setdefault(user_id, QuestionsSession())
    session.all = questions
    session.unanswered = _filter_by_category(questions, "unanswered")
    session.answered = _filter_by_category(questions, "answered")
    session.loaded_at = datetime.utcnow()
    session.tokens.clear()

    dates_msk = []
    for q in questions:
        created = _parse_date(getattr(q, "created_at", None))
        msk = _to_msk(created)
        if msk:
            dates_msk.append(msk)

    if dates_msk:
        start = min(dates_msk)
        end = max(dates_msk)
        session.pretty_period = f"{start:%d.%m.%Y} 00:00 — {end:%d.%m.%Y %H:%M} (МСК)"
    else:
        session.pretty_period = "период не определён"

    return _filter_by_category(questions, category)


def _get_session(user_id: int) -> QuestionsSession:
    """Берём (или создаём) сессию по user_id."""
    return _sessions.setdefault(user_id, QuestionsSession())


def _get_cached_questions(user_id: int, category: str) -> List[Question]:
    """Возвращаем кешированный список вопросов, если TTL не истёк."""
    session = _get_session(user_id)
    if datetime.utcnow() - session.loaded_at > SESSION_TTL:
        return []

    cat = (category or "all").lower()
    if cat == "unanswered":
        return session.unanswered
    if cat == "answered":
        return session.answered
    return session.all


async def ensure_question_answer_text(question: Question, user_id: Optional[int] = None) -> None:
    """Догружает текст ответа для вопроса, если он отмечен как отвеченный."""

    if not getattr(question, "has_answer", False):
        return

    if safe_strip(getattr(question, "answer_text", None)):
        return

    session = _sessions.get(user_id) if user_id is not None else None
    cached = None
    if session:
        cached = session.answer_cache.get(question.id)
    if cached:
        question.answer_text = cached.get("text") or question.answer_text
        question.answer_id = cached.get("answer_id") or question.answer_id
        question.has_answer = bool(question.answer_text)
        question.answers_count = question.answers_count or cached.get("answers_count")
        return

    try:
        answers = await get_question_answers(question.id, limit=1)
    except Exception as exc:  # pragma: no cover - сеть/формат
        logger.warning("Failed to fetch answer text for %s: %s", question.id, exc)
        return

    if not answers:
        return

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


def get_question_by_index(user_id: int, category: str, index: int) -> Optional[Question]:
    """Вернуть вопрос по абсолютному индексу в кешированном списке категории."""
    questions = _get_cached_questions(user_id, category)
    if not questions:
        return None
    if 0 <= index < len(questions):
        return questions[index]
    return None


def get_question_index(user_id: int, category: str, question_id: str) -> Optional[int]:
    """Найти абсолютный индекс вопроса по его Ozon ID в кешированном списке."""
    questions = _get_cached_questions(user_id, category)
    for idx, q in enumerate(questions):
        if str(getattr(q, "id", "")) == str(question_id):
            return idx
    return None


def find_question(user_id: int, question_id: str) -> Optional[Question]:
    """Поиск вопроса по его Ozon ID во всех кешированных списках сессии."""
    session = _get_session(user_id)
    for pool in (session.all, session.unanswered, session.answered):
        for q in pool:
            if str(getattr(q, "id", "")) == str(question_id):
                return q
    return None


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

    lines: List[str] = [
        f"❓ • {header_date} • {period_title}",
        f"Позиция: {product_name}{sku_part}",
        f"ID вопроса: {getattr(question, 'id', '—')}",
        "",
        "Текст вопроса:",
        getattr(question, "question_text", None)
        or getattr(question, "text", None)
        or getattr(question, "message", None)
        or "—",
        "",
        f"Статус: {status_icon} {status_text}",
        "",
        "Ответ продавца:",
    ]

    answer_text = safe_strip(answer_override) or safe_strip(getattr(question, "answer_text", None))

    if not answer_text:
        answer_text = "Ответа продавца пока нет."

    lines.append(answer_text)

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


def register_question_token(user_id: int, category: str, index: int) -> str:
    """Регистрируем короткий токен для ссылок на вопрос из callback_data.

    Сохраняем в сессии отображение token -> (category, index).
    """
    session = _get_session(user_id)
    token = uuid.uuid4().hex[:8]
    session.tokens[token] = (category, index)
    return token


def resolve_question_token(user_id: int, token: str) -> Optional[Question]:
    """Восстанавливаем объект Question по токену, если он ещё в кеше."""
    session = _get_session(user_id)
    category_index = session.tokens.get(token)
    if not category_index:
        return None
    category, index = category_index
    return get_question_by_index(user_id, category, index)


__all__ = [
    "refresh_questions",
    "get_questions_table",
    "get_question_by_index",
    "get_question_index",
    "get_questions_pretty_period",
    "find_question",
    "resolve_question_id",
    "format_question_card_text",
    "ensure_question_answer_text",
    "register_question_token",
    "resolve_question_token",
]

