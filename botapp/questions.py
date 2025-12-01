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

    # Текущие страницы по категориям (для возможной навигации)
    page: Dict[str, int] = field(
        default_factory=lambda: {"all": 0, "unanswered": 0, "answered": 0}
    )

    # Время загрузки кеша
    loaded_at: datetime = field(default_factory=datetime.utcnow)

    # Токены -> (category, index) для компактных callback_data
    tokens: Dict[str, Tuple[str, int]] = field(default_factory=dict)


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
            or (getattr(q, "answer_text", None) or "").strip()
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
        existing_name = (getattr(q, "product_name", None) or "").strip()
        has_cyrillic = bool(_CYRILLIC_RE.search(existing_name))
        if existing_name and has_cyrillic:
            continue
        pid = getattr(q, "product_id", None) or getattr(q, "sku", None)
        pid_str = str(pid).strip() if pid not in (None, "") else ""
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
            pid_str = str(pid_val).strip()
            if not pid_str:
                continue
            if getattr(q, "product_name", None) and _CYRILLIC_RE.search(
                str(q.product_name)
            ):
                covered_ids.add(pid_str)
                continue
            mapped_name = (title_map.get(pid_str) or "").strip()
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
            if str(pid_val).strip() != pid:
                continue
            existing_name = (getattr(q, "product_name", None) or "").strip()
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


async def ensure_question_answer_text(question: Question) -> None:
    """Догружает текст ответа для вопроса, если он отмечен как отвеченный."""

    if not getattr(question, "has_answer", False):
        return

    if (getattr(question, "answer_text", None) or "").strip():
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


# ---------------------------------------------------------------------------
# Пагинация и таблица списка вопросов
# ---------------------------------------------------------------------------


async def get_questions_table(
    *,
    user_id: int,
    category: str,
    page: int = 0,
) -> tuple[str, List[tuple[str, str, int]], int, int]:
    """Вернуть (text, items, current_page, total_pages) для списка вопросов.

    text  — многострочный текст для сообщения.
    items — список (label, question_id, absolute_index) для клавиатуры.
    """
    questions = _get_cached_questions(user_id, category)
    if not questions:
        questions = await refresh_questions(user_id, category)

    total = len(questions)
    total_pages = max((total - 1) // QUESTIONS_PAGE_SIZE + 1, 1)

    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * QUESTIONS_PAGE_SIZE
    end = start + QUESTIONS_PAGE_SIZE
    page_items = questions[start:end]

    lines: List[str] = ["❓ Вопросы покупателей"]

    pretty_category = {
        "all": "Все",
        "unanswered": "Без ответа",
        "answered": "С ответом",
    }.get((category or "all").lower(), category)

    lines.append(f"Категория: {pretty_category}")
    lines.append("✅ — есть ответ, ❗ — нет ответа")

    if not page_items:
        lines.append("")
        lines.append("Нет вопросов в этой категории.")
    else:
        lines.append("")
        for idx, q in enumerate(page_items, start=start + 1):
            created = _parse_date(getattr(q, "created_at", None))
            created_text = _fmt_dt_msk(created) or "—"
            age_text = _human_age(created)
            status_icon = "✅" if getattr(q, "has_answer", False) else "❗"
            product_name = (getattr(q, "product_name", None) or "").strip() or "—"

            lines.append(
                f"{idx}. {status_icon} "
                f"{created_text} ({age_text or '—'}) | "
                f"Товар: {product_name[:70]}"
            )

    # Для клавиатуры: label, question_id, абсолютный индекс
    items: List[tuple[str, str, int]] = []

    for rel_idx, q in enumerate(page_items):
        created = _parse_date(getattr(q, "created_at", None))
        created_text = _fmt_dt_msk(created) or "—"
        age_text = _human_age(created)
        status_icon = "✅" if getattr(q, "has_answer", False) else "❗"
        product_name = (getattr(q, "product_name", None) or "").strip() or "—"

        label = (
            f"{status_icon} {created_text} "
            f"({age_text or '—'}) | Товар: {product_name[:40]}"
        )

        question_id = getattr(q, "id", None)
        if not question_id:
            # На всякий случай, если вдруг нет id
            continue

        items.append((label, str(question_id), start + rel_idx))

    return "\n".join(lines), items, safe_page, total_pages


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
) -> str:
    """Собираем человекочитаемую карточку вопроса для Telegram."""

    created = _parse_date(getattr(question, "created_at", None))
    product_name = getattr(question, "product_name", None) or "—"
    status_raw = (getattr(question, "status", None) or "").upper()
    status_text = (
        "Ответ дан" if getattr(question, "has_answer", False) or status_raw == "PROCESSED" else "Без ответа"
    )
    answers_count = getattr(question, "answers_count", None)

    effective_answers_count = answers_count
    if effective_answers_count is None:
        raw_count = getattr(question, "answers_count", None)
        try:
            effective_answers_count = int(raw_count) if raw_count is not None else None
        except Exception:
            effective_answers_count = None

    lines: List[str] = [
        "❓ Вопрос покупателя",
        f"Товар: {product_name}",
        f"Дата: {_fmt_dt_msk(created)} (МСК)",
        f"Статус: {status_text}",
        f"Статус Ozon: {status_raw or '—'}",
        f"Ответов: {answers_count if answers_count is not None else '—'}",
        "",
        "Вопрос:",
        getattr(question, "question_text", None) or
        getattr(question, "text", None) or
        getattr(question, "message", None) or
        "—",
    ]

    answer_text = (
        (answer_override or "").strip()
        or (getattr(question, "answer_text", None) or "").strip()
        or "ответ пока не задан"
    )

    lines.extend(["", "Текущий ответ ITOM:", answer_text])

    return "\n".join(lines)


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
    "find_question",
    "resolve_question_id",
    "format_question_card_text",
    "ensure_question_answer_text",
    "register_question_token",
    "resolve_question_token",
]

