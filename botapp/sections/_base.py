"""
Shared contracts and helpers for section list/card flows.
Базовые классы и протоколы для унификации работы секций.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Generic, Protocol, Tuple, TypeVar

from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def is_cache_fresh(loaded_at: datetime | None, ttl_seconds: int) -> bool:
    """Проверить, свежий ли кеш."""
    if not loaded_at:
        return False
    now = datetime.now(timezone.utc)
    if loaded_at.tzinfo is None:
        loaded_at = loaded_at.replace(tzinfo=timezone.utc)
    return (now - loaded_at) < timedelta(seconds=int(ttl_seconds))


@dataclass
class SectionCache:
    """Кеш данных секции с TTL."""
    loaded_at: datetime | None = None
    ttl_seconds: int = 120

    def fresh(self) -> bool:
        """Проверить, свежий ли кеш."""
        return is_cache_fresh(self.loaded_at, self.ttl_seconds)
    
    def mark_loaded(self) -> None:
        """Пометить кеш как загруженный сейчас."""
        self.loaded_at = datetime.now(timezone.utc)
    
    def invalidate(self) -> None:
        """Инвалидировать кеш."""
        self.loaded_at = None


class SectionRenderer(Protocol):
    """Протокол для рендерера секции."""
    
    async def render_list(self, *args: Any, **kwargs: Any) -> Tuple[str, Any]:
        """Отрендерить список."""
        ...

    async def render_card(self, *args: Any, **kwargs: Any) -> Tuple[str, Any]:
        """Отрендерить карточку."""
        ...

    async def refresh_cache(self, *args: Any, **kwargs: Any) -> Any:
        """Обновить кеш."""
        ...


# Типы для Generic
T = TypeVar("T")  # Тип элемента списка
C = TypeVar("C")  # Тип карточки


@dataclass
class ListPageResult(Generic[T]):
    """Результат загрузки страницы списка."""
    items: list[T] = field(default_factory=list)
    page: int = 0
    total_pages: int = 1
    total_items: int = 0
    
    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages - 1
    
    @property
    def has_prev(self) -> bool:
        return self.page > 0


class BaseSectionHandler(ABC):
    """
    Абстрактный базовый класс для обработчиков секций.
    Унифицирует общую логику для reviews, questions, chats.
    """
    
    # Переопределить в наследниках
    section_list: str = ""  # ID секции списка
    section_card: str = ""  # ID секции карточки
    section_prompt: str = ""  # ID секции промпта
    
    @abstractmethod
    async def show_list(
        self,
        user_id: int,
        page: int = 0,
        category: str = "all",
        callback: CallbackQuery | None = None,
        message: Message | None = None,
        force_refresh: bool = False,
    ) -> Message | None:
        """Показать список элементов."""
        ...
    
    @abstractmethod
    async def show_card(
        self,
        user_id: int,
        token: str,
        page: int = 0,
        category: str = "all",
        callback: CallbackQuery | None = None,
        message: Message | None = None,
    ) -> Message | None:
        """Показать карточку элемента."""
        ...
    
    @abstractmethod
    async def handle_ai_action(
        self,
        user_id: int,
        token: str,
        callback: CallbackQuery | None = None,
    ) -> str | None:
        """Сгенерировать AI-ответ. Возвращает текст черновика."""
        ...
    
    async def handle_back_to_list(
        self,
        user_id: int,
        page: int = 0,
        category: str = "all",
        callback: CallbackQuery | None = None,
    ) -> Message | None:
        """Обработать возврат к списку."""
        return await self.show_list(
            user_id=user_id,
            page=page,
            category=category,
            callback=callback,
            force_refresh=False,
        )


__all__ = [
    "SectionCache",
    "SectionRenderer",
    "ListPageResult",
    "BaseSectionHandler",
    "is_cache_fresh",
]
