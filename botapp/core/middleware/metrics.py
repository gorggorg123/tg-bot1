"""
Metrics middleware for collecting performance and usage statistics
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)


@dataclass
class Metrics:
    """Metrics container"""
    
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_response_time: float = 0.0
    requests_by_type: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    requests_by_user: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    errors_by_type: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    command_usage: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    callback_usage: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    last_reset: datetime = field(default_factory=datetime.utcnow)
    
    @property
    def avg_response_time(self) -> float:
        """Average response time in milliseconds"""
        if self.total_requests == 0:
            return 0.0
        return (self.total_response_time / self.total_requests) * 1000
    
    @property
    def success_rate(self) -> float:
        """Success rate as percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.successful_requests / self.total_requests) * 100
    
    @property
    def error_rate(self) -> float:
        """Error rate as percentage"""
        if self.total_requests == 0:
            return 0.0
        return (self.failed_requests / self.total_requests) * 100
    
    def reset(self) -> None:
        """Reset all metrics"""
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_response_time = 0.0
        self.requests_by_type.clear()
        self.requests_by_user.clear()
        self.errors_by_type.clear()
        self.command_usage.clear()
        self.callback_usage.clear()
        self.last_reset = datetime.utcnow()
    
    def summary(self) -> str:
        """Get metrics summary"""
        return (
            f"Total: {self.total_requests} "
            f"(✓{self.successful_requests} ✗{self.failed_requests}), "
            f"Avg response: {self.avg_response_time:.0f}ms, "
            f"Success rate: {self.success_rate:.1f}%"
        )


class MetricsMiddleware(BaseMiddleware):
    """
    Middleware для сбора метрик производительности и использования
    
    Собирает:
    - Общее количество запросов
    - Успешные/неуспешные запросы
    - Среднее время ответа
    - Популярные команды
    - Активные пользователи
    
    Example:
        middleware = MetricsMiddleware()
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
        
        # Get metrics
        print(middleware.get_metrics())
    """
    
    def __init__(self):
        super().__init__()
        self.metrics = Metrics()
        logger.info("MetricsMiddleware initialized")
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Collect metrics"""
        
        # Extract event info
        event_type = type(event).__name__
        user_id = None
        command = None
        callback_data = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            if event.text and event.text.startswith("/"):
                command = event.text.split()[0]
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            callback_data = event.data[:50] if event.data else None
        
        # Measure execution time
        start_time = time.time()
        error = None
        
        try:
            # Handle request
            result = await handler(event, data)
            self.metrics.successful_requests += 1
            return result
        
        except Exception as e:
            error = e
            self.metrics.failed_requests += 1
            self.metrics.errors_by_type[type(e).__name__] += 1
            raise
        
        finally:
            # Record metrics
            duration = time.time() - start_time
            
            self.metrics.total_requests += 1
            self.metrics.total_response_time += duration
            self.metrics.requests_by_type[event_type] += 1
            
            if user_id:
                self.metrics.requests_by_user[user_id] += 1
            
            if command:
                self.metrics.command_usage[command] += 1
            
            if callback_data:
                self.metrics.callback_usage[callback_data] += 1
            
            # Log slow requests
            duration_ms = int(duration * 1000)
            if duration_ms > 1000:  # > 1 second
                logger.warning(
                    "Slow request: %s took %dms (user=%s, command=%s, callback=%s)",
                    event_type,
                    duration_ms,
                    user_id,
                    command,
                    callback_data,
                    extra={
                        "event_type": event_type,
                        "duration_ms": duration_ms,
                        "user_id": user_id,
                        "command": command,
                        "callback_data": callback_data,
                        "error": bool(error),
                    },
                )
    
    def get_metrics(self) -> Metrics:
        """Get current metrics"""
        return self.metrics
    
    def get_summary(self) -> str:
        """Get metrics summary as string"""
        return self.metrics.summary()
    
    def reset_metrics(self) -> None:
        """Reset all metrics"""
        logger.info("Resetting metrics")
        self.metrics.reset()
    
    def get_prometheus_format(self) -> str:
        """
        Export metrics in Prometheus format
        
        Returns:
            Metrics in Prometheus text format
        """
        lines = [
            "# HELP bot_requests_total Total number of requests",
            "# TYPE bot_requests_total counter",
            f"bot_requests_total {self.metrics.total_requests}",
            "",
            "# HELP bot_requests_success Successful requests",
            "# TYPE bot_requests_success counter",
            f"bot_requests_success {self.metrics.successful_requests}",
            "",
            "# HELP bot_requests_failed Failed requests",
            "# TYPE bot_requests_failed counter",
            f"bot_requests_failed {self.metrics.failed_requests}",
            "",
            "# HELP bot_response_time_avg Average response time in milliseconds",
            "# TYPE bot_response_time_avg gauge",
            f"bot_response_time_avg {self.metrics.avg_response_time:.2f}",
            "",
            "# HELP bot_success_rate Success rate percentage",
            "# TYPE bot_success_rate gauge",
            f"bot_success_rate {self.metrics.success_rate:.2f}",
            "",
        ]
        
        # Commands
        if self.metrics.command_usage:
            lines.append("# HELP bot_command_usage Command usage count")
            lines.append("# TYPE bot_command_usage counter")
            for cmd, count in self.metrics.command_usage.items():
                lines.append(f'bot_command_usage{{command="{cmd}"}} {count}')
            lines.append("")
        
        # Errors
        if self.metrics.errors_by_type:
            lines.append("# HELP bot_errors Errors by type")
            lines.append("# TYPE bot_errors counter")
            for error_type, count in self.metrics.errors_by_type.items():
                lines.append(f'bot_errors{{type="{error_type}"}} {count}')
            lines.append("")
        
        return "\n".join(lines)
