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

    title = get_questions_pretty_period(user_id)
    header = f"<b>{title}</b>\n"
    header += f"Категория: <b>{_escape(category)}</b>\n"
    header += f"Всего: <b>{total}</b> | Страница: <b>{safe_page + 1}/{total_pages}</b>\n\n"
    header += "Выберите вопрос из списка ниже:"

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

    parts: list[str] = []
    parts.append(f"<b>{period_title}</b>")
    parts.append(f"{status}")
    parts.append(f"🆔 <code>{_escape(q.id)}</code>")
    if created:
        parts.append(f"🕒 {_escape(created)}")
    if sku:
        parts.append(f"SKU: <code>{_escape(sku)}</code>")
    parts.append(f"🧾 Товар: {prod}")

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
    "find_question",
    "get_question_by_index",
    "resolve_question_token",
    "resolve_question_id",
]
