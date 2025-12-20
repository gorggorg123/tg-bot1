# botapp/storage.py
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
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

_LOCK = threading.RLock()


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


@dataclass
class _Store:
    loaded: bool = False
    reviews: Dict[str, dict] = None
    questions: Dict[str, dict] = None
    chats: Dict[str, dict] = None


_STORE = _Store(loaded=False, reviews={}, questions={}, chats={})


def _ensure_loaded() -> None:
    if _STORE.loaded:
        return
    with _LOCK:
        if _STORE.loaded:
            return

        reviews = _read_json(REVIEWS_FILE, default={})
        questions = _read_json(QUESTIONS_FILE, default={})
        chats = _read_json(CHATS_FILE, default={})

        _STORE.reviews = reviews if isinstance(reviews, dict) else {}
        _STORE.questions = questions if isinstance(questions, dict) else {}
        _STORE.chats = chats if isinstance(chats, dict) else {}
        _STORE.loaded = True


def flush_storage() -> None:
    _ensure_loaded()
    with _LOCK:
        _write_json_atomic(REVIEWS_FILE, _STORE.reviews)
        _write_json_atomic(QUESTIONS_FILE, _STORE.questions)
        _write_json_atomic(CHATS_FILE, _STORE.chats)


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


def get_chat_ai_state(chat_id: str) -> dict | None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return None
    with _LOCK:
        v = _STORE.chats.get(cid)
        return dict(v) if isinstance(v, dict) else None


def save_chat_ai_state(*, chat_id: str, ai_draft: str, user_prompt: str, meta: dict | None = None) -> None:
    _ensure_loaded()
    cid = str(chat_id).strip()
    if not cid:
        return

    payload = {
        "chat_id": cid,
        "ai_draft": (ai_draft or ""),
        "user_prompt": (user_prompt or ""),
        "meta": meta or {},
    }

    with _LOCK:
        _STORE.chats[cid] = payload
        _write_json_atomic(CHATS_FILE, _STORE.chats)


__all__ = [
    "get_review_reply",
    "upsert_review_reply",
    "get_question_answer",
    "upsert_question_answer",
    "get_chat_ai_state",
    "save_chat_ai_state",
    "flush_storage",
]
