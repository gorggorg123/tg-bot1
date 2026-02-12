"""
Custom exceptions for better error handling and debugging
"""
from __future__ import annotations

from typing import Any, Optional


class BotError(Exception):
    """Base exception for all bot errors"""
    
    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
        user_message: Optional[str] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or self.__class__.__name__
        self.details = details or {}
        self.user_message = user_message or "Произошла ошибка. Попробуйте позже."


class APIError(BotError):
    """External API errors (Ozon, Telegram, etc.)"""
    pass


class OzonAPIError(APIError):
    """Ozon API specific errors"""
    pass


class TelegramAPIError(APIError):
    """Telegram API specific errors"""
    pass


class ValidationError(BotError):
    """Input validation errors"""
    
    def __init__(
        self,
        message: str,
        *,
        field: Optional[str] = None,
        value: Any = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.field = field
        self.value = value
        self.user_message = f"Неверные данные: {message}"


class PermissionError(BotError):
    """Permission/authorization errors"""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, **kwargs)
        self.user_message = "У вас нет прав для выполнения этого действия."


class RateLimitError(BotError):
    """Rate limiting errors"""
    
    def __init__(
        self,
        message: str,
        *,
        retry_after: Optional[int] = None,
        **kwargs,
    ):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
        self.user_message = f"Слишком много запросов. Попробуйте через {retry_after or 60} секунд."


class ConfigurationError(BotError):
    """Configuration/setup errors"""
    pass


class DatabaseError(BotError):
    """Database operation errors"""
    pass


class CacheError(BotError):
    """Cache operation errors"""
    pass


class ServiceError(BotError):
    """Business logic errors"""
    pass
