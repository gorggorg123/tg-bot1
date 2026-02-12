"""
Base repository - базовый класс для всех репозиториев
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar

from cachetools import TTLCache

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """
    Базовый репозиторий
    
    Предоставляет общие методы для работы с данными:
    - Кеширование с TTL
    - Загрузка/сохранение в файлы
    - CRUD операции
    """
    
    def __init__(
        self,
        *,
        cache_ttl: int = 300,
        cache_maxsize: int = 1000,
        storage_path: Optional[Path] = None,
    ):
        """
        Args:
            cache_ttl: TTL кеша в секундах
            cache_maxsize: Максимальный размер кеша
            storage_path: Путь к файлу хранилища
        """
        self._cache: TTLCache = TTLCache(maxsize=cache_maxsize, ttl=cache_ttl)
        self._storage_path = storage_path
        self._lock = asyncio.Lock()
    
    def _cache_key(self, **kwargs) -> str:
        """Создать ключ кеша из параметров"""
        parts = []
        for k, v in sorted(kwargs.items()):
            if v is not None:
                parts.append(f"{k}={v}")
        return ":".join(parts)
    
    def _get_from_cache(self, key: str) -> Optional[T]:
        """Получить из кеша"""
        return self._cache.get(key)
    
    def _put_to_cache(self, key: str, value: T) -> None:
        """Положить в кеш"""
        self._cache[key] = value
    
    def _invalidate_cache(self, key: Optional[str] = None) -> None:
        """Инвалидировать кеш"""
        if key:
            self._cache.pop(key, None)
        else:
            self._cache.clear()
    
    async def _load_from_file(self) -> Dict[str, Any]:
        """Загрузить данные из файла"""
        if not self._storage_path or not self._storage_path.exists():
            return {}
        
        try:
            async with self._lock:
                with open(self._storage_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("Не удалось загрузить из %s: %s", self._storage_path, e)
            return {}
    
    async def _save_to_file(self, data: Dict[str, Any]) -> None:
        """Сохранить данные в файл"""
        if not self._storage_path:
            return
        
        try:
            async with self._lock:
                # Создаем директорию если нужно
                self._storage_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Атомарная запись через временный файл
                temp_path = self._storage_path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                
                # Переименовываем
                temp_path.replace(self._storage_path)
                
                logger.debug("Данные сохранены в %s", self._storage_path)
        except Exception as e:
            logger.error("Ошибка сохранения в %s: %s", self._storage_path, e)
    
    @abstractmethod
    async def get(self, id: Any) -> Optional[T]:
        """Получить объект по ID"""
        pass
    
    @abstractmethod
    async def list(self, **filters) -> List[T]:
        """Получить список объектов"""
        pass
    
    @abstractmethod
    async def save(self, obj: T) -> T:
        """Сохранить объект"""
        pass
    
    @abstractmethod
    async def delete(self, id: Any) -> bool:
        """Удалить объект"""
        pass
