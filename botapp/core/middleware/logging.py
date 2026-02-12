"""
Logging middleware for structured logging of all bot interactions
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """
    Middleware для структурированного логирования всех взаимодействий
    
    Логирует:
    - Все входящие сообщения и callback queries
    - Время обработки
    - Ошибки
    - Результаты
    
    Example:
        middleware = LoggingMiddleware()
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    """
    
    def __init__(self, *, log_text: bool = False, log_data: bool = False):
        """
        Args:
            log_text: Whether to log full message text (may contain sensitive info)
            log_data: Whether to log callback data
        """
        super().__init__()
        self.log_text = log_text
        self.log_data = log_data
        logger.info(
            "LoggingMiddleware initialized: log_text=%s, log_data=%s",
            log_text,
            log_data,
        )
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Log request and response"""
        
        # Generate request ID for tracing
        request_id = str(uuid.uuid4())[:8]
        data["request_id"] = request_id
        
        # Extract event info
        event_type = type(event).__name__
        user_id = None
        user_name = None
        event_info = {}
        
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            user_name = (
                event.from_user.username or event.from_user.full_name
                if event.from_user
                else None
            )
            event_info = {
                "message_id": event.message_id,
                "chat_id": event.chat.id,
                "command": event.text.split()[0] if event.text and event.text.startswith("/") else None,
                "has_text": bool(event.text),
                "has_photo": bool(event.photo),
                "has_document": bool(event.document),
            }
            if self.log_text and event.text:
                event_info["text_preview"] = event.text[:100]
        
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            user_name = (
                event.from_user.username or event.from_user.full_name
                if event.from_user
                else None
            )
            event_info = {
                "callback_id": event.id,
                "message_id": event.message.message_id if event.message else None,
                "chat_id": event.message.chat.id if event.message else None,
            }
            if self.log_data and event.data:
                event_info["data_preview"] = event.data[:100]
        
        # Log incoming request
        logger.info(
            "[%s] Incoming %s from user=%s (%s): %s",
            request_id,
            event_type,
            user_id,
            user_name,
            event_info,
            extra={
                "request_id": request_id,
                "event_type": event_type,
                "user_id": user_id,
                "user_name": user_name,
                **event_info,
            },
        )
        
        # Measure execution time
        start_time = time.time()
        error = None
        
        try:
            # Handle request
            result = await handler(event, data)
            return result
        
        except Exception as e:
            error = e
            logger.error(
                "[%s] Error processing %s from user=%s: %s: %s",
                request_id,
                event_type,
                user_id,
                type(e).__name__,
                str(e),
                exc_info=True,
                extra={
                    "request_id": request_id,
                    "event_type": event_type,
                    "user_id": user_id,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
            )
            raise
        
        finally:
            # Log completion
            duration_ms = int((time.time() - start_time) * 1000)
            status = "error" if error else "success"
            
            log_func = logger.error if error else logger.info
            log_func(
                "[%s] Completed %s in %dms: status=%s, user=%s",
                request_id,
                event_type,
                duration_ms,
                status,
                user_id,
                extra={
                    "request_id": request_id,
                    "event_type": event_type,
                    "user_id": user_id,
                    "duration_ms": duration_ms,
                    "status": status,
                },
            )
