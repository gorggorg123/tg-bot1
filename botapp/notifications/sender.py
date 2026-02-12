# botapp/notifications/sender.py
"""
Отправка уведомлений в Telegram.

Типы уведомлений:
- Новые отзывы
- Новые вопросы
- Новые сообщения в чатах
- Новые заказы FBO/FBS
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import get_user_settings, update_user_settings, is_in_quiet_hours

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Типы уведомлений."""
    NEW_REVIEW = "new_review"
    NEW_QUESTION = "new_question"
    NEW_CHAT_MESSAGE = "new_chat_message"
    NEW_ORDER_FBO = "new_order_fbo"
    NEW_ORDER_FBS = "new_order_fbs"
    SYSTEM = "system"


@dataclass
class NotificationData:
    """Данные для уведомления."""
    type: NotificationType
    title: str
    body: str
    data: dict  # Дополнительные данные (ID, ссылки и т.д.)
    
    # Опциональные поля
    product_name: Optional[str] = None
    rating: Optional[int] = None
    order_amount: Optional[float] = None


# Глобальная ссылка на бота (устанавливается при запуске)
_bot: Optional[Bot] = None


def set_bot(bot: Bot) -> None:
    """Установить экземпляр бота для отправки уведомлений."""
    global _bot
    _bot = bot
    logger.info("Notification sender: bot instance set")


def get_bot() -> Optional[Bot]:
    """Получить экземпляр бота."""
    return _bot


def _format_review_notification(data: NotificationData) -> tuple[str, InlineKeyboardMarkup | None]:
    """Форматирует уведомление о новом отзыве."""
    rating = data.rating or 0
    stars = "⭐" * rating if rating > 0 else "без оценки"
    
    text = (
        f"📝 <b>Новый отзыв!</b>\n\n"
        f"🏷 {data.product_name or 'Товар'}\n"
        f"⭐ Оценка: {stars}\n\n"
        f"{data.body[:200]}{'...' if len(data.body) > 200 else ''}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Открыть отзывы",
            callback_data="menu:reviews:open:"
        )]
    ])
    
    return text, keyboard


def _format_question_notification(data: NotificationData) -> tuple[str, InlineKeyboardMarkup | None]:
    """Форматирует уведомление о новом вопросе."""
    text = (
        f"❓ <b>Новый вопрос!</b>\n\n"
        f"🏷 {data.product_name or 'Товар'}\n\n"
        f"💬 {data.body[:200]}{'...' if len(data.body) > 200 else ''}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❓ Открыть вопросы",
            callback_data="menu:questions:open:"
        )]
    ])
    
    return text, keyboard


def _format_chat_notification(data: NotificationData) -> tuple[str, InlineKeyboardMarkup | None]:
    """Форматирует уведомление о новом сообщении в чате."""
    text = (
        f"💬 <b>Новое сообщение!</b>\n\n"
        f"👤 Покупатель написал:\n"
        f"«{data.body[:200]}{'...' if len(data.body) > 200 else ''}»"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Открыть чаты",
            callback_data="menu:chats:open:"
        )]
    ])
    
    return text, keyboard


