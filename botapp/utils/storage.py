# botapp/storage.py
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

# -----------------------------------------------------------------------------
# Storage location (Render persistent disk friendly)
# -----------------------------------------------------------------------------

def _storage_root() -> Path:
    p = (os.getenv("STORAGE_DIR") or "").strip()
    if p:
        return Path(p)

    for env_name in ("RENDER_DISK_PATH", "PERSIST_DIR", "PERSISTENT_DIR"):
        v = (os.getenv(env_name) or "").strip()
        if v:
            return Path(v)

    return Path("data")


ROOT = _storage_root()
ROOT.mkdir(parents=True, exist_ok=True)

REVIEWS_FILE = ROOT / "review_replies.json"
QUESTIONS_FILE = ROOT / "question_answers.json"
CHATS_FILE = ROOT / "chat_ai_state.json"
ACTIVATED_CHATS_FILE = ROOT / "activated_chats.json"
SETTINGS_FILE = ROOT / "settings.json"

_LOCK = threading.RLock()
_MAX_ACTIVATED_CHATS_PER_USER = 500


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def _write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    txt = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default)
    tmp.write_text(txt, encoding="utf-8")
    tmp.replace(path)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ChatAIState:
    chat_id: str
    user_id: int
    draft_text: str | None = None
    draft_created_at: datetime | None = None
    last_user_prompt: str | None = None
    ui_message_id: int | None = None
    last_opened_at: datetime | None = None


@dataclass
class _Store:
    loaded: bool = False
    reviews: Dict[str, dict] = None
    questions: Dict[str, dict] = None
    chats: Dict[str, dict] = None
    activated_chats: Dict[str, dict] = None
    settings: Dict[str, dict] = None


_STORE = _Store(loaded=False, reviews={}, questions={}, chats={}, activated_chats={}, settings={})


def _ensure_loaded() -> None:
    if _STORE.loaded:
        return
    with _LOCK:
        if _STORE.loaded:
            return

        reviews = _read_json(REVIEWS_FILE, default={})
        questions = _read_json(QUESTIONS_FILE, default={})
        chats = _read_json(CHATS_FILE, default={})
        activated = _read_json(ACTIVATED_CHATS_FILE, default={})
        settings = _read_json(SETTINGS_FILE, default={})

        _STORE.reviews = reviews if isinstance(reviews, dict) else {}
        _STORE.questions = questions if isinstance(questions, dict) else {}
        _STORE.chats = chats if isinstance(chats, dict) else {}
        _STORE.activated_chats = activated if isinstance(activated, dict) else {}
        _STORE.settings = settings if isinstance(settings, dict) else {}
        _STORE.loaded = True


def flush_storage() -> None:
    _ensure_loaded()
    with _LOCK:
        _write_json_atomic(REVIEWS_FILE, _STORE.reviews)
        _write_json_atomic(QUESTIONS_FILE, _STORE.questions)
        _write_json_atomic(CHATS_FILE, _STORE.chats)
        _write_json_atomic(ACTIVATED_CHATS_FILE, _STORE.activated_chats)
        _write_json_atomic(SETTINGS_FILE, _STORE.settings)


def get_review_reply(review_id: str) -> dict | None:
    _ensure_loaded()
    rid = str(review_id).strip()
    if not rid:
        return None
    with _LOCK:
        v = _STORE.reviews.get(rid)
        return dict(v) if isinstance(v, dict) else None


def upsert_review_reply(
    *,
    review_id: str,
    created_at: str | None,
    product_name: str | None,
    rating: int | None,
    review_text: str | None,
    draft: str | None,
    draft_source: str | None,
    sent_to_ozon: bool,
    sent_at: str | None,
    meta: dict | None = None,
) -> None:
    _ensure_loaded()
    rid = str(review_id).strip()
    if not rid:
        return

    payload = {
        "review_id": rid,
        "created_at": created_at,
        "product_name": product_name,
        "rating": rating,
        "review_text": review_text,
        "draft": (draft or ""),
        "draft_source": draft_source or "",
        "sent_to_ozon": bool(sent_to_ozon),
        "sent_at": sent_at,
        "meta": meta or {},
    }

    with _LOCK:
        _STORE.reviews[rid] = payload
        _write_json_atomic(REVIEWS_FILE, _STORE.reviews)


def get_question_answer(question_id: str) -> dict | None:
    _ensure_loaded()
    qid = str(question_id).strip()
    if not qid:
        return None
    with _LOCK:
        v = _STORE.questions.get(qid)
        return dict(v) if isinstance(v, dict) else None


def upsert_question_answer(
    *,
    question_id: str,
    created_at: str | None,
    sku: str | None,
    product_name: str | None,
    question: str | None,
    answer: str | None,
    answer_source: str | None,
    answer_sent_to_ozon: bool,
    answer_sent_at: str | None,
    meta: dict | None = None,
) -> None:
    _ensure_loaded()
    qid = str(question_id).strip()
    if not qid:
        return

    payload = {
        "question_id": qid,
        "created_at": created_at,
        "sku": sku,
        "product_name": product_name,
        "question": question,
        "answer": (answer or ""),
        "answer_source": answer_source or "",
        "answer_sent_to_ozon": bool(answer_sent_to_ozon),
        "answer_sent_at": answer_sent_at,
        "meta": meta or {},
    }

    with _LOCK:
        _STORE.questions[qid] = payload
        _write_json_atomic(QUESTIONS_FILE, _STORE.questions)


