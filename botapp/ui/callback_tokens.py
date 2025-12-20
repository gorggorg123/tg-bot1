"""Shared token storage for compact callback payloads."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


@dataclass
class TokenStore:
    """In-memory token mapping with per-user TTL cleanup."""

    ttl_seconds: int = 300
    _tokens: Dict[int, Dict[str, tuple[Any, datetime]]] = field(default_factory=dict)
    _key_map: Dict[int, Dict[str, str]] = field(default_factory=dict)

    def _now(self) -> datetime:
        return datetime.utcnow()

    def _cleanup_user(self, user_id: int) -> None:
        expire_before = self._now() - timedelta(seconds=int(self.ttl_seconds))
        tokens = self._tokens.get(user_id)
        if not tokens:
            self._tokens.pop(user_id, None)
            self._key_map.pop(user_id, None)
            return

        for token, (_, created_at) in list(tokens.items()):
            if created_at < expire_before:
                tokens.pop(token, None)

        if not tokens:
            self._tokens.pop(user_id, None)
            self._key_map.pop(user_id, None)
            return

        key_map = self._key_map.get(user_id)
        if not key_map:
            return
        for key, token in list(key_map.items()):
            if token not in tokens:
                key_map.pop(key, None)
        if not key_map:
            self._key_map.pop(user_id, None)

    def generate(self, user_id: int, payload: Any, *, key: str | None = None) -> str:
        """Store payload under a short token, reusing a stable token for a key if provided."""

        self._cleanup_user(user_id)
        if key is not None:
            existing = self._key_map.setdefault(user_id, {}).get(str(key))
            if existing and existing in self._tokens.get(user_id, {}):
                self._tokens[user_id][existing] = (payload, self._now())
                return existing

        token = secrets.token_hex(4)
        self._tokens.setdefault(user_id, {})[token] = (payload, self._now())
        if key is not None:
            self._key_map.setdefault(user_id, {})[str(key)] = token
        return token

    def resolve(self, user_id: int, token: str) -> Optional[Any]:
        """Return payload for a token if present and not expired."""

        self._cleanup_user(user_id)
        payload = self._tokens.get(user_id, {}).get(token)
        if not payload:
            return None

        value, created_at = payload
        if (self._now() - created_at) > timedelta(seconds=int(self.ttl_seconds)):
            self._tokens[user_id].pop(token, None)
            key_map = self._key_map.get(user_id)
            if key_map:
                for key, stored in list(key_map.items()):
                    if stored == token:
                        key_map.pop(key, None)
                if not key_map:
                    self._key_map.pop(user_id, None)
            return None
        return value

    def clear(self, user_id: int) -> None:
        self._tokens.pop(user_id, None)
        self._key_map.pop(user_id, None)


__all__ = ["TokenStore"]