def _format_order_notification(data: NotificationData, order_type: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Форматирует уведомление о новом заказе."""
    icon = "📦" if order_type == "FBO" else "🏭"
    amount_str = "—"
    if data.order_amount is not None:
        try:
            amount = float(data.order_amount)
            amount_str = f"{amount:,.0f} ₽".replace(",", " ")
        except (ValueError, TypeError):
            pass
    
    posting_number = "—"
    if isinstance(data.data, dict):
        posting_number = str(data.data.get("posting_number") or "—")
    
    text = (
        f"{icon} <b>Новый заказ {order_type}!</b>\n\n"
        f"🏷 {data.product_name or 'Товар'}\n"
        f"💰 Сумма: {amount_str}\n"
        f"📋 {posting_number}"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📦 Открыть отправки",
            callback_data="menu:fbo:summary:"
        )]
    ])
    
    return text, keyboard


def _format_notification(data: NotificationData) -> tuple[str, InlineKeyboardMarkup | None]:
    """Форматирует уведомление в зависимости от типа."""
    if data.type == NotificationType.NEW_REVIEW:
        return _format_review_notification(data)
    elif data.type == NotificationType.NEW_QUESTION:
        return _format_question_notification(data)
    elif data.type == NotificationType.NEW_CHAT_MESSAGE:
        return _format_chat_notification(data)
    elif data.type == NotificationType.NEW_ORDER_FBO:
        return _format_order_notification(data, "FBO")
    elif data.type == NotificationType.NEW_ORDER_FBS:
        return _format_order_notification(data, "FBS")
    else:
        # Системное уведомление
        return data.body, None


async def send_notification(
    user_id: int,
    notification_type: NotificationType,
    title: str,
    body: str,
    data: Optional[dict] = None,
    **kwargs
) -> bool:
    """
    Отправить уведомление пользователю.
    
    Args:
        user_id: ID пользователя Telegram
        notification_type: Тип уведомления
        title: Заголовок
        body: Текст уведомления
        data: Дополнительные данные
        **kwargs: product_name, rating, order_amount и т.д.
    
    Returns:
        True если уведомление отправлено успешно
    """
    if _bot is None:
        logger.warning("Cannot send notification: bot not set")
        return False
    
    # Проверяем настройки пользователя
    settings = get_user_settings(user_id)
    
    if not settings.enabled:
        logger.debug("Notifications disabled for user %d", user_id)
        return False
    
    # Проверяем тихие часы
    if is_in_quiet_hours(user_id):
        logger.debug("Quiet hours for user %d, skipping notification", user_id)
        return False
    
    # Проверяем конкретный тип уведомления
    type_checks = {
        NotificationType.NEW_REVIEW: settings.reviews_enabled,
        NotificationType.NEW_QUESTION: settings.questions_enabled,
        NotificationType.NEW_CHAT_MESSAGE: settings.chats_enabled,
        NotificationType.NEW_ORDER_FBO: settings.orders_fbo_enabled,
        NotificationType.NEW_ORDER_FBS: settings.orders_fbs_enabled,
        NotificationType.SYSTEM: True,
    }
    
    if not type_checks.get(notification_type, True):
        logger.debug("Notification type %s disabled for user %d", notification_type.value, user_id)
        return False
    
    # Формируем уведомление
    notification_data = NotificationData(
        type=notification_type,
        title=title,
        body=body,
        data=data or {},
        **kwargs
    )
    
    text, keyboard = _format_notification(notification_data)
    
    try:
        await _bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        
        # Обновляем статистику
        update_user_settings(
            user_id,
            total_notifications_sent=settings.total_notifications_sent + 1,
            last_notification_at=datetime.now().isoformat()
        )
        
        logger.info(
            "Notification sent: type=%s user=%d",
            notification_type.value, user_id
        )
        return True
        
    except Exception as e:
        logger.error(
            "Failed to send notification: type=%s user=%d error=%s",
            notification_type.value, user_id, e
        )
        return False


async def send_batch_notification(
    user_id: int,
    notification_type: NotificationType,
    items: list[dict],
    max_items: int = 5
) -> bool:
    """
    Отправить групповое уведомление (например, о нескольких новых отзывах).
    
    Args:
        user_id: ID пользователя
        notification_type: Тип уведомлений
        items: Список элементов
        max_items: Максимум элементов для показа
    """
    if not items or not isinstance(items, list):
        return False
    
    # Фильтруем только валидные элементы
    valid_items = [item for item in items if isinstance(item, dict)]
    if not valid_items:
        return False
    
    count = len(valid_items)
    
    if notification_type == NotificationType.NEW_REVIEW:
        icon = "📝"
        name = "отзыв" if count == 1 else ("отзыва" if 2 <= count <= 4 else "отзывов")
        callback = "menu:reviews:open:"
    elif notification_type == NotificationType.NEW_QUESTION:
        icon = "❓"
        name = "вопрос" if count == 1 else ("вопроса" if 2 <= count <= 4 else "вопросов")
        callback = "menu:questions:open:"
    elif notification_type == NotificationType.NEW_CHAT_MESSAGE:
        icon = "💬"
        name = "сообщение" if count == 1 else ("сообщения" if 2 <= count <= 4 else "сообщений")
        callback = "menu:chats:open:"
    elif notification_type in (NotificationType.NEW_ORDER_FBO, NotificationType.NEW_ORDER_FBS):
        icon = "📦"
        name = "заказ" if count == 1 else ("заказа" if 2 <= count <= 4 else "заказов")
        callback = "menu:fbo:summary:"
    else:
        return False
    
    # Формируем текст
    lines = [f"{icon} <b>Новых {name}: {count}</b>\n"]
    
    for item in valid_items[:max_items]:
        if not isinstance(item, dict):
            continue
        
        product = str(item.get("product_name") or "Товар")[:30]
        if notification_type == NotificationType.NEW_REVIEW:
            rating = item.get("rating") or 0
            try:
                rating = int(rating) if rating else 0
            except (ValueError, TypeError):
                rating = 0
            lines.append(f"• {'⭐' * rating if rating > 0 else 'без оценки'} {product}")
        elif notification_type == NotificationType.NEW_QUESTION:
            lines.append(f"• ❓ {product}")
        elif notification_type == NotificationType.NEW_CHAT_MESSAGE:
            preview = str(item.get("text") or "")[:30]
            lines.append(f"• 💬 {preview}...")
        else:
            amount = item.get("amount") or 0
            try:
                amount = float(amount) if amount else 0.0
            except (ValueError, TypeError):
                amount = 0.0
            lines.append(f"• {product} — {amount:,.0f} ₽".replace(",", " "))
    
    if count > max_items:
        lines.append(f"\n... и ещё {count - max_items}")
    
    text = "\n".join(lines)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Посмотреть", callback_data=callback)]
    ])
    
    if _bot is None:
        return False
    
    try:
        await _bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return True
    except Exception as e:
        logger.error("Failed to send batch notification: %s", e)
        return False


__all__ = [
    "NotificationType",
    "NotificationData",
    "set_bot",
    "get_bot",
    "send_notification",
    "send_batch_notification",
]
