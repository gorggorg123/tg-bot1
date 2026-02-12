"""
Authentication and authorization middleware
Supports role-based access control (RBAC)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Set

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from botapp.core.exceptions import PermissionError

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """User roles"""
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"
    GUEST = "guest"


class Permission(str, Enum):
    """Permissions"""
    # Admin permissions
    VIEW_LOGS = "view_logs"
    MANAGE_USERS = "manage_users"
    MANAGE_SETTINGS = "manage_settings"
    
    # Moderator permissions
    VIEW_ANALYTICS = "view_analytics"
    MANAGE_REVIEWS = "manage_reviews"
    MANAGE_QUESTIONS = "manage_questions"
    
    # User permissions
    VIEW_ORDERS = "view_orders"
    VIEW_FINANCE = "view_finance"
    VIEW_CHATS = "view_chats"
    SEND_MESSAGES = "send_messages"


# Role to permissions mapping
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        Permission.VIEW_LOGS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SETTINGS,
        Permission.VIEW_ANALYTICS,
        Permission.MANAGE_REVIEWS,
        Permission.MANAGE_QUESTIONS,
        Permission.VIEW_ORDERS,
        Permission.VIEW_FINANCE,
        Permission.VIEW_CHATS,
        Permission.SEND_MESSAGES,
    },
    Role.MODERATOR: {
        Permission.VIEW_ANALYTICS,
        Permission.MANAGE_REVIEWS,
        Permission.MANAGE_QUESTIONS,
        Permission.VIEW_ORDERS,
        Permission.VIEW_FINANCE,
        Permission.VIEW_CHATS,
        Permission.SEND_MESSAGES,
    },
    Role.USER: {
        Permission.VIEW_ORDERS,
        Permission.VIEW_FINANCE,
        Permission.VIEW_CHATS,
        Permission.SEND_MESSAGES,
    },
    Role.GUEST: set(),
}


class AuthMiddleware(BaseMiddleware):
    """
    Middleware для аутентификации и авторизации
    
    Поддерживает:
    - Role-based access control (RBAC)
    - Permission checks
    - User blocking
    
    Example:
        # Define admins
        middleware = AuthMiddleware(
            admins=[123456789],
            moderators=[987654321],
        )
        dp.message.middleware(middleware)
        
        # In handler, check permission
        from botapp.core.middleware.auth import require_permission, Permission
        
        @router.message(Command("analytics"))
        @require_permission(Permission.VIEW_ANALYTICS)
        async def analytics(message: Message):
            ...
    """
    
    def __init__(
        self,
        *,
        admins: list[int] | None = None,
        moderators: list[int] | None = None,
        blocked_users: list[int] | None = None,
        default_role: Role = Role.USER,
    ):
        """
        Args:
            admins: List of admin user IDs
            moderators: List of moderator user IDs
            blocked_users: List of blocked user IDs
            default_role: Default role for unknown users
        """
        super().__init__()
        self.admins = set(admins or [])
        self.moderators = set(moderators or [])
        self.blocked_users = set(blocked_users or [])
        self.default_role = default_role
        logger.info(
            "AuthMiddleware initialized: admins=%d, moderators=%d, blocked=%d, default_role=%s",
            len(self.admins),
            len(self.moderators),
            len(self.blocked_users),
            default_role.value,
        )
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Authenticate and authorize user"""
        
        # Extract user_id
        user_id = None
        if isinstance(event, (Message, CallbackQuery)):
            user_id = event.from_user.id if event.from_user else None
        
        if not user_id:
            # No user_id, deny
            logger.warning("Request without user_id")
            raise PermissionError("Authentication required")
        
        # Check if blocked
        if user_id in self.blocked_users:
            logger.warning("Blocked user %s tried to access", user_id)
            raise PermissionError(f"User {user_id} is blocked")
        
        # Determine role
        role = self._get_user_role(user_id)
        
        # Get permissions
        permissions = ROLE_PERMISSIONS.get(role, set())
        
        # Add to data for use in handlers
        data["user_role"] = role
        data["user_permissions"] = permissions
        data["user_id"] = user_id
        
        logger.debug(
            "User %s authenticated: role=%s, permissions=%d",
            user_id,
            role.value,
            len(permissions),
        )
        
        return await handler(event, data)
    
    def _get_user_role(self, user_id: int) -> Role:
        """Determine user role"""
        if user_id in self.admins:
            return Role.ADMIN
        if user_id in self.moderators:
            return Role.MODERATOR
        return self.default_role
    
    def has_permission(
        self,
        user_id: int,
        permission: Permission,
    ) -> bool:
        """Check if user has permission"""
        role = self._get_user_role(user_id)
        permissions = ROLE_PERMISSIONS.get(role, set())
        return permission in permissions
    
    def block_user(self, user_id: int) -> None:
        """Block user"""
        self.blocked_users.add(user_id)
        logger.info("User %s blocked", user_id)
    
    def unblock_user(self, user_id: int) -> None:
        """Unblock user"""
        self.blocked_users.discard(user_id)
        logger.info("User %s unblocked", user_id)


# Decorator for permission checks in handlers
def require_permission(permission: Permission):
    """
    Decorator to require permission for handler
    
    Example:
        @router.message(Command("analytics"))
        @require_permission(Permission.VIEW_ANALYTICS)
        async def analytics(message: Message):
            ...
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs):
            # Find message/callback in args
            event = None
            for arg in args:
                if isinstance(arg, (Message, CallbackQuery)):
                    event = arg
                    break
            
            if not event:
                raise PermissionError("No event found")
            
            # Check permission (should be set by middleware)
            user_permissions = kwargs.get("user_permissions", set())
            if permission not in user_permissions:
                user_id = event.from_user.id if event.from_user else None
                logger.warning(
                    "User %s tried to access %s without permission %s",
                    user_id,
                    func.__name__,
                    permission.value,
                )
                raise PermissionError(f"Permission {permission.value} required")
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator
