"""
Live Logger with WebSocket Support
==================================
Система логирования в реальном времени через WebSocket

Позволяет:
- Просматривать логи в реальном времени через браузер
- Фильтровать по уровню (DEBUG, INFO, WARNING, ERROR)
- Поиск по логам
- Экспорт логов

Использование:
    from botapp.monitoring import get_live_logger
    
    logger = get_live_logger()
    logger.log_event("User action", level="INFO", data={"user_id": 123})
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from enum import Enum

logger = logging.getLogger(__name__)


class LogLevel(str, Enum):
    """Уровни логирования"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEntry:
    """Запись лога"""
    timestamp: datetime
    level: LogLevel
    message: str
    source: str
    data: Dict[str, Any] = field(default_factory=dict)
    user_id: Optional[int] = None
    chat_id: Optional[int] = None
    handler: Optional[str] = None
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертировать в словарь для JSON"""
        result = asdict(self)
        result["timestamp"] = self.timestamp.isoformat()
        result["level"] = self.level.value
        return result


class LiveLogger:
    """
    Логгер с поддержкой live-streaming через WebSocket
    """
    
    def __init__(
        self,
        *,
        max_buffer_size: int = 1000,
        enable_websocket: bool = True,
    ):
        """
        Args:
            max_buffer_size: Максимальное количество логов в буфере
            enable_websocket: Включить WebSocket поддержку
        """
        self.max_buffer_size = max_buffer_size
        self.enable_websocket = enable_websocket
        
        # Буфер логов
        self._log_buffer: deque[LogEntry] = deque(maxlen=max_buffer_size)
        
        # WebSocket клиенты
        self._ws_clients: Set[Any] = set()
        
        # Статистика
        self._total_logs = 0
        self._logs_by_level: Dict[LogLevel, int] = {
            level: 0 for level in LogLevel
        }
        
        logger.info("LiveLogger initialized: buffer_size=%s, websocket=%s", max_buffer_size, enable_websocket)
    
    def log_event(
        self,
        message: str,
        *,
        level: str | LogLevel = LogLevel.INFO,
        source: str = "bot",
        data: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        handler: Optional[str] = None,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Залогировать событие
        
        Args:
            message: Текст сообщения
            level: Уровень логирования
            source: Источник (bot, ozon_api, database, etc.)
            data: Дополнительные данные
            user_id: ID пользователя (если применимо)
            chat_id: ID чата (если применимо)
            handler: Имя handler'а
            duration_ms: Длительность операции (мс)
            error: Текст ошибки (если есть)
        """
        # Нормализация level
        if isinstance(level, str):
            try:
                level = LogLevel(level.upper())
            except ValueError:
                level = LogLevel.INFO
        
        # Создание записи
        entry = LogEntry(
            timestamp=datetime.utcnow(),
            level=level,
            message=message,
            source=source,
            data=data or {},
            user_id=user_id,
            chat_id=chat_id,
            handler=handler,
            duration_ms=duration_ms,
            error=error,
        )
        
        # Добавление в буфер
        self._log_buffer.append(entry)
        
        # Обновление статистики
        self._total_logs += 1
        self._logs_by_level[level] = self._logs_by_level.get(level, 0) + 1
        
        # Отправка через WebSocket (если включено)
        if self.enable_websocket and self._ws_clients:
            asyncio.create_task(self._broadcast_log(entry))
        
        # Логирование в стандартный logger
        self._log_to_standard_logger(entry)
    
    def _log_to_standard_logger(self, entry: LogEntry) -> None:
        """Логирование в стандартный Python logger"""
        extra_info = []
        if entry.user_id:
            extra_info.append(f"user={entry.user_id}")
        if entry.chat_id:
            extra_info.append(f"chat={entry.chat_id}")
        if entry.handler:
            extra_info.append(f"handler={entry.handler}")
        if entry.duration_ms:
            extra_info.append(f"duration={entry.duration_ms:.2f}ms")
        
        log_message = f"[{entry.source}] {entry.message}"
        if extra_info:
            log_message += f" ({', '.join(extra_info)})"
        
        # Выбор уровня логирования
        level_map = {
            LogLevel.DEBUG: logging.DEBUG,
            LogLevel.INFO: logging.INFO,
            LogLevel.WARNING: logging.WARNING,
            LogLevel.ERROR: logging.ERROR,
            LogLevel.CRITICAL: logging.CRITICAL,
        }
        
        standard_level = level_map.get(entry.level, logging.INFO)
        logger.log(standard_level, log_message)
        
        if entry.error:
            logger.log(standard_level, "Error details: %s", entry.error)
    
    async def _broadcast_log(self, entry: LogEntry) -> None:
        """Отправить лог всем подключенным WebSocket клиентам"""
        if not self._ws_clients:
            return
        
        # Сериализация
        try:
            message = entry.to_dict()
        except Exception as e:
            logger.error("Failed to serialize log entry: %s", e)
            return
        
        # Отправка всем клиентам
        disconnected = set()
        for client in self._ws_clients:
            try:
                await client.send_json(message)
            except Exception as e:
                logger.warning("Failed to send log to WebSocket client: %s", e)
                disconnected.add(client)
        
        # Удаление отключенных клиентов
        self._ws_clients -= disconnected
    
    def register_ws_client(self, client: Any) -> None:
        """Зарегистрировать WebSocket клиента"""
        self._ws_clients.add(client)
        logger.info("WebSocket client registered: total=%s", len(self._ws_clients))
    
    def unregister_ws_client(self, client: Any) -> None:
        """Отменить регистрацию WebSocket клиента"""
        self._ws_clients.discard(client)
        logger.info("WebSocket client unregistered: total=%s", len(self._ws_clients))
    
    def get_recent_logs(
        self,
        *,
        limit: int = 100,
        level: Optional[LogLevel] = None,
        source: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить недавние логи
        
        Args:
            limit: Максимальное количество
            level: Фильтр по уровню
            source: Фильтр по источнику
        
        Returns:
            Список логов
        """
        logs = list(self._log_buffer)
        
        # Фильтрация
        if level:
            logs = [log for log in logs if log.level == level]
        if source:
            logs = [log for log in logs if log.source == source]
        
        # Ограничение
        logs = logs[-limit:]
        
        # Сериализация
        return [log.to_dict() for log in logs]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Получить статистику логирования"""
        return {
            "total_logs": self._total_logs,
            "buffer_size": len(self._log_buffer),
            "max_buffer_size": self.max_buffer_size,
            "logs_by_level": {
                level.value: count
                for level, count in self._logs_by_level.items()
            },
            "active_ws_clients": len(self._ws_clients),
        }
    
    def clear_buffer(self) -> None:
        """Очистить буфер логов"""
        self._log_buffer.clear()
        logger.info("Log buffer cleared")


# ===== Глобальный экземпляр =====

_live_logger: Optional[LiveLogger] = None


def get_live_logger() -> LiveLogger:
    """Получить глобальный экземпляр LiveLogger"""
    global _live_logger
    if _live_logger is None:
        _live_logger = LiveLogger()
    return _live_logger


def init_live_logger(
    *,
    max_buffer_size: int = 1000,
    enable_websocket: bool = True,
) -> LiveLogger:
    """Инициализировать LiveLogger с кастомными настройками"""
    global _live_logger
    _live_logger = LiveLogger(
        max_buffer_size=max_buffer_size,
        enable_websocket=enable_websocket,
    )
    return _live_logger


__all__ = [
    "LiveLogger",
    "LogEntry",
    "LogLevel",
    "get_live_logger",
    "init_live_logger",
]