def _parse_dt(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return None
    return None


def load_chat_ai_state(user_id: int, chat_id: str) -> ChatAIState | None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return None

    key = f"{int(user_id)}:{cid}"
    fallback_key = cid

    with _LOCK:
        data = _STORE.chats.get(key) or _STORE.chats.get(fallback_key)
        if not isinstance(data, dict):
            return None

    draft = data.get("draft_text") or data.get("ai_draft") or ""
    prompt = data.get("last_user_prompt") or data.get("user_prompt") or ""

    return ChatAIState(
        chat_id=cid,
        user_id=int(user_id),
        draft_text=draft or None,
        draft_created_at=_parse_dt(data.get("draft_created_at")),
        last_user_prompt=prompt or None,
        ui_message_id=data.get("ui_message_id"),
        last_opened_at=_parse_dt(data.get("last_opened_at")),
    )


def save_chat_ai_state(*, user_id: int, chat_id: str, state: ChatAIState) -> None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return

    key = f"{int(user_id)}:{cid}"
    payload = {
        "chat_id": cid,
        "user_id": int(user_id),
        "draft_text": state.draft_text or "",
        "draft_created_at": state.draft_created_at.isoformat() if state.draft_created_at else None,
        "last_user_prompt": state.last_user_prompt or "",
        "ui_message_id": state.ui_message_id,
        "last_opened_at": state.last_opened_at.isoformat() if state.last_opened_at else None,
    }

    with _LOCK:
        _STORE.chats[key] = payload
        _write_json_atomic(CHATS_FILE, _STORE.chats)


def clear_chat_ai_state(user_id: int, chat_id: str) -> None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return
    key = f"{int(user_id)}:{cid}"
    with _LOCK:
        _STORE.chats.pop(key, None)
        _STORE.chats.pop(cid, None)
        _write_json_atomic(CHATS_FILE, _STORE.chats)


def _trim_activated_chats(data: Dict[str, dict]) -> Dict[str, dict]:
    if len(data) <= _MAX_ACTIVATED_CHATS_PER_USER:
        return data

    def _sort_key(item: tuple[str, dict]) -> datetime:
        dt = _parse_dt(item[1].get("activated_at"))
        if dt is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return dt

    sorted_items = sorted(data.items(), key=_sort_key)
    trimmed = {k: v for k, v in sorted_items[-_MAX_ACTIVATED_CHATS_PER_USER:]}
    return trimmed


def get_activated_chat_ids(user_id: int) -> set[str]:
    _ensure_loaded()
    uid = str(int(user_id))
    with _LOCK:
        user_chats = _STORE.activated_chats.get(uid)
        if not isinstance(user_chats, dict):
            return set()
        return set(user_chats.keys())


def mark_chat_activated(user_id: int, chat_id: str) -> None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return

    uid = str(int(user_id))
    with _LOCK:
        user_chats = _STORE.activated_chats.get(uid)
        if not isinstance(user_chats, dict):
            user_chats = {}

        user_chats[cid] = {"activated_at": _utc_now_iso()}
        _STORE.activated_chats[uid] = _trim_activated_chats(user_chats)
        flush_storage()


def _user_settings(uid: str) -> dict:
    settings = _STORE.settings.get(uid)
    if not isinstance(settings, dict):
        settings = {}
    return settings


def get_user_settings(user_id: int) -> dict:
    _ensure_loaded()
    uid = str(int(user_id))
    with _LOCK:
        settings = dict(_user_settings(uid))
        settings.setdefault("outreach_enabled", False)
        settings.setdefault("outreach_interval_seconds", 5)
        return settings


def is_outreach_enabled(user_id: int) -> bool:
    settings = get_user_settings(user_id)
    return bool(settings.get("outreach_enabled"))


def set_outreach_enabled(user_id: int, enabled: bool) -> None:
    _ensure_loaded()
    uid = str(int(user_id))
    with _LOCK:
        settings = _user_settings(uid)
        settings["outreach_enabled"] = bool(enabled)
        settings.setdefault("outreach_interval_seconds", 5)
        _STORE.settings[uid] = settings
        flush_storage()


def get_outreach_interval_seconds(user_id: int) -> int:
    settings = get_user_settings(user_id)
    try:
        seconds = int(settings.get("outreach_interval_seconds", 5))
    except Exception:
        seconds = 5
    return max(1, seconds)


def set_outreach_interval_seconds(user_id: int, seconds: int) -> None:
    _ensure_loaded()
    uid = str(int(user_id))
    with _LOCK:
        settings = _user_settings(uid)
        settings["outreach_interval_seconds"] = max(1, int(seconds)) if seconds is not None else 5
        settings.setdefault("outreach_enabled", False)
        _STORE.settings[uid] = settings
        flush_storage()


__all__ = [
    "get_review_reply",
    "upsert_review_reply",
    "get_question_answer",
    "upsert_question_answer",
    "ChatAIState",
    "load_chat_ai_state",
    "save_chat_ai_state",
    "clear_chat_ai_state",
    "get_activated_chat_ids",
    "mark_chat_activated",
    "get_user_settings",
    "is_outreach_enabled",
    "set_outreach_enabled",
    "get_outreach_interval_seconds",
    "set_outreach_interval_seconds",
    "flush_storage",
]
