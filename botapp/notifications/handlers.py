# botapp/notifications/handlers.py
"""
Telegram обработчики для управления уведомлениями.
"""

from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters.callback_data import CallbackData

from .config import get_user_settings, update_user_settings
from .checker import is_checker_running

logger = logging.getLogger(__name__)

router = Router(name="notifications")


class NotifyCallbackData(CallbackData, prefix="notify"):
    """Callback data для настроек уведомлений."""
    action: str
    value: str = ""


def _build_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру настроек уведомлений."""
    settings = get_user_settings(user_id)
    
    def _toggle_btn(text: str, enabled: bool, action: str) -> InlineKeyboardButton:
        icon = "✅" if enabled else "❌"
        return InlineKeyboardButton(
            text=f"{icon} {text}",
            callback_data=NotifyCallbackData(action=action, value="toggle").pack()
        )
    
    keyboard = [
        # Главный переключатель
        [InlineKeyboardButton(
            text=f"{'🔔 Уведомления ВКЛ' if settings.enabled else '🔕 Уведомления ВЫКЛ'}",
            callback_data=NotifyCallbackData(action="main", value="toggle").pack()
        )],
        
        # Типы уведомлений
        [_toggle_btn("Отзывы", settings.reviews_enabled, "reviews")],
        [_toggle_btn("Вопросы", settings.questions_enabled, "questions")],
        [_toggle_btn("Чаты", settings.chats_enabled, "chats")],
        [_toggle_btn("Заказы FBO", settings.orders_fbo_enabled, "orders_fbo")],
        [_toggle_btn("Заказы FBS", settings.orders_fbs_enabled, "orders_fbs")],
        
        # Тихие часы
        [InlineKeyboardButton(
            text=f"{'🌙 Тихие часы ВКЛ' if settings.quiet_hours_enabled else '☀️ Тихие часы ВЫКЛ'} ({settings.quiet_hours_start}:00-{settings.quiet_hours_end}:00)",
            callback_data=NotifyCallbackData(action="quiet", value="toggle").pack()
        )],
        
        # Назад
        [InlineKeyboardButton(
            text="⬅️ В главное меню",
            callback_data="menu:home:open:"
        )],
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _format_settings_text(user_id: int) -> str:
    """Форматирует текст настроек."""
    settings = get_user_settings(user_id)
    
    status = "🔔 Включены" if settings.enabled else "🔕 Выключены"
    checker_status = "✅ Работает" if is_checker_running() else "⏸ Остановлен"
    
    lines = [
        "⚙️ <b>Настройки уведомлений</b>",
        "",
        f"Статус: {status}",
        f"Сервис: {checker_status}",
        "",
        "<b>Типы уведомлений:</b>",
        f"• Отзывы: {'✅' if settings.reviews_enabled else '❌'}",
        f"• Вопросы: {'✅' if settings.questions_enabled else '❌'}",
        f"• Чаты: {'✅' if settings.chats_enabled else '❌'}",
        f"• Заказы FBO: {'✅' if settings.orders_fbo_enabled else '❌'}",
        f"• Заказы FBS: {'✅' if settings.orders_fbs_enabled else '❌'}",
        "",
        f"<b>Тихие часы:</b> {'🌙 ' + str(settings.quiet_hours_start) + ':00-' + str(settings.quiet_hours_end) + ':00' if settings.quiet_hours_enabled else '☀️ Выключены'}",
        "",
        f"📊 Отправлено уведомлений: {settings.total_notifications_sent}",
    ]
    
    return "\n".join(lines)


@router.message(Command("notifications", "notify", "уведомления"))
async def cmd_notifications(message: Message) -> None:
    """Команда /notifications — настройки уведомлений."""
    user_id = message.from_user.id
    logger.info("Opening notifications settings for user_id=%d", user_id)
    
    text = _format_settings_text(user_id)
    keyboard = _build_settings_keyboard(user_id)
    
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@router.callback_query(NotifyCallbackData.filter())
async def handle_notify_callback(callback: CallbackQuery, callback_data: NotifyCallbackData) -> None:
    """Обработчик callback-ов настроек уведомлений."""
    user_id = callback.from_user.id
    action = callback_data.action
    value = callback_data.value
    
    settings = get_user_settings(user_id)
    
    if value == "toggle":
        # Переключение настройки
        if action == "main":
            update_user_settings(user_id, enabled=not settings.enabled)
        elif action == "reviews":
            update_user_settings(user_id, reviews_enabled=not settings.reviews_enabled)
        elif action == "questions":
            update_user_settings(user_id, questions_enabled=not settings.questions_enabled)
        elif action == "chats":
            update_user_settings(user_id, chats_enabled=not settings.chats_enabled)
        elif action == "orders_fbo":
            update_user_settings(user_id, orders_fbo_enabled=not settings.orders_fbo_enabled)
        elif action == "orders_fbs":
            update_user_settings(user_id, orders_fbs_enabled=not settings.orders_fbs_enabled)
        elif action == "quiet":
            update_user_settings(user_id, quiet_hours_enabled=not settings.quiet_hours_enabled)
    
    # Обновляем сообщение
    text = _format_settings_text(user_id)
    keyboard = _build_settings_keyboard(user_id)
    
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        pass  # Сообщение не изменилось
    
    await callback.answer()


__all__ = ["router"]
