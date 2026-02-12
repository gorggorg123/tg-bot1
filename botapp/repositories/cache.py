"""
Cache repository - продвинутое кеширование
Использует идеи от Ульянова + собственные улучшения
"""
from __future__ import annotations

import hashlib
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from cachetools import TTLCache, LRUCache

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CacheRepository:
    """
    Продвинутый кеш-репозиторий
    
    Поддерживает:
    - Многоуровневое кеширование (L1 memory, L2 disk)
    - TTL и LRU стратегии
    - Персистентность на диск
    - Cache warming
    - Метрики
    """
    
    def __init__(
        self,
        *,
        name: str = "default",
        memory_ttl: int = 300,
        memory_maxsize: int = 1000,
        disk_path: Optional[Path] = None,
        enable_disk_cache: bool = False,
    ):
        """
        Args:
            name: Имя кеша (для логов)
            memory_ttl: TTL для L1 кеша
            memory_maxsize: Размер L1 кеша
            disk_path: Путь для L2 кеша
            enable_disk_cache: Включить ли disk cache
        """
        self.name = name
        
        # L1: In-memory cache with TTL
        self._memory_cache: TTLCache = TTLCache(maxsize=memory_maxsize, ttl=memory_ttl)
        
        # L2: Disk cache (persistent)
        self._disk_cache_enabled = enable_disk_cache and disk_path is not None
        self._disk_path = disk_path
        
        # Metrics
        self._hits = 0
        self._misses = 0
        self._memory_hits = 0
        self._disk_hits = 0
        
        if self._disk_cache_enabled and disk_path:
            disk_path.mkdir(parents=True, exist_ok=True)
            logger.info("CacheRepository '%s' initialized with disk cache at %s", name, disk_path)
        else:
            logger.info("CacheRepository '%s' initialized (memory only)", name)
    
    def _make_key(self, key: str) -> str:
        """Создать безопасный ключ"""
        # Хешируем длинные ключи
        if len(key) > 200:
            return hashlib.sha256(key.encode()).hexdigest()
        return key
    
    def _disk_file_path(self, key: str) -> Path:
        """Путь к файлу на диске"""
        safe_key = self._make_key(key)
        return self._disk_path / f"{safe_key}.pkl"
    
    async def get(
        self,
        key: str,
        *,
        default: Any = None,
        fallback: Optional[Callable[[], Any]] = None,
    ) -> Any:
        """
        Получить значение из кеша
        
        Args:
            key: Ключ
            default: Значение по умолчанию
            fallback: Функция для получения значения если нет в кеше
        
        Returns:
            Значение из кеша или default
        """
        safe_key = self._make_key(key)
        
        # Try L1: memory cache
        if safe_key in self._memory_cache:
            self._hits += 1
            self._memory_hits += 1
            logger.debug("[%s] Memory cache HIT: %s", self.name, key[:50])
            return self._memory_cache[safe_key]
        
        # Try L2: disk cache
        if self._disk_cache_enabled:
            try:
                disk_file = self._disk_file_path(safe_key)
                if disk_file.exists():
                    with open(disk_file, "rb") as f:
                        value = pickle.load(f)
                    
                    # Promote to L1
                    self._memory_cache[safe_key] = value
                    
                    self._hits += 1
                    self._disk_hits += 1
                    logger.debug("[%s] Disk cache HIT: %s", self.name, key[:50])
                    return value
            except Exception as e:
                logger.warning("[%s] Disk cache read error: %s", self.name, e)
        
        # Cache MISS
        self._misses += 1
        logger.debug("[%s] Cache MISS: %s", self.name, key[:50])
        
        # Use fallback if provided
        if fallback:
            try:
                value = await fallback() if asyncio.iscoroutinefunction(fallback) else fallback()
                await self.set(key, value)
                return value
            except Exception as e:
                logger.error("[%s] Fallback error: %s", self.name, e)
        
        return default
    
    async def set(self, key: str, value: Any, *, ttl: Optional[int] = None) -> None:
        """
        Сохранить значение в кеш
        
        Args:
            key: Ключ
            value: Значение
            ttl: TTL (если None - используется дефолтный)
        """
        safe_key = self._make_key(key)
        
        # Save to L1
        self._memory_cache[safe_key] = value
        
        # Save to L2
        if self._disk_cache_enabled:
            try:
                disk_file = self._disk_file_path(safe_key)
                with open(disk_file, "wb") as f:
                    pickle.dump(value, f)
                logger.debug("[%s] Saved to disk cache: %s", self.name, key[:50])
            except Exception as e:
                logger.warning("[%s] Disk cache write error: %s", self.name, e)
    
    async def delete(self, key: str) -> None:
        """Удалить из кеша"""
        safe_key = self._make_key(key)
        
        # Remove from L1
        self._memory_cache.pop(safe_key, None)
        
        # Remove from L2
        if self._disk_cache_enabled:
            try:
                disk_file = self._disk_file_path(safe_key)
                disk_file.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("[%s] Disk cache delete error: %s", self.name, e)
    
    async def clear(self) -> None:
        """Очистить весь кеш"""
        # Clear L1
        self._memory_cache.clear()
        
        # Clear L2
        if self._disk_cache_enabled and self._disk_path:
            try:
                for file in self._disk_path.glob("*.pkl"):
                    file.unlink()
                logger.info("[%s] Disk cache cleared", self.name)
            except Exception as e:
                logger.warning("[%s] Disk cache clear error: %s", self.name, e)
    
    def get_stats(self) -> dict:
        """Получить статистику кеша"""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        
        return {
            "name": self.name,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{hit_rate:.1f}%",
            "memory_hits": self._memory_hits,
            "disk_hits": self._disk_hits,
            "memory_size": len(self._memory_cache),
            "disk_enabled": self._disk_cache_enabled,
        }


# Импорт для asyncio проверки
import asyncio
