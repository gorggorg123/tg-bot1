# botapp/api/rate_limiting.py
"""
Rate limiting для Ozon API (a-ulianov/OzonAPI стиль).

Реализует умное управление запросами:
- Глобальный rate limiter на основе конфигурации
- Per-endpoint rate limiters для специфичных методов
- Скользящее окно для точного контроля

Документация Ozon: 50 RPS суммарно, но рекомендуется 25-27 для стабильности.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from time import monotonic
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SimpleRateLimiter:
    """
    Асинхронный rate limiter на основе скользящего окна.
    
    Реализует алгоритм из a-ulianov/OzonAPI для соблюдения лимитов Ozon API.
    
    Args:
        rate: Максимальное количество запросов в окне
        per_seconds: Размер окна в секундах
        name: Имя лимитера для логирования (опционально)
    
    Example:
        limiter = SimpleRateLimiter(rate=27, per_seconds=1.0, name="global")
        await limiter.wait()  # Ждёт, если лимит превышен
    """
    
    def __init__(self, *, rate: int, per_seconds: float, name: str | None = None):
        self.rate = max(1, rate)
        self.per_seconds = max(per_seconds, 0.001)
        self.name = name or "unnamed"
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._wait_count = 0

    async def wait(self) -> float:
        """
        Ждёт, пока не освободится слот для запроса.
        
        Returns:
            Время ожидания в секундах (0 если не ждали)
        """
        async with self._lock:
            now = monotonic()
            window_start = now - self.per_seconds
            
            # Удаляем устаревшие записи
            while self._calls and self._calls[0] < window_start:
                self._calls.popleft()

            # Если есть свободный слот - добавляем запись и выходим
            if len(self._calls) < self.rate:
                self._calls.append(now)
                return 0.0

            # Иначе ждём, пока освободится слот
            sleep_for = self._calls[0] + self.per_seconds - now
            sleep_for = max(sleep_for, 0)
            
            if sleep_for > 0:
                self._wait_count += 1
                logger.debug(
                    "Rate limiter '%s' throttling: waiting %.3fs (total waits: %d)",
                    self.name,
                    sleep_for,
                    self._wait_count,
                )
                await asyncio.sleep(sleep_for)
                
            self._calls.append(monotonic())
            return sleep_for

    @property
    def current_load(self) -> int:
        """Текущее количество запросов в окне."""
        now = monotonic()
        window_start = now - self.per_seconds
        return sum(1 for t in self._calls if t >= window_start)

    @property
    def available_slots(self) -> int:
        """Количество доступных слотов."""
        return max(0, self.rate - self.current_load)

    @property
    def stats(self) -> dict:
        """Статистика лимитера."""
        return {
            "name": self.name,
            "rate": self.rate,
            "per_seconds": self.per_seconds,
            "current_load": self.current_load,
            "available_slots": self.available_slots,
            "total_waits": self._wait_count,
        }


def _get_default_global_rate() -> int:
    """Получить глобальный лимит из конфига или вернуть дефолт."""
    try:
        from botapp.ozon_api_config import get_ozon_config
        config = get_ozon_config()
        return config.default_profile.max_requests_per_second
    except Exception:
        return 27  # Оптимальное значение для Ozon API


# Глобальный rate limiter (создаётся лениво)
_global_rate_limiter: SimpleRateLimiter | None = None


def get_global_rate_limiter() -> SimpleRateLimiter:
    """
    Получить глобальный rate limiter (как в a-ulianov/OzonAPI).
    
    Лимит берётся из OzonAPIConfig.max_requests_per_second.
    """
    global _global_rate_limiter
    if _global_rate_limiter is None:
        rate = _get_default_global_rate()
        _global_rate_limiter = SimpleRateLimiter(
            rate=rate,
            per_seconds=1.0,
            name="global",
        )
        logger.info("Global rate limiter created: %d RPS", rate)
    return _global_rate_limiter


# Per-endpoint rate limiters (документированные лимиты Ozon)
# Значения взяты из практики работы с Ozon API
ENDPOINT_LIMITS: Dict[str, tuple[int, float]] = {
    # Чаты - жёсткие лимиты
    "/v3/chat/history": (5, 1.0),
    "/v3/chat/list": (5, 1.0),
    "/v2/chat/history": (5, 1.0),
    "/v2/chat/list": (5, 1.0),
    "/v1/chat/history": (5, 1.0),
    "/v1/chat/list": (5, 1.0),
    "/v1/chat/send/message": (3, 1.0),
    "/v1/chat/start": (3, 1.0),
    "/v2/chat/read": (10, 1.0),
    # Товары
    "/v1/product/info": (20, 1.0),
    "/v2/product/info": (20, 1.0),
    "/v3/product/info/list": (30, 1.0),
    "/v3/product/list": (30, 1.0),
    # Отзывы и вопросы
    "/v1/review/list": (10, 1.0),
    "/v1/review/comment/create": (5, 1.0),
    "/v1/question/list": (20, 1.0),
    "/v1/question/answer/create": (10, 1.0),
    "/v1/question/answer/list": (50, 1.0),
    # Аналитика (строгие лимиты)
    "/v1/analytics/data": (5, 1.0),
    # Финансы
    "/v3/finance/transaction/totals": (10, 1.0),
    # FBO/FBS
    "/v2/posting/fbo/list": (20, 1.0),
    "/v3/posting/fbs/list": (20, 1.0),
}

# Кеш созданных per-endpoint лимитеров
_endpoint_limiters: Dict[str, SimpleRateLimiter] = {}


def get_rate_limiter_for_path(path: str) -> SimpleRateLimiter | None:
    """
    Получить rate limiter для указанного пути API.
    
    Args:
        path: Путь API (например, "/v3/chat/history")
        
    Returns:
        Rate limiter или None, если для этого пути нет специального лимитера
    """
    # Нормализуем путь
    normalized = path if path.startswith("/") else f"/{path}"
    
    # Проверяем кеш
    if normalized in _endpoint_limiters:
        return _endpoint_limiters[normalized]
    
    # Создаём лимитер если есть в конфигурации
    if normalized in ENDPOINT_LIMITS:
        rate, per_seconds = ENDPOINT_LIMITS[normalized]
        limiter = SimpleRateLimiter(
            rate=rate,
            per_seconds=per_seconds,
            name=normalized,
        )
        _endpoint_limiters[normalized] = limiter
        return limiter
    
    return None


# Предопределённые rate limiters для обратной совместимости
# (создаются лениво через get_rate_limiter_for_path)
def _get_or_create_limiter(path: str, default_rate: int = 10) -> SimpleRateLimiter:
    """Получить или создать лимитер с fallback на дефолт."""
    limiter = get_rate_limiter_for_path(path)
    if limiter is None:
        limiter = SimpleRateLimiter(rate=default_rate, per_seconds=1.0, name=path)
        _endpoint_limiters[path] = limiter
    return limiter


# Обратная совместимость - ленивые свойства
class _LazyLimiters:
    """Ленивая инициализация лимитеров для обратной совместимости."""
    
    @property
    def question_answer(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v1/question/answer/list", 50)
    
    @property
    def chat_history(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v3/chat/history", 5)
    
    @property
    def chat_list(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v3/chat/list", 5)
    
    @property
    def chat_send(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v1/chat/send/message", 3)
    
    @property
    def review(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v1/review/list", 10)
    
    @property
    def product_info(self) -> SimpleRateLimiter:
        return _get_or_create_limiter("/v1/product/info", 20)


_lazy = _LazyLimiters()

# Экспорт для обратной совместимости (теперь через ленивые свойства)
question_answer_rate_limiter = property(lambda self: _lazy.question_answer)
chat_history_rate_limiter = property(lambda self: _lazy.chat_history)
chat_list_rate_limiter = property(lambda self: _lazy.chat_list)
chat_send_rate_limiter = property(lambda self: _lazy.chat_send)
review_rate_limiter = property(lambda self: _lazy.review)
product_info_rate_limiter = property(lambda self: _lazy.product_info)

# Маппинг путей API на rate limiters (для обратной совместимости)
PATH_RATE_LIMITERS: Dict[str, SimpleRateLimiter] = {}  # Заполняется лениво


def get_all_limiter_stats() -> list[dict]:
    """Получить статистику всех активных лимитеров."""
    stats = []
    
    # Глобальный лимитер
    if _global_rate_limiter:
        stats.append(_global_rate_limiter.stats)
    
    # Per-endpoint лимитеры
    for limiter in _endpoint_limiters.values():
        stats.append(limiter.stats)
    
    return stats


__all__ = [
    "SimpleRateLimiter",
    "ENDPOINT_LIMITS",
    "get_rate_limiter_for_path",
    "get_global_rate_limiter",
    "get_all_limiter_stats",
    # Обратная совместимость
    "PATH_RATE_LIMITERS",
    "question_answer_rate_limiter",
    "chat_history_rate_limiter", 
    "chat_list_rate_limiter",
    "chat_send_rate_limiter",
    "review_rate_limiter",
    "product_info_rate_limiter",
]
