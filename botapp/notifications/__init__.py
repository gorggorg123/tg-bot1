# botapp/notifications/__init__.py
"""
Система уведомлений для Ozon Seller Bot.

Модули:
- config: Настройки уведомлений для пользователей
- checker: Фоновые задачи проверки новых данных
- sender: Отправка уведомлений в Telegram
- handlers: Telegram обработчики для настройки

Использование:
    from botapp.notifications import (
        start_notification_checker,
        stop_notification_checker,
        set_bot,
        router,
    )
    
    # При запуске бота
    set_bot(bot)
    start_notification_checker()
    dp.include_router(router)
    
    # При остановке бота
    stop_notification_checker()
"""

from .config import (
    NotificationSettings,
    get_user_settings,
    update_user_settings,
    is_notifications_enabled,
    get_all_enabled_users,
)
from .checker import (
    start_notification_checker,
    stop_notification_checker,
    is_checker_running,
)
from .sender import (
    send_notification,
    send_batch_notification,
    NotificationType,
    set_bot,
    get_bot,
)
from .handlers import router

__all__ = [
    # Config
    "NotificationSettings",
    "get_user_settings",
    "update_user_settings",
    "is_notifications_enabled",
    "get_all_enabled_users",
    # Checker
    "start_notification_checker",
    "stop_notification_checker",
    "is_checker_running",
    # Sender
    "send_notification",
    "send_batch_notification",
    "NotificationType",
    "set_bot",
    "get_bot",
    # Router
    "router",
]
