from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

__all__ = ["send_ephemeral_message", "send_ephemeral_from_callback"]


async def send_ephemeral_message(
    bot: Bot | CallbackQuery | Message,
    chat_id: int | None,
    text: str,
    *,
    user_id: int | None = None,
    show_alert: bool = False,
) -> None:
    """Send a short-lived notification without breaking legacy call sites."""

    # Compatibility: allow passing CallbackQuery directly
    if isinstance(bot, CallbackQuery):
        await send_ephemeral_from_callback(bot, text, show_alert=show_alert)
        return

    # Compatibility: allow passing Message directly
    if isinstance(bot, Message):
        await send_ephemeral_from_callback(bot, text, show_alert=show_alert)
        return

    if chat_id is None:
        return

    target_chat = user_id or chat_id
    try:
        await bot.send_message(target_chat, text)
    except TelegramBadRequest:
        pass


async def send_ephemeral_from_callback(
    callback: CallbackQuery | Message,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    """Convenience wrapper for callbacks/messages."""

    if isinstance(callback, CallbackQuery):
        try:
            await callback.answer(text, show_alert=show_alert)
        except TelegramBadRequest:
            pass
        return

    if isinstance(callback, Message):
        try:
            await callback.answer(text)
        except TelegramBadRequest:
            pass
