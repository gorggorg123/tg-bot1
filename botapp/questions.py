import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from .ozon_client import GetQuestionListResponse, OzonClient, get_client

logger = logging.getLogger(__name__)

QUESTIONS_PAGE_SIZE = 10
SESSION_TTL = timedelta(minutes=2)
MSK_SHIFT = timedelta(hours=3)
MSK_TZ = timezone(MSK_SHIFT)

_sessions: dict[int, "QuestionSession"] = {}
_question_id_to_token: dict[int, dict[str, str]] = {}
_token_to_question_id: dict[int, dict[str, str]] = {}


@dataclass
class QuestionCard:
    id: str | None
    text: str
    product_name: str | None
    offer_id: str | None
    product_id: str | None
    created_at: datetime | None
    status: str | None = None
    answer_text: str | None = None
    answered: bool = False


@dataclass
class QuestionSession:
    questions: List[QuestionCard] = field(default_factory=list)
    unanswered: List[QuestionCard] = field(default_factory=list)
    loaded_at: datetime = field(default_factory=datetime.utcnow)

    def rebuild_unanswered(self) -> None:
        self.unanswered = [q for q in self.questions if not q.answered]


def _reset_question_tokens(user_id: int) -> None:
    _question_id_to_token[user_id] = {}
    _token_to_question_id[user_id] = {}


def _base36(num: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if num == 0:
        return "0"
    digits = []
    while num:
        num, rem = divmod(num, 36)
        digits.append(alphabet[rem])
    return "".join(reversed(digits))


def register_question_token(user_id: int, question_id: str | None) -> str | None:
    if not question_id:
        return None
    bucket = _question_id_to_token.setdefault(user_id, {})
    if question_id in bucket:
        return bucket[question_id]

    raw = abs(hash((user_id, question_id)))
    token = _base36(raw)[:8] or "q0"

    used = _token_to_question_id.setdefault(user_id, {})
    suffix = 0
    while token in used and used[token] != question_id:
        suffix += 1
        token_candidate = f"{token[:6]}{_base36(suffix)[:2]}"
        token = token_candidate[:8] or "q0"

    bucket[question_id] = token
    used[token] = question_id
    return token


def resolve_question_id(user_id: int, token: str | None) -> str | None:
    if not token:
        return None
    mapping = _token_to_question_id.get(user_id, {})
    return mapping.get(token, token)


def encode_question_id(user_id: int, question_id: str | None) -> str | None:
    return register_question_token(user_id, question_id)


def _parse_date(value: Any) -> datetime | None:
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
    if txt.isdigit():
        try:
            num = int(txt)
            ts = num / 1000 if num > 10**11 else num
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None

    try:
        dt = datetime.fromisoformat(txt.replace(" ", "T").replace("Z", "+00:00"))
    except Exception:
        return None

    if dt.tzinfo:
        return dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def _to_msk(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo:
        return dt.astimezone(MSK_TZ)
    return dt.replace(tzinfo=MSK_TZ)


def _fmt_dt_msk(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt_msk = _to_msk(dt)
    if not dt_msk:
        return ""
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _normalize_question(raw: Dict[str, Any]) -> QuestionCard:
    text_fields = [raw.get("text"), raw.get("question"), raw.get("message"), raw.get("last_message")]
    text_val = next((str(v).strip() for v in text_fields if v not in (None, "")), "")

    answer_fields = [raw.get("answer"), raw.get("last_answer"), raw.get("answer_text"), raw.get("seller_answer")]
    answer_text = next((str(v).strip() for v in answer_fields if v not in (None, "")), None)

    product_block = raw.get("product") if isinstance(raw.get("product"), dict) else {}
    offer_id = raw.get("offer_id") or raw.get("sku") or product_block.get("offer_id") or product_block.get("sku")
    product_id = raw.get("product_id") or product_block.get("product_id")
    product_name = raw.get("product_name") or raw.get("productTitle") or raw.get("product_title")

    status = (raw.get("status") or raw.get("state") or raw.get("question_status") or "").strip() or None

    created_candidates = [
        raw.get("created_at"),
        raw.get("createdAt"),
        raw.get("date"),
        raw.get("asked_at"),
        raw.get("timestamp"),
    ]
    created_at = next((dt for dt in (_parse_date(v) for v in created_candidates) if dt), None)

    answered = bool(answer_text) or (status and "answer" in status.lower())

    return QuestionCard(
        id=str(raw.get("question_id") or raw.get("id") or raw.get("uuid") or "") or None,
        text=text_val,
        product_name=str(product_name).strip() if product_name not in (None, "") else None,
        offer_id=str(offer_id).strip() if offer_id not in (None, "") else None,
        product_id=str(product_id).strip() if product_id not in (None, "") else None,
        created_at=created_at,
        status=status,
        answer_text=answer_text or None,
        answered=answered,
    )


async def _ensure_session(user_id: int, client: OzonClient | None = None) -> QuestionSession:
    now = datetime.utcnow()
    session = _sessions.get(user_id)
    if session and now - session.loaded_at < SESSION_TTL:
        return session

    return await refresh_questions(user_id, client)


async def refresh_questions(user_id: int, client: OzonClient | None = None) -> QuestionSession:
    client = client or get_client()
    _reset_question_tokens(user_id)

    raw_response: GetQuestionListResponse | dict | None = None
    try:
        raw_response = await client.question_list()
    except Exception as exc:
        logger.warning("Failed to fetch questions: %s", exc)

    items: list[Dict[str, Any]] = []
    if isinstance(raw_response, GetQuestionListResponse):
        items = [q.model_dump() for q in raw_response.items]
    elif isinstance(raw_response, dict):
        payload = raw_response.get("result") if isinstance(raw_response.get("result"), dict) else raw_response
        maybe_items = payload.get("questions") if isinstance(payload, dict) else None
        if isinstance(maybe_items, list):
            items = [q for q in maybe_items if isinstance(q, dict)]

    cards: list[QuestionCard] = []
    for raw in items:
        if not raw:
            continue
        card = _normalize_question(raw)
        if not card.id:
            logger.warning("Skip question without id for user %s: %s", user_id, raw)
            continue
        cards.append(card)

    unanswered = [c for c in cards if not c.answered]

    session = QuestionSession(questions=cards, unanswered=unanswered, loaded_at=datetime.utcnow())
    _sessions[user_id] = session
    return session


def _paginate(cards: List[QuestionCard], page: int) -> tuple[List[QuestionCard], int, int]:
    total_pages = max((len(cards) - 1) // QUESTIONS_PAGE_SIZE + 1, 1)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * QUESTIONS_PAGE_SIZE
    end = start + QUESTIONS_PAGE_SIZE
    return cards[start:end], safe_page, total_pages


def _build_label(card: QuestionCard) -> str:
    parts = []
    if card.product_name:
        parts.append(card.product_name)
    status = card.status or ("ОТВЕЧЕН" if card.answered else "БЕЗ ОТВЕТА")
    parts.append(status)
    return " • ".join(parts) if parts else "Вопрос"


async def get_questions_table(
    *, user_id: int, category: str = "unanswered", page: int = 0, client: OzonClient | None = None
) -> tuple[str, list[tuple[str, str | None, int]], int, int]:
    session = await _ensure_session(user_id, client)
    cards = session.unanswered if category == "unanswered" else session.questions
    page_items, safe_page, total_pages = _paginate(cards, page)

    items: list[tuple[str, str | None, int]] = []
    for idx, card in enumerate(page_items):
        token = encode_question_id(user_id, card.id)
        if not token:
            logger.warning("Skip question without id for user %s", user_id)
            continue
        label = _build_label(card)
        absolute_idx = safe_page * QUESTIONS_PAGE_SIZE + idx
        items.append((label, token, absolute_idx))

    text = "❓ Вопросы\n" "Выберите вопрос для просмотра."
    if not cards:
        text += "\nПока вопросов нет."

    return text, items, safe_page, total_pages


async def get_question_by_index(
    user_id: int, category: str, index: int, client: OzonClient | None = None
) -> tuple[QuestionCard | None, int]:
    session = await _ensure_session(user_id, client)
    cards = session.unanswered if category == "unanswered" else session.questions
    if not cards:
        return None, 0
    safe_index = max(0, min(index, len(cards) - 1))
    return cards[safe_index], safe_index


def format_question_card_text(card: QuestionCard | None) -> str:
    if not card:
        return "Вопрос не найден. Обновите список."

    created = _fmt_dt_msk(card.created_at)
    lines = ["❓ Вопрос", ""]
    if card.product_name:
        lines.append(card.product_name)
    if card.text:
        lines.append(card.text)
    if created:
        lines.append(f"Дата: {created} (МСК)")
    if card.answer_text:
        lines.append("")
        lines.append("Ответ продавца:")
        lines.append(card.answer_text)
    else:
        lines.append("")
        lines.append("Ответа продавца пока нет.")

    return "\n".join(lines)


__all__ = [
    "QuestionCard",
    "encode_question_id",
    "resolve_question_id",
    "register_question_token",
    "get_questions_table",
    "get_question_by_index",
    "format_question_card_text",
    "refresh_questions",
]
