"""
Metrics Collector
================
Сбор метрик производительности бота

Метрики:
- Запросы к Ozon API (количество, время ответа, ошибки)
- Обработка хендлеров (время выполнения, ошибки)
- Использование памяти и CPU
- Количество пользователей, сообщений
- Кеш-статистика
"""
from __future__ import annotations

import logging
import time
import psutil
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Deque

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """Точка данных метрики"""
    timestamp: datetime
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "value": self.value,
            "labels": self.labels,
        }


@dataclass
class HandlerMetrics:
    """Метрики обработчика"""
    name: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_duration: float = 0.0
    min_duration: Optional[float] = None
    max_duration: Optional[float] = None
    recent_durations: Deque[float] = field(default_factory=lambda: deque(maxlen=100))
    errors: Dict[str, int] = field(default_factory=dict)
    
    @property
    def average_duration(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_duration / self.total_calls
    
    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return (self.successful_calls / self.total_calls) * 100
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "average_duration": round(self.average_duration, 3),
            "min_duration": round(self.min_duration, 3) if self.min_duration else None,
            "max_duration": round(self.max_duration, 3) if self.max_duration else None,
            "success_rate": round(self.success_rate, 2),
            "errors": dict(self.errors),
        }


class MetricsCollector:
    """
    Коллектор метрик производительности
    """
    
    def __init__(self, *, max_history_points: int = 1000):
        """
        Args:
            max_history_points: Максимум точек истории для каждой метрики
        """
        self.max_history_points = max_history_points
        
        # История метрик
        self._metrics_history: Dict[str, Deque[MetricPoint]] = defaultdict(
            lambda: deque(maxlen=max_history_points)
        )
        
        # Метрики хендлеров
        self._handler_metrics: Dict[str, HandlerMetrics] = {}
        
        # Системные метрики
        self._process = psutil.Process()
        
        # Счетчики
        self._counters: Dict[str, int] = defaultdict(int)
        
        # Время старта
        self._start_time = time.time()
        
        logger.info("MetricsCollector initialized")
    
    def record_metric(
        self,
        name: str,
        value: float,
        *,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Записать метрику
        
        Args:
            name: Имя метрики (requests_total, response_time, etc.)
            value: Значение
            labels: Метки (handler, status, etc.)
        """
        point = MetricPoint(
            timestamp=datetime.utcnow(),
            value=value,
            labels=labels or {},
        )
        self._metrics_history[name].append(point)
    
    def record_handler_call(
        self,
        handler_name: str,
        duration: float,
        *,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """
        Записать вызов хендлера
        
        Args:
            handler_name: Имя хендлера
            duration: Время выполнения (секунды)
            success: Успешно ли выполнен
            error: Тип ошибки (если есть)
        """
        if handler_name not in self._handler_metrics:
            self._handler_metrics[handler_name] = HandlerMetrics(name=handler_name)
        
        metrics = self._handler_metrics[handler_name]
        metrics.total_calls += 1
        
        if success:
            metrics.successful_calls += 1
        else:
            metrics.failed_calls += 1
            if error:
                metrics.errors[error] = metrics.errors.get(error, 0) + 1
        
        # Обновление времени выполнения
        metrics.total_duration += duration
        metrics.recent_durations.append(duration)
        
        if metrics.min_duration is None or duration < metrics.min_duration:
            metrics.min_duration = duration
        if metrics.max_duration is None or duration > metrics.max_duration:
            metrics.max_duration = duration
    
    def increment_counter(self, name: str, value: int = 1) -> None:
        """Увеличить счетчик"""
        self._counters[name] += value
    
    def get_counter(self, name: str) -> int:
        """Получить значение счетчика"""
        return self._counters.get(name, 0)
    
    def get_metric_history(
        self,
        name: str,
        *,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """
        Получить историю метрики
        
        Args:
            name: Имя метрики
            limit: Максимум точек
            since: Начало временного диапазона
        
        Returns:
            История метрики
        """
        history = list(self._metrics_history.get(name, []))
        
        # Фильтрация по времени
        if since:
            history = [p for p in history if p.timestamp >= since]
        
        # Ограничение
        history = history[-limit:]
        
        return [p.to_dict() for p in history]
    
    def get_handler_metrics(
        self,
        handler_name: Optional[str] = None,
    ) -> Dict[str, Any] | List[Dict[str, Any]]:
        """
        Получить метрики хендлера(ов)
        
        Args:
            handler_name: Имя конкретного хендлера (если None - все)
        
        Returns:
            Метрики хендлера или список метрик
        """
        if handler_name:
            metrics = self._handler_metrics.get(handler_name)
            return metrics.to_dict() if metrics else {}
        
        return [m.to_dict() for m in self._handler_metrics.values()]
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """Получить системные метрики"""
        try:
            cpu_percent = self._process.cpu_percent(interval=0.1)
            memory_info = self._process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024
            
            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_mb": round(memory_mb, 2),
                "threads": self._process.num_threads(),
                "uptime_seconds": round(time.time() - self._start_time, 2),
            }
        except Exception as e:
            logger.error("Failed to get system metrics: %s", e)
            return {}
    
    def get_summary(self) -> Dict[str, Any]:
        """Получить сводку всех метрик"""
        # Топ самых медленных хендлеров
        sorted_handlers = sorted(
            self._handler_metrics.values(),
            key=lambda m: m.average_duration,
            reverse=True
        )
        slowest = [m.to_dict() for m in sorted_handlers[:5]]
        
        # Топ с ошибками
        handlers_with_errors = [
            m.to_dict()
            for m in self._handler_metrics.values()
            if m.failed_calls > 0
        ]
        
        # Счетчики
        total_requests = sum(
            m.total_calls for m in self._handler_metrics.values()
        )
        total_errors = sum(
            m.failed_calls for m in self._handler_metrics.values()
        )
        
        return {
            "system": self.get_system_metrics(),
            "totals": {
                "requests": total_requests,
                "errors": total_errors,
                "handlers": len(self._handler_metrics),
            },
            "slowest_handlers": slowest,
            "handlers_with_errors": handlers_with_errors,
            "counters": dict(self._counters),
        }
    
    def reset(self) -> None:
        """Сбросить все метрики"""
        self._metrics_history.clear()
        self._handler_metrics.clear()
        self._counters.clear()
        self._start_time = time.time()
        logger.info("Metrics reset")


# ===== Глобальный экземпляр =====

_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Получить глобальный коллектор метрик"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def init_metrics_collector(*, max_history_points: int = 1000) -> MetricsCollector:
    """Инициализировать коллектор метрик"""
    global _metrics_collector
    _metrics_collector = MetricsCollector(max_history_points=max_history_points)
    return _metrics_collector


__all__ = [
    "MetricsCollector",
    "MetricPoint",
    "HandlerMetrics",
    "get_metrics_collector",
    "init_metrics_collector",
]
