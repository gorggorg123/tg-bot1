from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Dict

from botapp.utils.storage import ROOT, _write_json_atomic

logger = logging.getLogger(__name__)

SECTION_REFS_FILE = ROOT / "section_refs.json"
SECTION_REFS_TTL = timedelta(days=10)


@dataclass(slots=True)
class SectionRef:
    chat_id: int
    message_id: int
    updated_at: datetime


class _StoreState:
    __slots__ = ("loaded", "data", "lock", "dirty", "last_flush_ts", "file")

    def __init__(self, file: Path):
        self.loaded = False
        self.data: Dict[str, Dict[str, dict]] = {}
        self.lock = RLock()
        self.dirty = False
        self.last_flush_ts: float = 0.0
        self.file = file


_STATE = _StoreState(SECTION_REFS_FILE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(dt: datetime | None) -> bool:
    if not dt:
        return True
    return dt < (_now() - SECTION_REFS_TTL)


def _load() -> None:
    if _STATE.loaded:
        return
    with _STATE.lock:
        if _STATE.loaded:
            return
        if _STATE.file.exists():
            try:
                raw = json.loads(_STATE.file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _STATE.data = raw
            except Exception:
                logger.warning("Failed to load section refs store, starting fresh", exc_info=True)
        _STATE.loaded = True
        _prune_locked()


def _prune_locked() -> None:
    cutoff = _now() - SECTION_REFS_TTL
    changed = False
    for user_id, sections in list(_STATE.data.items()):
        if not isinstance(sections, dict):
            _STATE.data.pop(user_id, None)
            changed = True
            continue
        for section, ref in list(sections.items()):
            try:
                updated_at = datetime.fromisoformat(str(ref.get("updated_at")))
            except Exception:
                updated_at = None
            if _is_expired(updated_at):
                sections.pop(section, None)
                changed = True
        if not sections:
            _STATE.data.pop(user_id, None)
            changed = True
    if changed:
        _STATE.dirty = True
        _flush_locked(force=True)


def _flush_locked(force: bool = False) -> None:
    now_ts = time.time()
    if not _STATE.dirty:
        return
    if not force and (now_ts - _STATE.last_flush_ts) < 1.0:
        return
    try:
        _STATE.file.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(_STATE.file, _STATE.data)
    except Exception:
        logger.error("Failed to persist section refs store to %s", _STATE.file, exc_info=True)
        return
    _STATE.last_flush_ts = now_ts
    _STATE.dirty = False


def get_ref(user_id: int, section: str) -> SectionRef | None:
    _load()
    key = str(int(user_id))
    sec = str(section)
    with _STATE.lock:
        sections = _STATE.data.get(key) or {}
        payload = sections.get(sec)
        if not isinstance(payload, dict):
            return None
        try:
            updated_at = datetime.fromisoformat(str(payload.get("updated_at")))
        except Exception:
            updated_at = None
        if _is_expired(updated_at):
            sections.pop(sec, None)
            _STATE.dirty = True
            _flush_locked()
            return None
        try:
            return SectionRef(chat_id=int(payload.get("chat_id")), message_id=int(payload.get("message_id")), updated_at=updated_at or _now())
        except Exception:
            return None


def set_ref(user_id: int, section: str, chat_id: int, message_id: int) -> None:
    _load()
    key = str(int(user_id))
    sec = str(section)
    with _STATE.lock:
        if key not in _STATE.data or not isinstance(_STATE.data.get(key), dict):
            _STATE.data[key] = {}
        _STATE.data[key][sec] = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "updated_at": _now().isoformat(),
        }
        _STATE.dirty = True
        _flush_locked()


def pop_ref(user_id: int, section: str) -> SectionRef | None:
    _load()
    key = str(int(user_id))
    sec = str(section)
    with _STATE.lock:
        sections = _STATE.data.get(key) or {}
        payload = sections.pop(sec, None)
        if payload is None:
            return None
        if not sections:
            _STATE.data.pop(key, None)
        _STATE.dirty = True
        _flush_locked()
        try:
            updated_at = datetime.fromisoformat(str(payload.get("updated_at")))
        except Exception:
            updated_at = None
        return SectionRef(
            chat_id=int(payload.get("chat_id")),
            message_id=int(payload.get("message_id")),
            updated_at=updated_at or _now(),
        )


def mark_stale(user_id: int, section: str) -> None:
    removed = pop_ref(user_id, section)
    if removed:
        logger.info("Marked stale and removed section ref section=%s user_id=%s mid=%s", section, user_id, removed.message_id)


def flush(force: bool = False) -> None:
    with _STATE.lock:
        _flush_locked(force=force)


def _reset_for_tests(file: Path) -> None:
    """Test helper: replace store file and clear state."""
    global _STATE
    _STATE = _StoreState(file)


__all__ = [
    "SectionRef",
    "flush",
    "get_ref",
    "mark_stale",
    "pop_ref",
    "set_ref",
]
