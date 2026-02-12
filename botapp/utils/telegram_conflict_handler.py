# botapp/utils/telegram_conflict_handler.py
"""
Утилиты для обработки TelegramConflictError и других Telegram API ошибок.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Any

from aiogram.exceptions import TelegramConflictError

logger = logging.getLogger(__name__)


class ConflictRecoveryHandler:
    """
    Обработчик для graceful recovery от TelegramConflictError.
    
    Используется для обработки ситуаций, когда запущено несколько экземпляров бота.
    """
    
    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        exponential_backoff: bool = True,
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.exponential_backoff = exponential_backoff
        self._conflict_count = 0
        self._last_conflict_time: float | None = None
    
    async def handle_with_retry(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Выполняет функцию с автоматическим retry при TelegramConflictError.
        
        Args:
            func: Асинхронная функция для выполнения
            *args: Позиционные аргументы
            **kwargs: Именованные аргументы
            
        Returns:
            Результат выполнения функции
            
        Raises:
            TelegramConflictError: Если все попытки исчерпаны
        """
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except TelegramConflictError as e:
                last_exception = e
                self._conflict_count += 1
                
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay
                    if self.exponential_backoff:
                        delay *= (2 ** attempt)
                    
                    logger.warning(
                        "TelegramConflictError (попытка %d/%d). "
                        "Повтор через %.1f сек. Всего конфликтов: %d",
                        attempt + 1,
                        self.max_retries,
                        delay,
                        self._conflict_count,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "❌ Все %d попыток исчерпаны. "
                        "Вероятно, запущен другой экземпляр бота!",
                        self.max_retries,
                    )
        
        if last_exception:
            raise last_exception
        
        return None
    
    def get_stats(self) -> dict[str, Any]:
        """Возвращает статистику по конфликтам."""
        return {
            "total_conflicts": self._conflict_count,
            "last_conflict_time": self._last_conflict_time,
        }


# Глобальный экземпляр для использования в боте
_global_handler: ConflictRecoveryHandler | None = None


def get_conflict_handler() -> ConflictRecoveryHandler:
    """Возвращает глобальный экземпляр обработчика конфликтов."""
    global _global_handler
    if _global_handler is None:
        _global_handler = ConflictRecoveryHandler(
            max_retries=3,
            retry_delay=2.0,
            exponential_backoff=True,
        )
    return _global_handler
