"""Shared SKU/product title cache utilities for bot sections."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from botapp.utils.storage import ROOT, _LOCK as STORAGE_LOCK, _write_json_atomic
from botapp.utils.text_utils import safe_strip

logger = logging.getLogger(__name__)

SKU_TITLE_CACHE_TTL = timedelta(hours=12)
SKU_TITLE_CACHE_PATH = ROOT / "sku_title_cache.json"
SKU_TITLE_CACHE_KEY = "titles"

_sku_title_cache: dict[str, str] = {}
_sku_title_cache_loaded = False
_sku_title_cache_expire_at: datetime | None = None
_sku_cache_lock = STORAGE_LOCK


def _load_sku_title_cache() -> None:
    """Load persistent sku->title cache from disk if not loaded yet."""

    global _sku_title_cache_loaded, _sku_title_cache, _sku_title_cache_expire_at

    with _sku_cache_lock:
        if _sku_title_cache_loaded:
            if _sku_title_cache_expire_at and datetime.utcnow() > _sku_title_cache_expire_at:
                _sku_title_cache = {}
                _sku_title_cache_expire_at = None
            return

        _sku_title_cache_loaded = True

        if not SKU_TITLE_CACHE_PATH.exists():
            return

        try:
            with SKU_TITLE_CACHE_PATH.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Failed to load SKU title cache: %s", exc)
            return

        if not isinstance(payload, dict):
            return

        expires_at_raw = payload.get("expires_at")
        cache_data = payload.get(SKU_TITLE_CACHE_KEY)
        try:
            if expires_at_raw:
                _sku_title_cache_expire_at = datetime.fromisoformat(str(expires_at_raw))
        except Exception:
            _sku_title_cache_expire_at = None

        if _sku_title_cache_expire_at and datetime.utcnow() > _sku_title_cache_expire_at:
            _sku_title_cache = {}
            _sku_title_cache_expire_at = None
            return

        if not isinstance(cache_data, dict):
            return

        try:
            _sku_title_cache = {str(k): safe_strip(v) for k, v in cache_data.items() if safe_strip(v)}
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Invalid SKU title cache contents: %s", exc)
            _sku_title_cache = {}


def _persist_sku_title_cache() -> None:
    """Persist sku->title cache to disk."""

    global _sku_title_cache_expire_at

    with _sku_cache_lock:
        _sku_title_cache_expire_at = datetime.utcnow() + SKU_TITLE_CACHE_TTL
        payload = {"expires_at": _sku_title_cache_expire_at.isoformat(), SKU_TITLE_CACHE_KEY: _sku_title_cache}
        try:
            SKU_TITLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(SKU_TITLE_CACHE_PATH, payload)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Failed to persist SKU title cache: %s", exc)


def get_sku_title_from_cache(key: str | None) -> str | None:
    if not key:
        return None
    _load_sku_title_cache()
    with _sku_cache_lock:
        return _sku_title_cache.get(str(key))


def save_sku_title_to_cache(key: str | int | None, title: str | None) -> None:
    if not key or not safe_strip(title):
        return
    _load_sku_title_cache()
    with _sku_cache_lock:
        _sku_title_cache[str(key)] = safe_strip(title)  # type: ignore[index]
    _persist_sku_title_cache()


__all__ = [
    "SKU_TITLE_CACHE_TTL",
    "SKU_TITLE_CACHE_PATH",
    "get_sku_title_from_cache",
    "save_sku_title_to_cache",
]
