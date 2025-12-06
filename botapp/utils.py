from __future__ import annotations

from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

__all__ = ["send_ephemeral_message"]


async def send_ephemeral_message(
    callback: CallbackQuery | Message | Bot,
    text: str,
    show_alert: bool = False,
    *args,
    **kwargs,
) -> None:
    """Respond to a callback (or message) with a short-lived notification.

    Supports legacy call style ``send_ephemeral_message(bot, chat_id, text)`` by
    accepting ``Bot`` as the first argument and treating ``text`` as chat_id when
    a second positional argument is provided.
    """

    if isinstance(callback, CallbackQuery):
        try:
            await callback.answer(text, show_alert=show_alert)
        except TelegramBadRequest:
            pass
        return

    if isinstance(callback, Message):
        with suppress(Exception):
            await callback.answer(text)
        return

    if isinstance(callback, Bot) and args:
        chat_id = text
        body_text = args[0]
        with suppress(Exception):
            await callback.send_message(chat_id, body_text)
