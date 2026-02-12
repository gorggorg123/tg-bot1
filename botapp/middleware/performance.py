"""
Performance Monitoring Middleware
=================================
Middleware для мониторинга производительности всех хендлеров

Автоматически:
- Замеряет время выполнения
- Логирует медленные операции
- Собирает метрики
- Отправляет в систему мониторинга
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Update, TelegramObject

from botapp.monitoring import get_metrics_collector, get_live_logger

logger = logging.getLogger(__name__)


class PerformanceMiddleware(BaseMiddleware):
    """
    Middleware для мониторинга производительности хендлеров
    
    Использование:
        from botapp.middleware.performance import PerformanceMiddleware
        
        dp.update.middleware(PerformanceMiddleware())
    """
    
    def __init__(
        self,
        *,
        slow_threshold_seconds: float = 1.0,
        enable_metrics: bool = True,
        enable_logging: bool = True,
    ):
        """
        Args:
            slow_threshold_seconds: Порог "медленной" операции (сек)
            enable_metrics: Включить сбор метрик
            enable_logging: Включить логирование
        """
        super().__init__()
        self.slow_threshold = slow_threshold_seconds
        self.enable_metrics = enable_metrics
        self.enable_logging = enable_logging
        
        # Получаем коллекторы
        if enable_metrics:
            self.metrics = get_metrics_collector()
        if enable_logging:
            self.live_logger = get_live_logger()
        
        logger.info(
            "PerformanceMiddleware initialized: slow_threshold=%.2fs, metrics=%s, logging=%s",
            slow_threshold_seconds, enable_metrics, enable_logging
        )
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Обработка события с замером времени"""
        # Получение информации о handler
        handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
        
        # Извлечение данных о пользователе/чате (если есть)
        user_id = None
        chat_id = None
        
        if isinstance(event, Update):
            if event.message:
                user_id = event.message.from_user.id if event.message.from_user else None
                chat_id = event.message.chat.id
            elif event.callback_query:
                user_id = event.callback_query.from_user.id
                chat_id = event.callback_query.message.chat.id if event.callback_query.message else None
        
        # Замер времени
        start_time = time.time()
        error = None
        success = True
        
        try:
            result = await handler(event, data)
            return result
            
        except Exception as e:
            success = False
            error = type(e).__name__
            logger.error(
                "Handler %s failed for user=%s chat=%s: %s",
                handler_name, user_id, chat_id, e,
                exc_info=True
            )
            raise
            
        finally:
            # Время выполнения
            duration = time.time() - start_time
            duration_ms = duration * 1000
            
            # Логирование медленных операций
            if duration >= self.slow_threshold:
                logger.warning(
                    "⚠️ SLOW HANDLER: %s took %.2fs (user=%s, chat=%s)",
                    handler_name, duration, user_id, chat_id
                )
            
            # Сбор метрик
            if self.enable_metrics:
                self.metrics.record_handler_call(
                    handler_name=handler_name,
                    duration=duration,
                    success=success,
                    error=error,
                )
            
            # Live logging
            if self.enable_logging:
                level = "WARNING" if duration >= self.slow_threshold or error else "INFO"
                
                self.live_logger.log_event(
                    message=f"Handler {handler_name} executed",
                    level=level,
                    source="bot",
                    handler=handler_name,
                    user_id=user_id,
                    chat_id=chat_id,
                    duration_ms=duration_ms,
                    error=error,
                    data={
                        "success": success,
                        "slow": duration >= self.slow_threshold,
                    }
                )


class ErrorTrackingMiddleware(BaseMiddleware):
    """Middleware для отслеживания и логирования ошибок"""
    
    def __init__(self, *, enable_notifications: bool = False):
        """
        Args:
            enable_notifications: Включить уведомления об ошибках
        """
        super().__init__()
        self.enable_notifications = enable_notifications
        self.live_logger = get_live_logger()
        
        logger.info("ErrorTrackingMiddleware initialized")
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Обработка с отслеживанием ошибок"""
        try:
            return await handler(event, data)
        except Exception as e:
            # Логирование ошибки
            handler_name = handler.__name__ if hasattr(handler, '__name__') else str(handler)
            
            error_details = {
                "handler": handler_name,
                "error_type": type(e).__name__,
                "error_message": str(e),
            }
            
            # Извлечение user_id/chat_id
            user_id = None
            chat_id = None
            if isinstance(event, Update):
                if event.message:
                    user_id = event.message.from_user.id if event.message.from_user else None
                    chat_id = event.message.chat.id
                elif event.callback_query:
                    user_id = event.callback_query.from_user.id
                    chat_id = event.callback_query.message.chat.id if event.callback_query.message else None
            
            # Live logging
            self.live_logger.log_event(
                message=f"Error in {handler_name}: {e}",
                level="ERROR",
                source="bot",
                handler=handler_name,
                user_id=user_id,
                chat_id=chat_id,
                error=str(e),
                data=error_details,
            )
            
            # TODO: Отправка уведомления админам
            if self.enable_notifications:
                pass  # Implement admin notification
            
            # Пробрасываем исключение дальше
            raise


__all__ = [
    "PerformanceMiddleware",
    "ErrorTrackingMiddleware",
]
