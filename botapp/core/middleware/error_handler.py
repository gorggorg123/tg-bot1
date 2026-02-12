"""
Error handler middleware for centralized exception handling
"""
from __future__ import annotations

import logging
import traceback
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from aiogram.exceptions import TelegramAPIError as AiogramTelegramAPIError

from botapp.core.exceptions import (
    BotError,
    RateLimitError,
    PermissionError,
    ValidationError,
    APIError,
    OzonAPIError,
    TelegramAPIError,
)

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseMiddleware):
    """
    Middleware для централизованной обработки ошибок
    
    Преобразует технические ошибки в пользовательские сообщения
    Логирует все ошибки для мониторинга
    
    Example:
        middleware = ErrorHandlerMiddleware()
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    """
    
    def __init__(
        self,
        *,
        send_to_user: bool = True,
        notify_admin: bool = False,
        admin_chat_id: int | None = None,
    ):
        """
        Args:
            send_to_user: Send error message to user
            notify_admin: Notify admin about critical errors
            admin_chat_id: Admin chat ID for notifications
        """
        super().__init__()
        self.send_to_user = send_to_user
        self.notify_admin = notify_admin
        self.admin_chat_id = admin_chat_id
        logger.info(
            "ErrorHandlerMiddleware initialized: send_to_user=%s, notify_admin=%s",
            send_to_user,
            notify_admin,
        )
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Handle errors"""
        
        try:
            return await handler(event, data)
        
        except RateLimitError as e:
            # Rate limit errors are expected, don't spam logs
            logger.debug("Rate limit error: %s", e)
            # Message already sent in RateLimitMiddleware
            return None
        
        except PermissionError as e:
            logger.warning("Permission denied: %s", e, extra={"user_id": self._get_user_id(event)})
            if self.send_to_user:
                await self._send_error_message(event, e.user_message)
            return None
        
        except ValidationError as e:
            logger.warning(
                "Validation error: field=%s, value=%s, message=%s",
                e.field,
                e.value,
                e.message,
                extra={"user_id": self._get_user_id(event)},
            )
            if self.send_to_user:
                await self._send_error_message(event, e.user_message)
            return None
        
        except OzonAPIError as e:
            logger.error(
                "Ozon API error: %s (code=%s)",
                e.message,
                e.code,
                exc_info=True,
                extra={
                    "user_id": self._get_user_id(event),
                    "error_code": e.code,
                    "error_details": e.details,
                },
            )
            if self.send_to_user:
                await self._send_error_message(
                    event,
                    "❌ Ошибка при обращении к Ozon API. Попробуйте позже.",
                )
            if self.notify_admin:
                await self._notify_admin(event, e)
            return None
        
        except TelegramAPIError as e:
            logger.error(
                "Telegram API error: %s",
                e.message,
                exc_info=True,
                extra={"user_id": self._get_user_id(event)},
            )
            # Don't send message to user (Telegram error)
            return None
        
        except AiogramTelegramAPIError as e:
            logger.error(
                "Aiogram Telegram API error: %s",
                str(e),
                exc_info=True,
                extra={"user_id": self._get_user_id(event)},
            )
            # Don't send message to user (Telegram error)
            return None
        
        except APIError as e:
            logger.error(
                "API error: %s (code=%s)",
                e.message,
                e.code,
                exc_info=True,
                extra={
                    "user_id": self._get_user_id(event),
                    "error_code": e.code,
                    "error_details": e.details,
                },
            )
            if self.send_to_user:
                await self._send_error_message(event, e.user_message)
            return None
        
        except BotError as e:
            logger.error(
                "Bot error: %s (code=%s)",
                e.message,
                e.code,
                exc_info=True,
                extra={
                    "user_id": self._get_user_id(event),
                    "error_code": e.code,
                    "error_details": e.details,
                },
            )
            if self.send_to_user:
                await self._send_error_message(event, e.user_message)
            return None
        
        except Exception as e:
            # Unexpected error - critical!
            logger.critical(
                "Unexpected error: %s: %s\n%s",
                type(e).__name__,
                str(e),
                traceback.format_exc(),
                exc_info=True,
                extra={
                    "user_id": self._get_user_id(event),
                    "error_type": type(e).__name__,
                },
            )
            if self.send_to_user:
                await self._send_error_message(
                    event,
                    "❌ Произошла непредвиденная ошибка. Мы уже работаем над её исправлением.",
                )
            if self.notify_admin:
                await self._notify_admin(event, e)
            return None
    
    def _get_user_id(self, event: TelegramObject) -> int | None:
        """Extract user ID from event"""
        if isinstance(event, (Message, CallbackQuery)):
            return event.from_user.id if event.from_user else None
        return None
    
    async def _send_error_message(self, event: TelegramObject, message: str) -> None:
        """Send error message to user"""
        try:
            if isinstance(event, Message):
                await event.answer(message)
            elif isinstance(event, CallbackQuery):
                await event.answer(message, show_alert=True)
        except Exception:
            logger.exception("Failed to send error message to user")
    
    async def _notify_admin(self, event: TelegramObject, error: Exception) -> None:
        """Notify admin about critical error"""
        if not self.admin_chat_id:
            return
        
        try:
            bot = None
            if isinstance(event, Message):
                bot = event.bot
            elif isinstance(event, CallbackQuery):
                bot = event.bot
            
            if not bot:
                return
            
            user_id = self._get_user_id(event)
            error_text = (
                f"🚨 <b>Critical Error</b>\n\n"
                f"<b>User:</b> {user_id}\n"
                f"<b>Error:</b> {type(error).__name__}\n"
                f"<b>Message:</b> {str(error)[:500]}\n"
            )
            
            await bot.send_message(
                chat_id=self.admin_chat_id,
                text=error_text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to notify admin")
