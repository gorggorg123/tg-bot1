"""Shared contracts and helpers for section list/card flows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, Tuple


def is_cache_fresh(loaded_at: datetime | None, ttl_seconds: int) -> bool:
    if not loaded_at:
        return False
    return (datetime.utcnow() - loaded_at) < timedelta(seconds=int(ttl_seconds))


@dataclass
class SectionCache:
    loaded_at: datetime | None = None
    ttl_seconds: int = 120

    def fresh(self) -> bool:
        return is_cache_fresh(self.loaded_at, self.ttl_seconds)


class SectionRenderer(Protocol):
    async def render_list(self, *args: Any, **kwargs: Any) -> Tuple[str, Any]:
        ...

    async def render_card(self, *args: Any, **kwargs: Any) -> Tuple[str, Any]:
        ...

    async def refresh_cache(self, *args: Any, **kwargs: Any) -> Any:
        ...


__all__ = ["SectionCache", "SectionRenderer", "is_cache_fresh"]
