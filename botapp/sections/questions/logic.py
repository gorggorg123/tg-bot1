# botapp/sections/questions/logic.py
"""Helpers for loading and formatting customer questions from Ozon.

Логика максимально похожа на модуль с отзывами:
- кешируем вопросы по user_id,
- поддерживаем категории (all / unanswered / answered),
- даём удобные функции для main.py и keyboards.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from botapp.api.ozon_client import (
    Question,
    _product_name_cache,
    get_client,
    get_question_answers,
    get_questions_list,
)
from botapp.sections._base import is_cache_fresh
from botapp.ui import TokenStore, build_list_header, slice_page
from botapp.utils.text_utils import safe_strip, safe_str

logger = logging.getLogger(__name__)

# МСК: Ozon все даты отдаёт в UTC, но интерфейс — под МСК
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)

# Сколько вопросов на странице списка
QUESTIONS_PAGE_SIZE = 10
INITIAL_QUESTIONS_TARGET = QUESTIONS_PAGE_SIZE * 3

# Время жизни кеша списка вопросов
CACHE_TTL_SECONDS = 120
SESSION_TTL = timedelta(seconds=CACHE_TTL_SECONDS)
# Время жизни токенов для callback-данных карточек
TOKEN_TTL_SECONDS = 3600

# Кеш ответа по вопросу (question_id -> answer payload)
ANSWER_CACHE_TTL = timedelta(minutes=30)


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

    # Кеш текстов ответов по question.id, живёт вместе с сессией
    answer_cache: Dict[str, Dict[str, Optional[str]]] = field(default_factory=dict)


# user_id -> QuestionsSession
_sessions: Dict[int, QuestionsSession] = {}
_question_tokens = TokenStore(ttl_seconds=TOKEN_TTL_SECONDS)
_answer_text_cache: Dict[str, tuple[Dict[str, Optional[str]], datetime]] = {}


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
    """Дополняем названия товаров для вопросов.

    Основной способ — batch через /v3/product/info/list (sku/product_id), чтобы не плодить
    десятки запросов и не зависеть от аналитики.
    """

    try:
        client = get_client()
    except Exception as exc:  # pragma: no cover
        logger.warning("Cannot init Ozon client for product names: %s", exc)
        return

    if not questions:
        return

    # Вопросы с уже нормальным названием или кэшированным значением пропускаем
    need: list[Question] = []
    for q in questions:
        existing = safe_strip(getattr(q, "product_name", None))
        if existing and _CYRILLIC_RE.search(existing):
            continue

        key = safe_strip(getattr(q, "offer_id", None)) or safe_strip(getattr(q, "product_id", None))
        if key:
            if key in _product_name_cache:
                cached = _product_name_cache.get(key)
                if cached:
                    q.product_name = cached
                continue

        need.append(q)

    if not need:
        return

    skus: list[int] = []
    pids: list[str] = []
    for q in need:
        sku_val = getattr(q, "sku", None)
        if isinstance(sku_val, int):
            skus.append(sku_val)
        pid = safe_strip(getattr(q, "product_id", None))
        if pid:
            pids.append(pid)

    def _uniq(seq):
        seen=set()
        out=[]
        for x in seq:
            if x in (None, ""):
                continue
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    skus = _uniq(skus)
    pids = _uniq(pids)

    title_by_pid, _title_by_offer_unused, title_by_sku = await client.get_product_titles_cached(
        product_ids=pids, skus=skus
    )

    for q in need:
        existing = safe_strip(getattr(q, "product_name", None))
        if existing and _CYRILLIC_RE.search(existing):
            continue
        sku_val = getattr(q, "sku", None)
        if isinstance(sku_val, int) and sku_val in title_by_sku:
            q.product_name = title_by_sku[sku_val]
            _product_name_cache[str(sku_val)] = q.product_name
            continue
        pid = safe_strip(getattr(q, "product_id", None))
        if pid and pid in title_by_pid:
            q.product_name = title_by_pid[pid]
            _product_name_cache[pid] = q.product_name
            continue

    # Финальный точечный fallback (редко)
    for q in need:
        existing = safe_strip(getattr(q, "product_name", None))
        if existing and _CYRILLIC_RE.search(existing):
            continue
        pid = safe_strip(getattr(q, "product_id", None))
        if not pid:
            continue
        try:
            name = await client.get_product_name(pid)
        except Exception:
            name = None
        _product_name_cache[pid] = name
        if name:
            q.product_name = name


async def refresh_questions(user_id: int, category: str = "all", *, force: bool = False) -> List[Question]:
    """Запрашиваем список вопросов с Ozon и обновляем кеш для пользователя.

    Возвращаем список уже отфильтрованный по категории.
    """

    if not force:
        cached = _get_cached_questions(user_id, category)
        if cached:
            return cached

    questions: list[Question] = []
    offset = 0
    limit = 200

    while True:
        batch = await get_questions_list(
            status=None,
            limit=limit,
            offset=offset,
        )

        if not isinstance(batch, list):
            break

        questions.extend(batch)

        if len(batch) < limit:
            break

        if len(questions) >= INITIAL_QUESTIONS_TARGET:
            break

        offset += limit

    await _prefetch_question_product_names(questions)

    session = _sessions.setdefault(user_id, QuestionsSession())
    session.all = questions
    session.unanswered = _filter_by_category(questions, "unanswered")
    session.answered = _filter_by_category(questions, "answered")
    session.loaded_at = datetime.utcnow()

    for q in questions:
        existing_answer = safe_strip(getattr(q, "answer_text", None))
        if existing_answer:
            payload = {
                "text": q.answer_text,
                "answer_id": getattr(q, "answer_id", None),
                "answer_created_at": getattr(q, "answer_created_at", None),
                "answers_count": getattr(q, "answers_count", None),
            }
            _set_answer_cache(q.id, payload)
            session.answer_cache[q.id] = payload

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
    if not is_cache_fresh(session.loaded_at, CACHE_TTL_SECONDS):
        return []

    cat = (category or "all").lower()
    if cat == "unanswered":
        return session.unanswered
    if cat == "answered":
        return session.answered
    return session.all


def _get_answer_cache(question_id: str) -> Optional[Dict[str, Optional[str]]]:
    payload = _answer_text_cache.get(question_id)
    if not payload:
        return None

    data, created_at = payload
    if datetime.utcnow() - created_at > ANSWER_CACHE_TTL:
        _answer_text_cache.pop(question_id, None)
        return None
    return data


def _set_answer_cache(question_id: str, data: Dict[str, Optional[str]]) -> None:
    _answer_text_cache[question_id] = (data, datetime.utcnow())


def invalidate_answer_cache(question_id: str, *, user_id: Optional[int] = None) -> None:
    _answer_text_cache.pop(question_id, None)
    if user_id is not None:
        session = _sessions.get(user_id)
        if session:
            session.answer_cache.pop(question_id, None)


async def ensure_question_answer_text(question: Question, user_id: Optional[int] = None, force_reload: bool = False) -> None:
    """Догружает текст ответа ПРОДАВЦА для вопроса.
    
    ВАЖНО: API /v1/question/list возвращает answer_text без указания автора,
    поэтому мы НЕ доверяем этому полю и всегда загружаем ответ через
    /v1/question/answer/list с фильтрацией seller_only=True.
    
    Args:
        question: Объект вопроса для обновления
        user_id: ID пользователя для сессии
        force_reload: Если True, игнорирует кэш и загружает заново
    """
    session = _sessions.get(user_id) if user_id is not None else None
    
    # Проверяем кэш (только если не force_reload)
    if not force_reload:
        cached = session.answer_cache.get(question.id) if session else None
        if not cached:
            cached = _get_answer_cache(question.id)
        
        if cached and cached.get("verified_seller"):
            # Кэш с проверенным ответом продавца
            question.answer_text = cached.get("text")
            question.answer_id = cached.get("answer_id")
            question.has_answer = bool(question.answer_text)
            question.answers_count = cached.get("answers_count") or question.answers_count
            question.answer_created_at = cached.get("answer_created_at") or getattr(question, "answer_created_at", None)
            if session:
                session.answer_cache[question.id] = cached
            return

    # Определяем SKU для запроса
    sku = None
    try:
        sku = int(question.sku) if getattr(question, "sku", None) is not None else None
    except Exception:
        sku = None

    if sku is None:
        pid = safe_strip(getattr(question, "product_id", None))
        if pid and pid.isdigit():
            try:
                sku = int(pid)
            except Exception:
                sku = None

    # Загружаем ответы ТОЛЬКО от продавца через /v1/question/answer/list
    try:
        # seller_only=True фильтрует только ответы продавца
        answers = await get_question_answers(question.id, limit=5, sku=sku)
    except Exception as exc:  # pragma: no cover - сеть/формат
        logger.warning("Failed to fetch seller answer for question %s: %s", question.id, exc)
        # При ошибке НЕ используем answer_text из основного списка (он может быть от покупателя)
        return

    if not answers:
        # Нет ответов от продавца — очищаем answer_text (мог быть комментарий покупателя)
        logger.debug("No seller answer found for question %s, clearing answer_text", question.id)
        question.answer_text = None
        question.answer_id = None
        question.has_answer = False
        
        # Кэшируем отсутствие ответа
        payload = {
            "text": None,
            "answer_id": None,
            "answer_created_at": None,
            "answers_count": 0,
            "verified_seller": True,  # Пометка что проверили
        }
        _set_answer_cache(question.id, payload)
        if session:
            session.answer_cache[question.id] = payload
        return

    # Берём первый ответ продавца (самый свежий)
    first = answers[0]
    question.answer_text = first.text
    question.answer_id = first.id
    question.has_answer = bool(question.answer_text)
    question.answers_count = len(answers)
    question.answer_created_at = first.created_at or getattr(question, "answer_created_at", None)

    logger.debug(
        "Seller answer loaded for question %s: text=%s..., is_seller=%s",
        question.id, (first.text or "")[:50], first.is_seller
    )

    payload = {
        "text": question.answer_text,
        "answer_id": question.answer_id,
        "answer_created_at": question.answer_created_at,
        "answers_count": question.answers_count,
        "verified_seller": True,  # Пометка что это проверенный ответ продавца
    }
    _set_answer_cache(question.id, payload)

    if session:
        session.answer_cache[question.id] = payload


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
    """Карточка вопроса: показывает товар, вопрос, опубликованный ответ и/или черновик."""

    created = _parse_date(getattr(question, "created_at", None))
    created_part = _fmt_dt_msk(created) or "дата неизвестна"
    age = _human_age(created)
    age_part = f" ({age})" if age else ""

    product = _pick_product_label_question(question)

    qid = safe_strip(getattr(question, "id", None))
    question_text = safe_strip(getattr(question, "question_text", None)) or "—"

    published_answer = safe_strip(getattr(question, "answer_text", None))
    answer_dt_raw = safe_strip(getattr(question, "answer_created_at", None))
    answer_dt = _fmt_dt_msk(_parse_date(answer_dt_raw)) if answer_dt_raw else None
    has_answer = bool(getattr(question, "has_answer", False)) or bool(published_answer)
    badge, status_label = _status_badge_question(question)
    raw_status = safe_strip(getattr(question, "status", None))

    lines: list[str] = []
    
    # ═══ Заголовок ═══
    lines.append(f"❓ <b>Вопрос</b> • {badge}")
    lines.append(f"📅 {created_part}{age_part}")
    lines.append(f"🛒 Товар: {product}")
    if qid:
        lines.append(f"<code>ID: {qid}</code>")

    status_parts = [status_label]
    if raw_status and raw_status.upper() != status_label.upper():
        status_parts.append(f"({raw_status})")
    lines.append(f"📌 Статус: {' '.join(status_parts)}")

    # ═══ Текст вопроса ═══
    lines.append("")
    lines.append("<b>Текст вопроса:</b>")
    lines.append(question_text)

    # ═══ Опубликованный ответ на Ozon ═══
    if published_answer:
        dt_part = f" • {answer_dt}" if answer_dt else ""
        count_part = ""
        if answers_count and answers_count > 1:
            count_part = f" ({answers_count} сообщ.)"
        lines.append("")
        lines.append(f"✅ <b>Ответ продавца на Ozon</b>{dt_part}{count_part}:")
        lines.append(published_answer)

    # ═══ Черновик ═══
    draft = safe_strip(answer_override)
    if draft:
        lines.append("")
        lines.append("📝 <b>Черновик ответа</b> (не отправлен):")
        lines.append(draft)

    # ═══ Период ═══
    if period_title:
        lines.append("")
        lines.append(f"📅 Период: {period_title}")

    return "\n".join(lines)


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
    product_id = safe_strip(getattr(q, "product_id", None))

    if product_name:
        return product_name[:47] + ("…" if len(product_name) > 50 else "")
    if sku:
        return f"Артикул: {sku}"
    if product_id:
        return f"ID: {product_id}"
    return "—"


def _pick_product_label_question(q: Question) -> str:
    product_name = safe_strip(getattr(q, "product_name", None))
    sku = safe_strip(getattr(q, "sku", None))
    product_id = safe_strip(getattr(q, "product_id", None))

    if product_name and product_id:
        return f"{product_name} (ID: {product_id})"
    if product_name and sku:
        return f"{product_name} (Артикул: {sku})"
    if product_name:
        return product_name
    if sku:
        return f"Артикул: {sku}"
    if product_id:
        return f"ID: {product_id}"
    return "—"


def build_questions_table(
    *,
    cards: List[Question],
    pretty_period: str,
    category: str,
    page: int = 0,
    page_size: int = QUESTIONS_PAGE_SIZE,
) -> tuple[str, List[tuple[str, str, int]], int, int]:
    """Собрать шапку списка вопросов и кнопки выбора."""

    slice_items, safe_page, total_pages = slice_page(cards, page, page_size)
    category_label = {
        "unanswered": "Без ответа",
        "answered": "С ответом",
        "all": "Все",
    }.get(category, category)

    header = build_list_header(
        f"🗂 Вопросы: {category_label}", pretty_period, safe_page, total_pages
    )

    items: list[tuple[str, str, int]] = []

    for i, q in enumerate(slice_items, start=1 + safe_page * page_size):
        created_at = _parse_date(getattr(q, "created_at", None))
        date_part = _fmt_dt_msk(created_at) or "—"
        product = _pick_short_product_label_question(q)

        a_text = safe_str(getattr(q, "answer_text", None))

        has_answer = bool(getattr(q, "has_answer", False)) or bool(safe_strip(a_text))
        badge = "✅" if has_answer else "🆕"

        qid = safe_strip(getattr(q, "id", None))
        if qid:
            # Фиксированная длина для выравнивания всех кнопок
            # Telegram обрезает trailing spaces, поэтому используем NO-BREAK SPACE (\u00A0)
            # Формат: "01) ✅ 23.01.2026 21:03  Название товара..."
            idx = f"{i:02d})"
            
            # Фиксированная длина для названия товара
            TARGET_PROD_LEN = 28
            
            # Обрезаем или дополняем название товара до фиксированной длины
            if len(product) > TARGET_PROD_LEN:
                prod_fixed = product[:TARGET_PROD_LEN - 1] + "…"
            else:
                # Дополняем NO-BREAK SPACE до фиксированной длины
                prod_fixed = product + "\u00A0" * (TARGET_PROD_LEN - len(product))
            
            # Формируем label
            label = f"{idx} {badge} {date_part}  {prod_fixed}"
            
            items.append((label, qid, i - 1))

    text = header or " "

    return text, items, safe_page, total_pages


def get_questions_pretty_period(user_id: int) -> str:
    session = _get_session(user_id)
    return session.pretty_period or ""


# ---------------------------------------------------------------------------
# Токены для компактных callback_data
# ---------------------------------------------------------------------------


def register_question_token(user_id: int, category: str, index: int) -> str:
    """Регистрируем короткий токен для ссылок на вопрос из callback_data."""

    question = get_question_by_index(user_id, category, index)
    if not question:
        return _question_tokens.generate(user_id, ("miss", category, index))

    qid = str(getattr(question, "id", ""))
    return _question_tokens.generate(user_id, qid, key=qid)


async def resolve_question_token(user_id: int, token: str) -> Optional[Question]:
    """Восстанавливаем объект Question по токену, если он ещё в кеше."""

    qid = _question_tokens.resolve(user_id, token)
    if not qid:
        return None

    session = _get_session(user_id)
    cache_stale = not is_cache_fresh(session.loaded_at, CACHE_TTL_SECONDS)

    q = find_question(user_id, qid)
    if q and not cache_stale:
        return q

    try:
        await refresh_questions(user_id, "all", force=True)
    except Exception:
        logger.debug("Failed to refresh questions on token resolve user_id=%s", user_id)
        return None

    return find_question(user_id, qid)


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
    "invalidate_answer_cache",
    "register_question_token",
    "resolve_question_token",
]
