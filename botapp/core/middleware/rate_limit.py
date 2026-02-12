"""
Rate limiting middleware to prevent abuse and protect against floods
Supports per-user and per-command rate limiting
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from botapp.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket rate limiter"""
    
    def __init__(self, rate: int = 10, per: int = 60):
        """
        Args:
            rate: Number of allowed requests
            per: Time period in seconds
        """
        self.rate = rate
        self.per = per
        self.allowance: Dict[int, float] = defaultdict(lambda: rate)
        self.last_check: Dict[int, float] = defaultdict(lambda: time.time())
    
    def is_allowed(self, user_id: int) -> tuple[bool, int | None]:
        """
        Check if request is allowed for user
        
        Returns:
            tuple: (is_allowed, retry_after_seconds)
        """
        current = time.time()
        time_passed = current - self.last_check[user_id]
        self.last_check[user_id] = current
        
        # Replenish tokens
        self.allowance[user_id] += time_passed * (self.rate / self.per)
        if self.allowance[user_id] > self.rate:
            self.allowance[user_id] = self.rate
        
        # Check if request is allowed
        if self.allowance[user_id] < 1.0:
            retry_after = int((1.0 - self.allowance[user_id]) * (self.per / self.rate))
            return False, retry_after
        
        self.allowance[user_id] -= 1.0
        return True, None
    
    def reset(self, user_id: int) -> None:
        """Reset rate limit for user"""
        self.allowance.pop(user_id, None)
        self.last_check.pop(user_id, None)


class RateLimitMiddleware(BaseMiddleware):
    """
    Middleware для ограничения частоты запросов
    
    Example:
        # 10 requests per minute per user
        middleware = RateLimitMiddleware(rate=10, per=60)
        dp.message.middleware(middleware)
        dp.callback_query.middleware(middleware)
    """
    
    def __init__(
        self,
        *,
        rate: int = 20,
        per: int = 60,
        exclude_commands: list[str] | None = None,
        exclude_users: list[int] | None = None,
    ):
        """
        Args:
            rate: Number of allowed requests
            per: Time period in seconds
            exclude_commands: Commands to exclude from rate limiting (e.g., ["/start"])
            exclude_users: User IDs to exclude (e.g., admins)
        """
        super().__init__()
        self.limiter = RateLimiter(rate=rate, per=per)
        self.exclude_commands = set(exclude_commands or [])
        self.exclude_users = set(exclude_users or [])
        logger.info(
            "RateLimitMiddleware initialized: rate=%s, per=%ss, exclude_commands=%s, exclude_users=%s",
            rate,
            per,
            self.exclude_commands,
            self.exclude_users,
        )
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process rate limiting"""
        
        # Extract user_id
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            command = event.text.split()[0] if event.text else None
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id if event.from_user else None
            command = None
        else:
            # Unknown event type, allow it
            return await handler(event, data)
        
        if not user_id:
            # No user_id, allow it
            return await handler(event, data)
        
        # Check exclusions
        if user_id in self.exclude_users:
            logger.debug("User %s excluded from rate limiting", user_id)
            return await handler(event, data)
        
        if command and command in self.exclude_commands:
            logger.debug("Command %s excluded from rate limiting", command)
            return await handler(event, data)
        
        # Check rate limit
        is_allowed, retry_after = self.limiter.is_allowed(user_id)
        
        if not is_allowed:
            logger.warning(
                "Rate limit exceeded for user %s (retry after %ss)",
                user_id,
                retry_after,
            )
            
            # Send user-friendly message
            if isinstance(event, Message):
                await event.answer(
                    f"⏱ Слишком много запросов. Пожалуйста, подождите {retry_after} секунд.",
                    show_alert=False if isinstance(event, CallbackQuery) else None,
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    f"⏱ Слишком много запросов. Подождите {retry_after}с.",
                    show_alert=True,
                )
            
            raise RateLimitError(
                f"Rate limit exceeded for user {user_id}",
                retry_after=retry_after,
            )
        
        # Allow request
        logger.debug("Rate limit check passed for user %s", user_id)
        return await handler(event, data)
    
    def reset_user(self, user_id: int) -> None:
        """Manually reset rate limit for user (e.g., after payment)"""
        self.limiter.reset(user_id)
        logger.info("Rate limit reset for user %s", user_id)
