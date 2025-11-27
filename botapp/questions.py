"""Helpers for loading and formatting customer questions from Ozon.

This module mirrors the reviews helper design: it caches per-user question lists
in memory, supports pagination, and offers helpers to format question cards.
Functions exported here are intentionally stable because other modules import
them directly (e.g., :mod:`main`, :mod:`botapp.keyboards`).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from botapp.ozon_client import Question, get_questions_list

logger = logging.getLogger(__name__)

MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)
QUESTIONS_PAGE_SIZE = 10
SESSION_TTL = timedelta(minutes=2)


@dataclass
class QuestionsSession:
    """Cached state for a single Telegram user."""

    all: List[Question] = field(default_factory=list)
    unanswered: List[Question] = field(default_factory=list)
    answered: List[Question] = field(default_factory=list)
    page: Dict[str, int] = field(
        default_factory=lambda: {"all": 0, "unanswered": 0, "answered": 0}
    )
    loaded_at: datetime = field(default_factory=datetime.utcnow)
    tokens: Dict[str, Tuple[str, int]] = field(default_factory=dict)


_sessions: Dict[int, QuestionsSession] = {}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _to_msk(dt: datetime | None) -> datetime | None:
    if not dt:
        return None
    base = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return base.astimezone(MSK_TZ)


def _fmt_dt_msk(dt: datetime | None) -> str:
    if not dt:
        return ""
    msk = _to_msk(dt)
    return msk.strftime("%d.%m.%Y %H:%M") if msk else ""


def _human_age(dt: datetime | None) -> str:
    if not dt:
        return ""
    msk = _to_msk(dt)
    if not msk:
        return ""
    today = datetime.now(MSK_TZ).date()
    delta = (today - msk.date()).days
    if delta < 0:
        return "из будущего"
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "вчера"
    return f"{delta} дн. назад"


def _filter_by_category(items: List[Question], category: str) -> List[Question]:
    """Filter questions list by UI category."""

    if category == "unanswered":
        return [q for q in items if (q.status or "").upper() != "PROCESSED" and not q.answer_text]
    if category == "answered":
        return [q for q in items if (q.status or "").upper() == "PROCESSED" or q.answer_text]
    return items


async def refresh_questions(user_id: int, category: str) -> List[Question]:
    """Fetch questions from Ozon and refresh user cache.

    Returns the list filtered by category for convenience.
    """

    questions = await get_questions_list(
        status=None if category == "all" else category,
        limit=200,
        offset=0,
    )

    session = _sessions.setdefault(user_id, QuestionsSession())
    session.all = questions
    session.unanswered = [q for q in questions if not q.answer_text]
    session.answered = [q for q in questions if q.answer_text]
    session.loaded_at = datetime.utcnow()
    session.tokens.clear()
    return _filter_by_category(questions, category)


def _get_session(user_id: int) -> QuestionsSession:
    return _sessions.setdefault(user_id, QuestionsSession())


def _get_cached_questions(user_id: int, category: str) -> List[Question]:
    session = _get_session(user_id)
    if datetime.utcnow() - session.loaded_at > SESSION_TTL:
        return []
    if category == "unanswered":
        return session.unanswered
    if category == "answered":
        return session.answered
    return session.all


async def get_questions_table(
    *, user_id: int, category: str, page: int = 0
) -> tuple[str, list[tuple[str, str, int]], int, int]:
    """Return formatted table text and keyboard items for a questions list page."""

    questions = _get_cached_questions(user_id, category)
    if not questions:
        questions = await refresh_questions(user_id, category)

    total = len(questions)
    total_pages = max((total - 1) // QUESTIONS_PAGE_SIZE + 1, 1)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * QUESTIONS_PAGE_SIZE
    end = start + QUESTIONS_PAGE_SIZE
    page_items = questions[start:end]

    lines = ["❓ Вопросы покупателей"]
    pretty_category = {
        "all": "Все",
        "unanswered": "Без ответа",
        "answered": "С ответом",
    }.get(category, category)
    lines.append(f"Категория: {pretty_category}")

    if not page_items:
        lines.append("Нет вопросов в этой категории.")
    else:
        for idx, q in enumerate(page_items, start=start + 1):
            status_icon = "✅" if q.answer_text else "⏳"
            lines.append(
                f"{status_icon} | {_fmt_dt_msk(_parse_date(q.created_at))} ({_human_age(_parse_date(q.created_at))}) | "
                f"Товар: {(q.product_name or '').strip()[:70] or '—'}"
            )

    items = [
        (
            f"{'✅' if q.answer_text else '⏳'}  | {_fmt_dt_msk(_parse_date(q.created_at))} ({_human_age(_parse_date(q.created_at))}) | "
            f"Товар: {(q.product_name or '').strip()[:40] or '—'}",
            q.id,
            start + idx,
        )
        for idx, q in enumerate(page_items)
    ]
    return "\n".join(lines), items, safe_page, total_pages


def get_question_by_index(user_id: int, category: str, index: int) -> Question | None:
    """Return question by absolute index in cached list for the category."""

    questions = _get_cached_questions(user_id, category)
    if not questions:
        return None
    if 0 <= index < len(questions):
        return questions[index]
    return None


def get_question_index(user_id: int, category: str, question_id: str) -> int | None:
    """Return absolute index of question in cached list if present."""

    questions = _get_cached_questions(user_id, category)
    for idx, q in enumerate(questions):
        if q.id == question_id:
            return idx
    return None


def find_question(user_id: int, question_id: str) -> Question | None:
    """Find question by its Ozon identifier across cached categories."""

    session = _get_session(user_id)
    for pool in (session.all, session.unanswered, session.answered):
        for q in pool:
            if q.id == question_id:
                return q
    return None


def format_question_card_text(question: Question, answer_override: str | None = None) -> str:
    """Build a readable card text for Telegram messages."""

    created = _parse_date(question.created_at)
    status_text = "Ответ дан" if question.answer_text else "Без ответа"
    lines = [
        "❓ Вопрос покупателя",
        f"Товар: {question.product_name or '—'}",
        f"Дата: {_fmt_dt_msk(created)} (МСК)",
        f"Статус: {status_text}",
        "",
        "Вопрос:",
        question.question_text or "—",
    ]
    answer_text = answer_override or question.answer_text or "ответ пока не задан"
    lines.extend(["", "Текущий ответ:", answer_text])
    return "\n".join(lines)


def register_question_token(user_id: int, category: str, index: int) -> str:
    """Register and return a short token for callbacks referencing a question."""

    session = _get_session(user_id)
    token = uuid.uuid4().hex[:8]
    session.tokens[token] = (category, index)
    return token


def resolve_question_token(user_id: int, token: str) -> Question | None:
    """Resolve a token back to a question instance if still cached."""

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
    "format_question_card_text",
    "register_question_token",
    "resolve_question_token",
]
